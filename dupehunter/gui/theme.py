"""主题、字体与高 DPI。"""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont


def enable_dpi_awareness() -> str:
    """让 Tk 在高 DPI 屏上不糊。

    必须在**创建任何窗口之前**调用。依次尝试 Per-Monitor v2 -> v1 -> 系统。
    """
    if sys.platform != "win32":
        return "n/a"
    user32 = ctypes.windll.user32
    # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except Exception:
        pass
    try:  # Windows 8.1+
        shcore = ctypes.windll.shcore
        if shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except Exception:
        pass
    try:  # Vista+
        if user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass
    return "unaware"


# Windows 11 深色模式下的浅色底色，避免文字看不见
DARK_BG = "#1f1f1f"
DARK_FG = "#e6e6e6"

UI_FONT_CANDIDATES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Segoe UI",
    "PingFang SC",
    "Noto Sans CJK SC",
    "SimHei",
    "TkDefaultFont",
)

MONO_FONT_CANDIDATES = (
    "Cascadia Mono",
    "Consolas",
    "Sarasa Mono SC",
    "DejaVu Sans Mono",
    "Courier New",
)


def pick_font(root: tk.Misc, candidates=UI_FONT_CANDIDATES, size: int = 9) -> str:
    """从候选里挑一个系统上真实存在的字体。"""
    available = set(tkfont.families(root))
    for name in candidates:
        if name in available:
            return name
    return "TkDefaultFont"


def pick_mono_font(root: tk.Misc, size: int = 9) -> str:
    available = set(tkfont.families(root))
    for name in MONO_FONT_CANDIDATES:
        if name in available:
            return name
    return "TkFixedFont"


def is_dark_mode(root: tk.Misc) -> bool:
    """粗略判断系统是否处于深色模式。"""
    if sys.platform != "win32":
        return False
    try:
        # HKCU\..\Themes\Personalize -> AppsUseLightTheme == 0 表示深色
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except Exception:
        return False


def build_style(root: tk.Misc, dark: bool | None = None) -> ttk.Style:
    """构造 ttk 样式。

    选 ``clam`` 而不是 ``vista``：``vista`` 主题会**忽略** ``rowheight``
    设置（行高改不动），而且 Tk 8.6 完全不实现 Win11 深色模式。
    自定义行高 / 深色配色 / 统一字体这三件事都只有 clam 能做到。
    """
    style = ttk.Style(root)
    available = style.theme_names()
    theme = "clam" if "clam" in available else style.theme_use()

    if dark is None:
        dark = is_dark_mode(root)

    ui = pick_font(root, size=9)
    mono = pick_mono_font(root, size=9)
    row_height = 22

    try:
        style.configure(".", font=(ui, 9))
        style.configure("Treeview", font=(ui, 9), rowheight=row_height)
        style.configure("Treeview.Heading", font=(ui, 9, "bold"))
        style.configure("TButton", font=(ui, 9))
        style.configure("TCheckbutton", font=(ui, 9))
        style.configure("TLabel", font=(ui, 9))
        style.configure("TLabelframe.Label", font=(ui, 9, "bold"))
    except tk.TclError:
        pass

    if dark:
        try:
            style.theme_use("clam")
            style.configure(".", background=DARK_BG, foreground=DARK_FG,
                            fieldbackground=DARK_BG)
            style.configure("TFrame", background=DARK_BG)
            style.configure("TLabel", background=DARK_BG, foreground=DARK_FG)
            style.configure("TLabelframe", background=DARK_BG, foreground=DARK_FG)
            style.configure("TLabelframe.Label", background=DARK_BG, foreground=DARK_FG)
            style.configure("TButton", background="#2d2d2d", foreground=DARK_FG)
            style.map("TButton",
                      background=[("active", "#3c3c3c")])
            style.configure("TCheckbutton", background=DARK_BG, foreground=DARK_FG)
            style.configure("TRadiobutton", background=DARK_BG, foreground=DARK_FG)
            style.configure("TEntry", fieldbackground="#2d2d2d", foreground=DARK_FG)
            # clam 的 Combobox 在 readonly 状态下用的是另一套 element，
            # 只配 fieldbackground 不生效——必须对 readonly 态单独 map，
            # 否则会渲染成"白底 + 白字"（看起来就是个空框）。
            style.configure("TCombobox", fieldbackground="#2d2d2d",
                            background="#2d2d2d", foreground=DARK_FG,
                            arrowcolor=DARK_FG, bordercolor="#3c3c3c",
                            selectbackground="#2d2d2d",
                            selectforeground=DARK_FG)
            style.map(
                "TCombobox",
                fieldbackground=[("readonly", "#2d2d2d"), ("disabled", "#2d2d2d")],
                background=[("readonly", "#2d2d2d"), ("disabled", "#2d2d2d")],
                foreground=[("readonly", DARK_FG), ("disabled", "#888888")],
                selectbackground=[("readonly", "#2d2d2d")],
                selectforeground=[("readonly", DARK_FG)],
                arrowcolor=[("readonly", DARK_FG), ("disabled", "#888888")],
            )
            style.configure("TProgressbar", background="#3c7dd8",
                            troughcolor="#2d2d2d")
            style.configure("Treeview", background="#252525",
                            fieldbackground="#252525", foreground=DARK_FG,
                            bordercolor="#3c3c3c")
            style.configure("Treeview.Heading", background="#333333",
                            foreground=DARK_FG, relief="flat")
            style.map("Treeview", background=[("selected", "#2f5c9e")])
            style.configure("Vertical.TScrollbar", background="#333333",
                            troughcolor="#252525", arrowcolor=DARK_FG)
            style.configure("Horizontal.TScrollbar", background="#333333",
                            troughcolor="#252525", arrowcolor=DARK_FG)
        except tk.TclError:
            pass

    return style


class Fonts:
    """统一管理字体，避免到处硬编码。"""

    def __init__(self, root: tk.Misc) -> None:
        self.ui = pick_font(root, size=9)
        self.ui_small = pick_font(root, size=8)
        self.mono = pick_mono_font(root, size=9)
        self.bold = (self.ui, 9, "bold")
        self.title = (self.ui, 13, "bold")
        self.mono_tuple = (self.mono, 9)
