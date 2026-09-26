"""在项目根目录生成带图标的 Windows 快捷方式（.lnk）。

为什么需要：exe 在 ``winui\\src\\...\\bin\\Release\\...`` 这样的构建产物深处，
正常用户不会去那里翻。表层放一个带图标的快捷方式，点一下就能开。

为什么快捷方式指向 .bat 而不是直接指向 exe：exe 的路径依赖构建配置
（Debug/Release、TFM 版本），改一次配置快捷方式就指向空气了。启动器脚本
每次现找，必要时现构建。

用法::

    python tools\\make_shortcut.py            # 生成/更新快捷方式
    python tools\\make_shortcut.py --desktop  # 顺便在桌面也放一个
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

LAUNCHER = os.path.join(ROOT, "启动 WinUI 3 界面.bat")
ICON = os.path.join(ROOT, "winui", "src", "DumpSearch.App", "Assets", "DumpSearch.ico")
LNK_NAME = "Dump Search (WinUI 3).lnk"


def make_lnk(target: str, icon: str, desc: str) -> str:
    """用 WScript.Shell COM 建快捷方式。"""
    import win32com.client  # type: ignore

    shell = win32com.client.Dispatch("WScript.Shell")
    lnk = shell.CreateShortCut(target)
    # 76 = 正常窗口; hotkey 留空
    lnk.WindowStyle = 1
    lnk.TargetPath = LAUNCHER
    lnk.WorkingDirectory = ROOT
    lnk.Description = desc
    lnk.IconLocation = icon + ",0"
    lnk.save()
    return target


def make_lnk_powershell(target: str, icon: str, desc: str) -> str:
    """没有 pywin32 时的退路：让 PowerShell 建。

    WScript.Shell 的 COM 只能用 32 位视图注册，64 位 Python 里
    ``Dispatch`` 有时拿不到正确的 CLSID；PowerShell 更稳。
    """
    import subprocess

    ps = (
        "$s = (New-Object -ComObject WScript.Shell);"
        f"$l = $s.CreateShortcut('{target}');"
        "$l.TargetPath = '" + LAUNCHER.replace("'", "''") + "';"
        "$l.WorkingDirectory = '" + ROOT.replace("'", "''") + "';"
        "$l.Description = '" + desc.replace("'", "''") + "';"
        "$l.IconLocation = '" + icon.replace("'", "''") + ",0';"
        "$l.WindowStyle = 1;"
        "$l.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        check=True,
    )
    return target


def build(target: str, desc: str) -> str:
    if os.path.exists(target):
        os.remove(target)          # 只删自己刚生成的那个快捷方式
    try:
        return make_lnk(target, ICON, desc)
    except Exception as exc:
        print(f"  pywin32 不可用（{exc}），改用 PowerShell")
        return make_lnk_powershell(target, ICON, desc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--desktop", action="store_true", help="同时在桌面放一份")
    args = ap.parse_args()

    missing = [p for p in (LAUNCHER, ICON) if not os.path.exists(p)]
    if missing:
        for p in missing:
            print(f"[错误] 缺少文件: {p}")
        if ICON in missing:
            print("       先跑 python tools\\make_icon.py 生成图标")
        return 1

    out = build(os.path.join(ROOT, LNK_NAME),
                "Dump Search · 全盘重复文件查找（WinUI 3 原生界面）")
    print(f"已生成快捷方式: {out}")

    if args.desktop:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if os.path.isdir(desktop):
            d = build(os.path.join(desktop, LNK_NAME), "Dump Search · 重复文件查找")
            print(f"已生成桌面快捷方式: {d}")
        else:
            print(f"[跳过] 找不到桌面目录: {desktop}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
