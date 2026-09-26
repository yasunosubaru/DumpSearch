"""验证：SHFileOperationW 对长路径到底是"进回收站"还是"静默永久删除"。

这是整个工具最关键的安全问题——如果长路径文件被静默永久删除，
那"可撤销的清理"这个核心承诺就是假的。

方法：直接数当前用户回收站目录里 $R 条目的数量与字节数，绕开
SHQueryRecycleBin 可能有缓存/聚合的问题。
"""

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dupehunter.ops import trash  # noqa: E402

DOLLAR = chr(36)  # '$'，避免被 shell 吞掉
RECYCLE_DIR = DOLLAR + "Recycle.Bin"
ITEM_PREFIX = DOLLAR + "R"


def current_sid():
    """取当前交互用户的 SID。失败返回 None。"""
    try:
        import ctypes
        from ctypes import wintypes as W
    except Exception:
        return None
    try:
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = W.HANDLE
        adv.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD,
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
        buf = ctypes.create_string_buffer(need.value)
        if not adv.GetTokenInformation(token, 1, buf, need, ctypes.byref(need)):
            return None
        sid = W.LPWSTR()
        if not adv.ConvertSidToStringSidW(ctypes.cast(buf, ctypes.c_void_p),
                                          ctypes.byref(sid)):
            return None
        return sid.value
    except Exception:
        return None


def find_bin():
    """定位当前用户的回收站目录；退化时把所有 SID 目录合起来数。"""
    cands = glob.glob(os.path.join("C:\\", RECYCLE_DIR, "S-1-*"))
    sid = current_sid()
    if sid:
        for c in cands:
            if os.path.basename(c).upper() == sid.upper():
                return c
    # 退化路径：返回全部，count() 会把它们都算上
    return cands or None


def count(binroot):
    roots = [binroot] if isinstance(binroot, str) else list(binroot or [])
    total_files = 0
    total_bytes = 0
    for r in roots:
        if not os.path.isdir(r):
            continue
        files = [f for f in glob.glob(os.path.join(r, ITEM_PREFIX + "*"))
                 if os.path.isfile(f)]
        total_files += len(files)
        for f in files:
            try:
                total_bytes += os.path.getsize(f)
            except OSError:
                pass
    return total_files, total_bytes


def main():
    binroot = find_bin()
    print("当前用户 SID:", current_sid())
    print("回收站目录:", binroot)

    # TEMP 兜底用 %SystemRoot%\Temp：默认值里**不要**写带用户名的路径，
    # 那等于把本机用户名写进公开仓库的源码里。
    root = os.path.join(
        os.environ.get("TEMP")
        or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp"),
        "lp_verify")
    deep = root
    for _ in range(14):
        deep = os.path.join(deep, "a_very_long_directory_name_for_path_length_test")
    os.makedirs(deep, exist_ok=True)

    cases = []
    # 短路径
    p_short = os.path.join(root, "S" + "q" * 20 + ".txt")
    with open(p_short, "wb") as f:
        f.write(b"SHORTDATA" * 10)
    cases.append(("短路径", p_short))

    # 刚好在 260 附近
    p_edge = os.path.join(deep, "E.txt")
    os.makedirs(os.path.dirname(p_edge), exist_ok=True)
    cases.append(("长路径-空", p_edge))

    # 明确 >260
    p_long = os.path.join(deep, "L" + "x" * 60 + ".txt")
    with open(p_long, "wb") as f:
        f.write(b"LONGDATA" * 100)
    cases.append(("长路径-789", p_long))

    print()
    print("%-12s %-7s %-9s %-9s %s" % ("用例", "字符数", "回收站前", "回收站后", "结果"))
    print("-" * 72)

    verdict = {}
    for label, path in cases:
        with open(path, "wb") as f:
            f.write(b"PAYLOAD-" + label.encode("ascii", "replace") + b"-" * 50)
        n0, b0 = count(binroot)
        out = trash.trash_files([path], use_send2trash=False)
        n1, b1 = count(binroot)
        gone = not os.path.exists(path)
        recycled = (n1 > n0)
        if not gone:
            status = "未删除(%s)" % out.failed
        elif recycled:
            status = "OK 进回收站"
        else:
            status = "*** 静默永久删除 ***"
        verdict[label] = (len(path), status)
        print("%-12s %-7d %-9d %-9d %s"
              % (label, len(path), n0, n1, status))
        if out.failed:
            print("             失败原因:", out.failed)

    print()
    bad = [k for k, (_l, s) in verdict.items() if "永久删除" in s]
    if bad:
        print("!!! 以下用例被静默永久删除，必须改用 $I/$R 手动回收站方案:")
        for k in bad:
            print("   -", k, verdict[k][0], "字符")
        return 1
    print("全部用例均正确进入回收站")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
