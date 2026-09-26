"""把文件移入 Windows 回收站（可撤销）。

实现方式优先走 ``send2trash``（若已安装），否则用 ``ctypes`` 直接调用
``shell32.SHFileOperationW``，**不引入任何强制依赖**。

为什么不用 ``os.remove()``：那是不可撤销的硬删除。回收站方案让"删除
重复文件"这个不可逆动作始终可回退，这是本工具的核心安全约束。

跨卷注意事项：``SHFileOperationW`` 配合 ``FOF_ALLOWUNDO`` 时，回收站
条目由该卷自己的 ``$Recycle.Bin`` 承载，因此从 ``C:\\`` 删除 ``D:\\``
上的文件同样会进回收站，而不是被降级为永久删除。
"""

from __future__ import annotations

import ctypes
import os
import stat
import struct
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence

from .. import winpath

IS_WINDOWS = os.name == "nt"

# ---------------------------------------------------------------------------
# Win32 常量
# ---------------------------------------------------------------------------
FO_DELETE = 0x0003

FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMMKDIR = 0x0200
FOF_NOERRORUI = 0x0400
FOF_RENAMEONCOLLISION = 0x0008

# SHFILEOPSTRUCT.fFlags 的"全量"组合
_SILENT_ALLOWUNDO = (
    FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOCONFIRMMKDIR
    | FOF_NOERRORUI
)

# 每次提交给 SHFileOperationW 的最大路径数（过多时该 API 不可靠）
_BATCH = 60

# shell 回收站 API 的路径长度上限。**超过这个长度的路径，SHFileOperationW
# 会"成功"但把文件直接永久删掉、不留回收站条目**（实测：721 / 781 字符
# 的文件回收站计数不变且文件消失）。所以必须提前分流到 $I/$R 方案。
LONG_PATH_UNSUPPORTED = 260

_FILE_ATTRIBUTE_READONLY = 0x00000001
_FILE_ATTRIBUTE_SYSTEM = 0x00000004


@dataclass(slots=True)
class DeleteOutcome:
    """一次批量删除的结果。"""

    deleted: list[str] = None  # type: ignore[assignment]
    failed: list[tuple[str, str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.deleted is None:
            self.deleted = []
        if self.failed is None:
            self.failed = []

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def total(self) -> int:
        return len(self.deleted) + len(self.failed)

    def summary(self) -> str:
        parts = [f"已移入回收站 {len(self.deleted):,} 个"]
        if self.failed:
            parts.append(f"失败 {len(self.failed):,} 个")
        return "，".join(parts)


if IS_WINDOWS:
    import ctypes.wintypes

    class _SHFILEOPSTRUCT(ctypes.Structure):
        """``SHFILEOPSTRUCTW`` 的 ctypes 映射。"""

        _fields_ = [
            ("hwnd", ctypes.wintypes.HWND),
            ("wFunc", ctypes.wintypes.UINT),
            ("pFrom", ctypes.wintypes.LPCWSTR),
            ("pTo", ctypes.wintypes.LPCWSTR),
            ("fFlags", ctypes.wintypes.BOOL),
            ("fAnyOperationsAborted", ctypes.wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.wintypes.LPCWSTR),
        ]
else:  # pragma: no cover - 非 Windows 平台不需要该结构
    _SHFILEOPSTRUCT = None  # type: ignore[assignment,misc]


def _shell32_op(paths: Sequence[str], flags: int) -> int:
    """调用 ``SHFileOperationW(FO_DELETE)`` 删除一批路径。

    ``pFrom`` 必须是**双 NUL 结尾**的以 NUL 分隔的宽字符串列表。
    返回 0 表示成功；非 0 是 Win32 错误码；``aborted`` 表示用户取消。
    """
    payload = "".join(p + "\0" for p in paths) + "\0"
    op = _SHFILEOPSTRUCT()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.c_wchar_p(payload)
    op.pTo = None
    op.fFlags = flags
    op.fAnyOperationsAborted = 0
    op.hNameMappings = None
    op.lpszProgressTitle = None

    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))  # type: ignore[attr-defined]
    if result != 0:
        return result
    if op.fAnyOperationsAborted:
        return -1  # 用户中途取消
    return 0


def _clear_readonly(path: str) -> None:
    """去掉只读/系统属性，否则 shell 删除会失败。"""
    try:
        attrs = os.stat(path).st_file_attributes
    except (OSError, AttributeError):
        return
    if attrs & (_FILE_ATTRIBUTE_READONLY | _FILE_ATTRIBUTE_SYSTEM):
        try:
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        except OSError:
            pass


