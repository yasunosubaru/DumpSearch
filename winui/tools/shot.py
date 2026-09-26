"""截取指定进程的主窗口（用于验证 WinUI 3 界面渲染效果）。

用法::

    python tools\\shot.py <进程名或exe路径> <输出png> [等待秒数] [传给程序的参数...]

会先把窗口提到前台再抓图，否则截到的是别的窗口。
"""

import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import time

D = chr(36)


def user32():
    return ctypes.windll.user32


def find_window_by_pid(pid: int) -> int:
    u = user32()
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _lparam):
        owner = ctypes.c_ulong()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and u.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    u.EnumWindows(cb, 0)
    return found[0] if found else 0


def capture(hwnd: int, out: str) -> bool:
    """用 PrintWindow 直接抓窗口自身内容。

    不用屏幕区域抓取：桌面上总有别的置顶窗口（agent UI 等），屏幕区域
    抓到的会是别人的内容。PrintWindow + PW_RENDERFULLCONTENT 即使窗口
    被完全遮挡也能拿到自己的渲染结果。
    """
    try:
        import PIL.Image  # type: ignore
    except Exception as exc:
        print(f"需要 Pillow: {exc}")
        return False

    u = user32()
    gdi32 = ctypes.windll.gdi32

    rect = ctypes.wintypes.RECT()
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    ok = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        ctypes.c_void_p(hwnd), ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
        ctypes.byref(rect), ctypes.sizeof(rect))
    if ok != 0:
        u.GetWindowRect(hwnd, ctypes.byref(rect))

    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        print("窗口尺寸无效")
        return False

    hdc = u.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        # 2 = PW_RENDERFULLCONTENT
        if not u.PrintWindow(hwnd, mdc, 2):
            if not u.PrintWindow(hwnd, mdc, 0):
                print("PrintWindow 失败")
                return False

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32),
            ]

        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height          # 负值 = 自上而下
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0           # BI_RGB

        stride = width * 4
        buf = ctypes.create_string_buffer(stride * height)
        got = gdi32.GetDIBits(mdc, bmp, 0, height, buf, ctypes.byref(header), 0)
        if not got:
            print("GetDIBits 失败")
            return False

        img = PIL.Image.frombuffer("RGBA", (width, height), buf, "raw",
                                   "BGRA", 0, 1).convert("RGB")
        img.save(out)
        print(f"已保存 {out} ({img.width}x{img.height})")
        return True
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        u.ReleaseDC(hwnd, hdc)


def main() -> int:
    HWND_NOTOPMOST = -2
    SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0002, 0x0001, 0x0040
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    target, out = sys.argv[1], sys.argv[2]
    wait = float(sys.argv[3]) if len(sys.argv) > 3 else 8.0
    # 第 4 个及以后的参数原样转给目标程序（--autoscan <目录> 之类）
    extra = sys.argv[4:]
    out = os.path.abspath(out)

    proc = subprocess.Popen([target, *extra])
    try:
        time.sleep(wait)
        if proc.poll() is not None:
            print(f"进程已退出，退出码 {proc.returncode}")
            return 1
        hwnd = find_window_by_pid(proc.pid)
        if not hwnd:
            print("找不到可见窗口")
            return 1

        # 只做温和的置前尝试：真正抓图靠 PrintWindow，不依赖前台顺序
        u = user32()
        u.ShowWindow(hwnd, 9)              # SW_RESTORE
        u.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                       SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        u.BringWindowToTop(hwnd)
        time.sleep(1.0)
        return 0 if capture(hwnd, out) else 1
    finally:
        time.sleep(0.3)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
