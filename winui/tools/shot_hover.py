"""把鼠标停到结果行上，验证悬停详情浮层真的会出现。

为什么不用 shot.py：ToolTip 在 WinUI 里是**独立的弹出窗口**，
PrintWindow 只抓主窗口自己的重定向表面，浮层根本不在里面。
必须真的移动光标触发，再抓**屏幕**区域。

用法::

    python tools\\shot_hover.py <输出png> [扫描目录] [行号]
"""

import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import time

D = chr(36)
HERE = os.path.dirname(os.path.abspath(__file__))


def user32():
    return ctypes.windll.user32


def find_windows_by_pid(pid):
    """列出该进程所有可见顶层窗口。悬停浮层是独立窗口，会一并被列出来。"""
    u = user32()
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _l):
        owner = ctypes.c_ulong()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and u.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    u.EnumWindows(cb, 0)
    return found


def find_window_by_pid(pid):
    ws = find_windows_by_pid(pid)
    return ws[0] if ws else 0


def grab_window(hwnd, out):
    """PrintWindow 抓单个窗口（主窗口或浮层都行）。"""
    import ctypes.wintypes
    from PIL import Image

    u = user32()
    gdi = ctypes.windll.gdi32
    rect = ctypes.wintypes.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.c_void_p(hwnd), ctypes.c_uint(9),
            ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        u.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None

    hdc = u.GetWindowDC(hwnd)
    mdc = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    old = gdi.SelectObject(mdc, bmp)
    try:
        if not u.PrintWindow(hwnd, mdc, 2) and not u.PrintWindow(hwnd, mdc, 0):
            return None

        class BIH(ctypes.Structure):
            _fields_ = [
                ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32),
            ]

        h2 = BIH()
        h2.biSize = ctypes.sizeof(BIH)
        h2.biWidth = w
        h2.biHeight = -h
        h2.biPlanes = 1
        h2.biBitCount = 32
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(h2), 0):
            return None
        img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
        bg = Image.new("RGB", (w, h), (32, 32, 38))
        bg.paste(img, mask=img.split()[3])
        bg.save(out)
        return bg.size
    finally:
        gdi.SelectObject(mdc, old)
        gdi.DeleteObject(bmp)
        gdi.DeleteDC(mdc)
        u.ReleaseDC(hwnd, hdc)


def grab_screen(x, y, w, h, out):
    from PIL import ImageGrab
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    img.save(out)
    return img.size


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    out = os.path.abspath(sys.argv[1])
    target = sys.argv[2] if len(sys.argv) > 2 else \
        os.path.join(os.environ["TEMP"], "opencode", "dupecsharp")
    # 第几行文件（结果表里从上往下数，含组行）
    row_index = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    exe = os.path.abspath(os.path.join(
        HERE, "..", "src", "DumpSearch.App", "bin", "Release",
        "net8.0-windows10.0.19041.0", "DumpSearch.exe"))
    if not os.path.exists(exe):
        print(f"找不到 exe: {exe}")
        return 2

    proc = subprocess.Popen([exe, "--autoscan", target])
    try:
        time.sleep(11)
        hwnd = find_window_by_pid(proc.pid)
        if not hwnd:
            print("找不到窗口")
            return 1

        u = user32()
        HWND_TOPMOST = -1
        SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0002, 0x0001, 0x0040
        u.ShowWindow(hwnd, 9)
        u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                       SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
        time.sleep(1.2)

        rect = ctypes.wintypes.RECT()
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.c_void_p(hwnd), ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect), ctypes.sizeof(rect))
        L, T = rect.left, rect.top

        # 结果表大致从窗口 y=195 开始，每行约 36px（两行式布局）
        row_y = T + 195 + row_index * 36
        row_x = L + 700
        before = set(find_windows_by_pid(proc.pid))
        u.SetCursorPos(row_x, row_y)
        time.sleep(0.4)
        u.SetCursorPos(row_x + 2, row_y)     # 再动一下，确保产生 hover 事件
        time.sleep(2.0)                       # 等浮层动画完成

        # 悬停浮层是**独立窗口**，PrintWindow 主窗口抓不到它；
        # 但它属于同一个进程，按 PID 找出来单独抓就行。
        # 抓屏幕是没用的：桌面上总有别的置顶窗口（agent UI 等）会盖住。
        after = set(find_windows_by_pid(proc.pid))
        popups = [h for h in after - before if h]
        if popups:
            for i, h in enumerate(popups):
                size = grab_window(h, out if i == 0 else out.replace(".png", f"_{i}.png"))
                print(f"浮层窗口已保存 {out} {size}")
            return 0

        print("没有捕获到浮层窗口（悬停可能没触发）")
        w, h = rect.right - rect.left, rect.bottom - rect.top
        size = grab_screen(L, T, w, h, out)
        print(f"已保存主窗口屏幕截图 {out} {size}")
        return 1
    finally:
        time.sleep(0.3)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