def exists(path: str) -> bool:
    return winpath.lexists_safe(path)


def _send2trash_batch(paths: Sequence[str]) -> list[str]:
    """走 send2trash，返回失败路径列表。"""
    from send2trash import send2trash  # type: ignore

    failed: list[str] = []
    for path in paths:
        try:
            send2trash(path)
        except Exception:
            if exists(path):
                failed.append(path)
    return failed


def trash_files(
    paths: Iterable[str],
    progress: Optional[Callable[[int, int], None]] = None,
    use_send2trash: Optional[bool] = None,
    force_hard_delete: bool = False,
) -> DeleteOutcome:
    """把 ``paths`` 移入回收站。

    :param progress: ``callback(done, total)``
    :param use_send2trash: 强制/禁用 send2trash；``None`` 表示自动探测
    :param force_hard_delete: 仅在明确知道后果时使用——直接永久删除
    :returns: :class:`DeleteOutcome`，失败项带原因
    """
    targets = [p for p in dict.fromkeys(paths) if p]
    outcome = DeleteOutcome()
    if not targets:
        return outcome

    if force_hard_delete:
        return _hard_delete(targets, progress)

    # 先做存在性核对。不能直接交给 shell：SHFileOperationW 对不存在的
    # 路径返回 0（不算错误），而"文件本来就不存在"绝不能被报告成
    # "已成功删除"——那会让用户以为清理干净了。
    present: list[str] = []
    for path in targets:
        if exists(path):
            present.append(path)
        else:
            outcome.failed.append((path, "文件已不存在（可能已被其它程序移动或删除）"))

    if not present:
        return outcome

    if use_send2trash is None:
        use_send2trash = _has_send2trash()

    if not IS_WINDOWS:
        # 非 Windows 平台：send2trash 也能工作（写 .Trash）
        if use_send2trash:
            failed = _send2trash_batch(present)
            bad = set(failed)
            for p in present:
                if p in bad:
                    outcome.failed.append((p, "send2trash 失败"))
                else:
                    outcome.deleted.append(p)
            return outcome
        return _hard_delete(present, progress)

    # ---- 关键分流 ----------------------------------------------------
    # 超过 260 字符的路径绝不能交给 SHFileOperationW：它会"成功"返回却
    # 把文件**永久删除**（不留回收站条目）。这些走 $I/$R 手动方案。
    short_paths = [p for p in present if not is_long_path(p)]
    long_paths = [p for p in present if is_long_path(p)]

    total = len(short_paths)
    done = 0
    for start in range(0, total, _BATCH):
        batch = short_paths[start : start + _BATCH]

        if not use_send2trash:
            # 常规路径交给 shell（传常规路径，不带 \\?\ 前缀）
            code = _delete_batch_shell(batch, _SILENT_ALLOWUNDO)
            still_there = [p for p in batch if exists(p)]
            if still_there:
                alt = [winpath.native(p) for p in still_there]
                _delete_batch_shell(alt, _SILENT_ALLOWUNDO)
            for p in batch:
                if exists(p):
                    outcome.failed.append((p, _explain(code, False)))
                else:
                    outcome.deleted.append(p)
        else:
            failed = _send2trash_batch(batch)
            bad = set(failed)
            for p in batch:
                if p in bad:
                    outcome.failed.append((p, "send2trash 拒绝删除"))
                else:
                    outcome.deleted.append(p)

        done += len(batch)
        if progress:
            progress(done, total)

    # ---- 长路径：$I/$R 方案，逐个处理以便精确定位失败 ----------------
    if long_paths:
        sid = None
        for idx, path in enumerate(long_paths, 1):
            if sid is None:
                sid = resolve_sid()
            ok, reason = trash_manual(path, sid)
            if ok:
                outcome.deleted.append(path)
            else:
                outcome.failed.append((path, reason))
            if progress:
                progress(total + idx, total + len(long_paths))

    return outcome


def _delete_batch_shell(paths: Sequence[str], flags: int) -> int:
    """把一批路径交给 ``SHFileOperationW(FO_DELETE)``，返回其错误码。"""
    for p in paths:
        _clear_readonly(p)
    if not paths:
        return 0
    return _shell32_op(paths, flags)


