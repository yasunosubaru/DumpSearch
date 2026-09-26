"""Windows 平台路径工具：长路径前缀、盘符枚举、卷类型判定。

设计上把"**显示路径**"与"**打开路径**"区分开：

* :func:`normalize` 产出的路径始终干净、可直接展示、也符合
  ``os.path`` 的常规语义（不含 ``\\\\?\\``）。
* :func:`native` 只在真正调用 ``open()`` / ``os.stat()`` 之前调用，
  负责补上 ``\\\\?\\`` 前缀以绕开 MAX_PATH 260 限制。
"""

from __future__ import annotations

import os
import string
import sys
from typing import Iterable, Optional

IS_WINDOWS = os.name == "nt"

# Windows 保留设备名（即使带扩展名也不允许作为文件名）
RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)

_UNC_PREFIX = "\\\\?\\UNC\\"
_EXT_PREFIX = "\\\\?\\"


def normalize(path: str) -> str:
    """把用户输入整理成规范化的显示路径。

    * 去掉已有 ``\\\\?\\`` 前缀
    * 展开环境变量（``%USERPROFILE%``）与 ``~``
    * 转成绝对路径
    * 去掉结尾多余的分隔符（根目录除外）
    """
    raw = (path or "").strip().strip('"')
    if not raw:
        raise ValueError("路径为空")
    if raw.startswith(_EXT_PREFIX):
        raw = raw[len(_EXT_PREFIX) :]
        if raw.startswith("UNC\\"):
            raw = "\\\\" + raw[4:]

    raw = os.path.expandvars(raw)
    raw = os.path.expanduser(raw)
    raw = raw.replace("/", "\\")
    raw = os.path.normpath(raw)

    if not IS_WINDOWS and "/" in raw:
        raw = os.path.normpath(os.path.abspath(raw))

    if len(raw) > 3 and raw.endswith("\\"):
        raw = raw.rstrip("\\")
    return raw


def display(path: str) -> str:
    """去掉长路径前缀，还原成**可展示、可比较**的常规路径。

    :func:`native` 的逆操作。GUI 显示、CSV 导出、日志、以及与"用户手输
    路径"做比较时都必须用这个结果——``\\\\?\\`` 前缀会禁用 Win32 的路径
    规范化（``..`` 不折叠、``/`` 不转换），混进业务数据里会引发难查的
    匹配失败。
    """
    if not path.startswith(_EXT_PREFIX):
        return path
    rest = path[len(_EXT_PREFIX) :]
    if rest.startswith("UNC\\"):
        return "\\\\" + rest[4:]
    return rest


def native(path: str) -> str:
    """返回适合交给 Win32 API 的路径（带长路径前缀）。"""
    if not IS_WINDOWS:
        return path
    if path.startswith(_EXT_PREFIX):
        return path
    if path.startswith("\\\\"):
        # \\server\share\... -> \\?\UNC\server\share\...
        return _UNC_PREFIX + path[2:].replace("/", "\\")
    absolute = os.path.abspath(path)
    if absolute.startswith(_EXT_PREFIX):
        return absolute
    return _EXT_PREFIX + absolute.replace("/", "\\")


def is_unc(path: str) -> bool:
    return path.startswith("\\\\")


def is_reserved_component(name: str) -> bool:
    stem = name.split(".", 1)[0].upper()
    return stem in RESERVED_NAMES


def has_alternate_data_stream(path: str) -> bool:
    """粗略判断路径里是否显式写了 NTFS 备用数据流（``file.txt:stream``）。"""
    base = path.rsplit("\\", 1)[-1]
    return ":" in base


# NTFS / Win32 文件属性位
FILE_ATTRIBUTE_READONLY = 0x00000001
FILE_ATTRIBUTE_HIDDEN = 0x00000002
FILE_ATTRIBUTE_SYSTEM = 0x00000004
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_SPARSE_FILE = 0x00000200
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_ATTRIBUTE_COMPRESSED = 0x00000800
FILE_ATTRIBUTE_OFFLINE = 0x00001000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000

# 会导致无限递归的重解析点 tag
TAG_MOUNT_POINT = 0xA0000003   # junction / 卷挂载点
TAG_SYMLINK = 0xA000000C

SKIP_DIR_REPARSE_TAGS = frozenset({TAG_MOUNT_POINT, TAG_SYMLINK})


def attrs_of(st) -> int:
    """从 stat 结果取 Windows 属性位（非 Windows 返回 0）。"""
    return int(getattr(st, "st_file_attributes", 0) or 0)


