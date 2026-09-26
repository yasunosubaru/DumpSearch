"""把文件 / 文件夹交给系统默认程序打开，并在资源管理器中定位。"""

from __future__ import annotations

import os
import subprocess
import sys

IS_WINDOWS = os.name == "nt"


def open_file(path: str) -> bool:
    """用系统默认关联程序打开文件。"""
    try:
        if IS_WINDOWS:
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except (OSError, ValueError):
        return False


def open_folder(path: str) -> bool:
    """打开所在目录。"""
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    if not folder:
        return False
    return open_file(folder)


def reveal_in_explorer(path: str) -> bool:
    """在资源管理器中选中并高亮该文件。"""
    if not IS_WINDOWS:
        return open_folder(path)
    try:
        # /select, 后面必须紧跟带引号的路径，且整体作为一个参数传入
        subprocess.Popen(["explorer.exe", f"/select,{path}"])
        return True
    except (OSError, ValueError):
        return False


def copy_to_clipboard(text: str) -> bool:
    """把文本放进剪贴板（Tk 之外也能用）。"""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]

        data = text + "\0"
        size = len(data) * ctypes.sizeof(ctypes.c_wchar)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False
        locked = kernel32.GlobalLock(handle)
        ctypes.memmove(locked, ctypes.create_unicode_buffer(data), size)
        kernel32.GlobalUnlock(handle)
        user32.SetClipboardData(CF_UNICODETEXT, handle)
        return True
    except Exception:
        return False


def shell_quote(path: str) -> str:
    """给命令行用的引号包裹。"""
    return '"' + path.replace('"', '""') + '"'


def temp_report_path(suffix: str = ".csv") -> str:
    import tempfile

    fd, path = tempfile.mkstemp(suffix=suffix, prefix="dumpsearch_")
    os.close(fd)
    return path