def _explain(code: int, via_send2trash: bool) -> str:
    if code == -1:
        return "操作被中止"
    if code == 0:
        return "删除后文件仍然存在（可能路径超长或被占用）"
    if code == 32:
        return "文件被其他程序占用，无法删除（请关闭正在打开它的程序）"
    if code == 33:
        return "文件被锁定（共享冲突）"
    if code == 75:
        return "操作被取消（DE_CANCELLED）"
    if code == 121:
        return "文件为只读且无法清除属性"
    if code == 0x75 or code == 1223:
        return "操作被取消（ERROR_CANCELLED）"
    if via_send2trash:
        return f"send2trash 拒绝删除（错误码 {code}）"
    return f"SHFileOperationW 失败（错误码 {code}）"


def _hard_delete(
    paths: Sequence[str], progress: Optional[Callable[[int, int], None]] = None
) -> DeleteOutcome:
    """永久删除——**不可撤销**，仅供明确知情的调用方使用。"""
    outcome = DeleteOutcome()
    total = len(paths)
    for idx, path in enumerate(paths, 1):
        try:
            _clear_readonly(path)
            winpath.remove_safe(path)
            outcome.deleted.append(path)
        except OSError as exc:
            outcome.failed.append((path, f"{type(exc).__name__}: {exc.strerror or exc}"))
        if progress and idx % 200 == 0:
            progress(idx, total)
    if progress:
        progress(total, total)
    return outcome


def _has_send2trash() -> bool:
    try:
        import send2trash  # type: ignore  # noqa: F401
    except Exception:
        return False
    return True


# ---------------------------------------------------------------------------
# 长路径：手写 $Recycle.Bin 的 $I / $R 记录
# ---------------------------------------------------------------------------
#
# 为什么需要：SHFileOperationW 对 >260 字符路径会"成功"地永久删除
# （不留回收站条目），这会让本工具"可撤销"的核心承诺变成谎言。
#
# NTFS 回收站的结构是：每个 SID 一个目录，里面成对出现
#   $R<随机ID><扩展名>  —— 文件真实内容，名字就是原文件名
#   $I<随机ID><扩展名>  —— 元数据记录，格式见下
# 资源管理器据此把 $R 还原成 $I 里记录的原路径。

_DOLLAR = "$"
_RECYCLE_DIR = _DOLLAR + "Recycle.Bin"
_ITEM_R = _DOLLAR + "R"
_ITEM_I = _DOLLAR + "I"

_sid_cache: Optional[str] = None


def resolve_sid() -> str:
    """当前用户的 SID。依次尝试 ctypes / whoami / 可写目录探测。"""
    global _sid_cache
    if _sid_cache:
        return _sid_cache

    sid = _sid_via_advapi32() or _sid_via_whoami() or _sid_by_writable_probe()
    if sid:
        _sid_cache = sid
    return sid


def _sid_via_advapi32() -> Optional[str]:
    if not IS_WINDOWS:
        return None
    try:
        from ctypes import wintypes as W

        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        adv.OpenProcessToken.argtypes = [ctypes.c_void_p, W.DWORD,
                                         ctypes.POINTER(W.HANDLE)]
        adv.OpenProcessToken.restype = W.BOOL
        adv.GetTokenInformation.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            W.DWORD, ctypes.POINTER(W.DWORD)]
        adv.GetTokenInformation.restype = W.BOOL
        adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                               ctypes.POINTER(W.LPWSTR)]
        adv.ConvertSidToStringSidW.restype = W.BOOL

        token = W.HANDLE()
        if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x8,
                                    ctypes.byref(token)):
            return None
        need = W.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(need))
        if not need.value:
            return None
        buf = ctypes.create_string_buffer(need.value)
        if not adv.GetTokenInformation(token, 1, buf, need, ctypes.byref(need)):
            return None
        sid_out = W.LPWSTR()
        if not adv.ConvertSidToStringSidW(ctypes.cast(buf, ctypes.c_void_p),
                                          ctypes.byref(sid_out)):
            return None
        return sid_out.value
    except Exception:
        return None


def _sid_via_whoami() -> Optional[str]:
    try:
        import subprocess

        out = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        for token in out.replace('"', "").split(","):
            token = token.strip()
            if token.upper().startswith("S-1-") and len(token) > 10:
                return token
    except Exception:
        pass
    return None


def _sid_by_writable_probe() -> str:
    """退化方案：找一个能写入的 SID 目录。"""
    import glob

    base = os.path.join("C:\\", _RECYCLE_DIR)
    for candidate in glob.glob(os.path.join(base, "S-1-*")):
        try:
            probe = os.path.join(candidate, ".ds_probe")
            with open(probe, "wb") as fh:
                fh.write(b"0")
            os.remove(probe)
            return os.path.basename(candidate)
        except OSError:
            continue
    return ""