def is_reparse_point(entry: "os.DirEntry[str]") -> bool:
    """判断目录项是否为重解析点（junction / 符号链接 / 云占位符）。

    **必须看 ``FILE_ATTRIBUTE_REPARSE_POINT``，不能用 ``os.path.islink()``**：
    实测 ``islink()`` 对 junction（``IO_REPARSE_TAG_MOUNT_POINT``）返回
    ``False``，而 ``os.walk(followlinks=False)`` 又只按 islink 过滤，
    于是会跟着 junction 一路递归下去（构造自指 junction 实测 41 层仍未停）。
    """
    try:
        st = entry.stat(follow_symlinks=False)
    except (OSError, ValueError):
        try:
            return entry.is_symlink()
        except OSError:
            return False
    if not (attrs_of(st) & FILE_ATTRIBUTE_REPARSE_POINT):
        return False
    tag = int(getattr(st, "st_reparse_tag", 0) or 0)
    return tag in SKIP_DIR_REPARSE_TAGS or tag == 0


def is_cloud_placeholder(st) -> bool:
    """OneDrive 等云同步占位符：``st_size`` 是逻辑大小，但读它会触发下载。"""
    attrs = attrs_of(st)
    return bool(attrs & (FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
                         | FILE_ATTRIBUTE_RECALL_ON_OPEN
                         | FILE_ATTRIBUTE_OFFLINE))


def is_sparse_or_compressed(st) -> bool:
    return bool(attrs_of(st) & (FILE_ATTRIBUTE_SPARSE_FILE
                                | FILE_ATTRIBUTE_COMPRESSED))


# --------------------------------------------------------------------------
# 盘符 / 卷
# --------------------------------------------------------------------------

DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

DRIVE_LABELS = {
    DRIVE_REMOVABLE: "可移动盘",
    DRIVE_FIXED: "本地磁盘",
    DRIVE_REMOTE: "网络盘",
    DRIVE_CDROM: "光驱",
    DRIVE_RAMDISK: "内存盘",
}

# 这些目录在全盘扫描里几乎必然是无意义的噪声
DEFAULT_EXCLUDE_DIRS: tuple[str, ...] = (
    "$Recycle.Bin",
    "System Volume Information",
    "Windows",
    "Program Files",
    "Program Files (x86)",
    "ProgramData",
    "AppData",
    "node_modules",
    ".git",
    ".svn",
    ".hg",
    ".cache",
    ".venv",
    "venv",
    "__pycache__",
    ".Trash",
    ".Trashes",
    "$WinREAgent",
    "Recovery",
    "Config.Msi",
    "MSOCache",
    "Intel",
    "AMD",
    "PerfLogs",
    "Documents and Settings",
)


def _drive_type(root: str) -> int:
    if not IS_WINDOWS:
        return DRIVE_FIXED
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)))
    except Exception:  # pragma: no cover - 平台相关
        return DRIVE_UNKNOWN


def _volume_label(root: str) -> str:
    if not IS_WINDOWS:
        return ""
    import ctypes

    buf = ctypes.create_unicode_buffer(261)
    fsbuf = ctypes.create_unicode_buffer(261)
    try:
        ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root),
            buf,
            ctypes.sizeof(buf) // 2,
            None,
            None,
            None,
            fsbuf,
            ctypes.sizeof(fsbuf) // 2,
        )
    except Exception:  # pragma: no cover
        return ""
    return buf.value or ""


def _space(root: str) -> Optional[tuple[int, int]]:
    """返回 ``(可用字节, 总字节)``；查询失败返回 ``None``。

    失败不是"0 字节可用"——已挂载的 VHD 字母盘、未就绪的网络盘都会让
    ``GetDiskFreeSpaceExW`` 失败但路径存在，必须据此把盘符过滤掉，
    否则扫描时才发现整盘读不了。
    """
    if not IS_WINDOWS:  # pragma: no cover
        try:
            usage = os.statvfs(root)
            return (
                int(usage.f_bavail * usage.f_frsize),
                int(usage.f_blocks * usage.f_frsize),
            )
        except (OSError, AttributeError):
            return None
    import ctypes

    free_avail = ctypes.c_ulonglong(0)
    total_all = ctypes.c_ulonglong(0)
    ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
        ctypes.c_wchar_p(root),
        ctypes.byref(free_avail),
        ctypes.byref(total_all),
        None,
    )
    if not ok:
        return None
    return int(free_avail.value), int(total_all.value)


class DriveInfo:
    """一个可扫描的卷。"""

    __slots__ = ("root", "label", "drive_type", "free_bytes", "total_bytes")

    def __init__(self, root: str, label: str, drive_type: int,
                 free_bytes: int, total_bytes: int) -> None:
        self.root = root
        self.label = label
        self.drive_type = drive_type
        self.free_bytes = free_bytes
        self.total_bytes = total_bytes

    @property
    def name(self) -> str:
        return f"{self.root} {self.label}".strip()

    @property
    def type_name(self) -> str:
        return DRIVE_LABELS.get(self.drive_type, "未知")

    @property
    def is_network(self) -> bool:
        return self.drive_type == DRIVE_REMOTE

    def __repr__(self) -> str:  # pragma: no cover
        return f"DriveInfo({self.root!r}, {self.label!r}, {self.type_name!r})"