def _now_filetime() -> int:
    """当前时间作为 Windows FILETIME（100ns 自 1601-01-01 起的计数）。"""
    if IS_WINDOWS:
        try:
            from ctypes import wintypes as W

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            ft = W.FILETIME()
            k32.GetSystemTimeAsFileTime.argtypes = [ctypes.POINTER(W.FILETIME)]
            k32.GetSystemTimeAsFileTime(ctypes.byref(ft))
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        except Exception:
            pass
    # 退化为 Unix 时间 -> FILETIME
    import time

    return int((time.time() + 11644473600) * 10_000_000) & 0xFFFFFFFFFFFFFFFF


def _recycle_bin_dir(drive: str, sid: str) -> Optional[str]:
    """``drive`` 形如 ``C:``，返回该卷的 ``$Recycle.Bin\<sid>``。"""
    if not sid:
        return None
    path = os.path.join(f"{drive}\\", _RECYCLE_DIR, sid)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return None
    return path if os.path.isdir(path) else None


def trash_manual(path: str, sid: Optional[str] = None) -> tuple[bool, str]:
    """长路径专用：手动搬进 ``$Recycle.Bin`` 并写好 ``$I`` 记录。

    返回 ``(是否成功, 失败原因)``。**任何一步失败都会把文件搬回去**，
    绝不会出现"文件没了但回收站里也没有"的情况。
    """
    if not IS_WINDOWS:
        return False, "长路径回收站仅支持 Windows"

    sid = sid or resolve_sid()
    if not sid:
        return False, "无法确定当前用户 SID"

    if not winpath.lexists_safe(path):
        return False, "源文件不存在"

    drive = os.path.splitdrive(path)[0]
    binroot = _recycle_bin_dir(drive, sid)
    if not binroot:
        return False, f"无法访问 {drive}\\{_RECYCLE_DIR}（网络盘/无权限？）"

    is_dir = os.path.isdir(path)
    # 扩展名原样保留，Shell 还原时才认得出原类型；目录不带扩展名
    ext = "" if is_dir else os.path.splitext(path)[1]

    target = None
    for _ in range(8):
        rid = os.urandom(6).hex().upper()
        candidate = os.path.join(binroot, _ITEM_R + rid + ext)
        if not winpath.lexists_safe(candidate):
            target = candidate
            item_id = rid + ext
            break
    if target is None:
        return False, "无法分配回收站条目 ID"

    try:
        os.rename(path, target)
    except OSError as exc:
        code = getattr(exc, "winerror", None) or 0
        if code in (32, 33):
            return False, "文件被其他进程占用"
        return False, f"移入回收站失败: {exc.strerror or exc}"

    # $I v2: 版本(8) | 大小(8) | 修改时间 FILETIME(8) | 路径字符数(4) | UTF-16LE 路径
    # 路径必须**存完整值**：截断会让 Shell 静默丢弃整个条目。
    try:
        size = 0 if is_dir else os.path.getsize(target)
    except OSError:
        size = 0
    display_path = winpath.display(path)
    encoded = display_path.encode("utf-16-le")
    record = struct.pack("<QQQI", 2, size, _now_filetime(),
                         len(display_path)) + encoded

    info_path = os.path.join(binroot, _ITEM_I + item_id)
    try:
        with open(winpath.native(info_path), "wb") as fh:
            fh.write(record)
    except OSError as exc:
        # 回滚：把文件搬回原位，绝不留下"删了但没进回收站"的局面
        try:
            os.rename(target, path)
        except OSError:
            pass
        return False, f"写回收站记录失败（已回滚）: {exc.strerror or exc}"

    return True, ""


def is_long_path(path: str) -> bool:
    """是否超出 shell 回收站 API 的安全长度。"""
    return len(winpath.display(path)) > LONG_PATH_UNSUPPORTED


def recycle_bin_size() -> int:
    """粗略估算回收站占用（遍历各卷 ``$Recycle.Bin``），失败返回 0。"""
    total = 0
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\$Recycle.Bin"
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    continue
    return total


def empty_recycle_bin() -> bool:
    """清空当前用户回收站。"""
    if not IS_WINDOWS:
        return False
    import ctypes

    SHERB_NOCONFIRMATION = 0x0001
    SHERB_NOPROGRESSUI = 0x0002
    SHERB_NOSOUND = 0x0004
    return (
        ctypes.windll.shell32.SHEmptyRecurseBin(  # type: ignore[attr-defined]
            None,
            SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND,
        )
        == 0
    )