def list_drives(
    include_network: bool = True, include_cdrom: bool = False
) -> list[DriveInfo]:
    """枚举本机可用的盘符。

    Windows 上优先用 ``GetLogicalDrives`` 拿到真实存在的盘（含未赋字母的
    挂载点可由调用方自行追加），并过滤掉光驱 / 无根目录的卷。
    """
    roots: list[str] = []

    if IS_WINDOWS:
        try:
            import ctypes

            mask = ctypes.windll.kernel32.GetLogicalDrives()
            for i, letter in enumerate(string.ascii_uppercase):
                if mask & (1 << i):
                    roots.append(f"{letter}:\\")
        except Exception:  # pragma: no cover
            roots = [f"{letter}:\\" for letter in string.ascii_uppercase]
    else:  # pragma: no cover - 非 Windows 平台
        roots = ["/"]

    result: list[DriveInfo] = []
    for root in roots:
        if not os.path.exists(root):
            continue
        dtype = _drive_type(root)
        if dtype in (DRIVE_NO_ROOT_DIR, DRIVE_UNKNOWN):
            continue
        if dtype == DRIVE_CDROM and not include_cdrom:
            continue
        if dtype == DRIVE_REMOTE and not include_network:
            continue
        label = _volume_label(root)
        space = _space(root)
        if space is None:
            # 盘符存在但卷不可用（未挂载的 VHD、掉线的网络盘）
            continue
        free, total = space
        result.append(DriveInfo(root, label, dtype, free, total))
    return result


def is_subpath(child: str, parent: str) -> bool:
    """``child`` 是否在 ``parent`` 之内（大小写不敏感，Windows 语义）。"""
    c = os.path.normcase(os.path.abspath(child))
    p = os.path.normcase(os.path.abspath(parent))
    if c == p:
        return True
    return c.startswith(p.rstrip("\\") + "\\")


def unique_paths(paths: Iterable[str]) -> list[str]:
    """去重并保持顺序（大小写不敏感）。"""
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


if sys.platform == "win32":  # pragma: no cover
    try:
        import ctypes.wintypes  # noqa: F401
    except Exception:
        pass


def describe_os() -> str:
    return f"{sys.platform} python{sys.version.split()[0]}"


# --------------------------------------------------------------------------
# 容错 I/O：先走"常规路径"，仅当失败且路径确实很长时才退回 \\?\ 前缀
# --------------------------------------------------------------------------
#
# 为什么要这么做（实测结论，2026-09）：
#
# * CPython 自 3.6 起的 ``python.exe`` manifest 声明了 ``longPathAware``，
#   因此在 Windows 10+ 上**不带前缀**的 260+ 字符路径可以被
#   ``open`` / ``os.stat`` / ``os.scandir`` 正常处理（实测 742 字符 OK）。
# * 但 ``shell32.SHFileOperationW``（回收站 API）是遗留 shell 接口，
#   对 ``\\?\`` 前缀处理有缺陷：单条调用会返回 DE_IOERR，批量调用直接
#   DE_FILELOCKED(124)，尽管文件其实已被删掉。
#
# 所以统一约定：**业务代码永远传常规路径**，需要长路径兼容时由本模块
# 在失败后自动降级，而不是让 ``\\?\\`` 泄漏到数据层。
LONG_PATH_THRESHOLD = 250


def _fallback(path: str) -> Optional[str]:
    """路径够长时返回带前缀的备选，否则返回 ``None``。"""
    if len(path) < LONG_PATH_THRESHOLD:
        return None
    prefixed = native(path)
    return prefixed if prefixed != path else None


def open_binary(path: str, mode: str = "rb", **kwargs):
    """打开文件，失败时对超长路径自动降级重试。"""
    try:
        return open(path, mode, **kwargs)
    except OSError:
        alt = _fallback(path)
        if alt is None:
            raise
        return open(alt, mode, **kwargs)


def stat_path(path: str, follow_symlinks: bool = True):
    return os.stat(path, follow_symlinks=follow_symlinks)


def scandir_safe(path: str):
    """返回 ``os.scandir`` 上下文管理器，失败时对超长路径降级重试。"""
    try:
        return os.scandir(path)
    except OSError:
        alt = _fallback(path)
        if alt is None:
            raise
        return os.scandir(alt)


def remove_safe(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        alt = _fallback(path)
        if alt is None:
            raise
        os.remove(alt)


def lexists_safe(path: str) -> bool:
    try:
        return os.path.lexists(path)
    except (OSError, ValueError):
        return False


def getsize_safe(path: str, default: int = 0) -> int:
    try:
        return os.stat(path).st_size
    except OSError:
        return default
