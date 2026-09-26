"""XAML 运行时崩溃二分：STATUS_STOWED_RESOURCES (0xC000027B) 定位器。

背景：XamlCompiler 对"资源键存在但值转不了目标类型"这类问题**不报错**，
只有真正跑起来才抛 0xC000027B 崩在 Microsoft.UI.Xaml.dll 里。
本脚本把 MainWindow.xaml 里指定的片段整块去掉，逐一重建+运行，
用来定位到底是哪一段引起的崩溃。

用法::

    python tools\\bisect_runtime.py            # 跑全部用例
    python tools\\bisect_runtime.py --keep     # 只探测，不改文件（无效，占位）

片段用 ``<!-- CASE:name -->`` / ``<!-- /CASE:name -->`` 注释标记边界；
没有标记的用例走"整段正则删除"（见 CASES 的 'pattern' 字段）。
"""

import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(os.path.join(HERE, "..", "src", "DumpSearch.App"))
XAML = os.path.join(APP, "MainWindow.xaml")
CSPROJ = os.path.join(APP, "DumpSearch.App.csproj")
EXE = os.path.join(APP, "bin", "Release", "net8.0-windows10.0.19041.0", "DumpSearch.exe")
FIXTURE = os.path.join(os.environ.get("TEMP", r"C:\Temp"), "opencode", "dupecsharp")

# 每个用例 = (名字, 从 MainWindow.xaml 里删掉它的方式)
# "block"  : 删除 <!-- CASE:name --> ... <!-- /CASE:name --> 之间的内容
# "regex"  : 用正则整段替换
CASES = [
    ("tooltip", "block"),
    ("colheader", "block"),
    ("keepbar", "block"),
    ("folderlist", "block"),
]


def mark(name: str) -> None:
    path = os.path.join(APP, "MainWindow.xaml")
    raw = open(path, encoding="utf-8-sig").read()
    print(f"  需要在 MainWindow.xaml 里给 {name} 加边界注释才能二分：")
    print(f"    <!-- CASE:{name} -->  ...  <!-- /CASE:{name} -->")
    print(f"  文件: {path}")
    _ = raw


def strip_block(text: str, name: str) -> str | None:
    pat = re.compile(
        rf"[ \t]*<!--\s*CASE:{re.escape(name)}\s*-->.*?"
        rf"<!--\s*/CASE:{re.escape(name)}\s*-->\n?",
        re.S,
    )
    new, n = pat.subn("", text)
    return new if n == 1 else None


def build() -> bool:
    env = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")
    r = subprocess.run(
        ["dotnet", "build", "-c", "Release", "--nologo"],
        cwd=APP, capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env,
    )
    return r.returncode == 0


def run() -> int:
    """返回进程退出码；启动即崩说明 0xC000027B。"""
    try:
        p = subprocess.run(
            [EXE, "--autoscan", FIXTURE],
            capture_output=True, timeout=25,
            encoding="utf-8", errors="replace",
        )
        return p.returncode
    except subprocess.TimeoutExpired:
        return 0          # 没崩，跑满 25 秒还在运行
    except Exception as exc:                      # noqa: BLE001
        print(f"    启动异常: {exc}")
        return -1


OK_CODES = (0, 124)     # 0 = 正常退出, 124 = 还在跑（没崩）


def main() -> int:
    backup_xaml = XAML + ".bisect"
    backup_proj = CSPROJ + ".bisect"
    shutil.copy2(XAML, backup_xaml)
    shutil.copy2(CSPROJ, backup_proj)
    original = open(XAML, encoding="utf-8-sig").read()

    try:
        print("基线：先确认不改动时的状态")
        if not build():
            print("  基线构建失败，先修构建错误")
            return 2
        base = run()
        print(f"  基线退出码 {base}"
              f"（{'正常' if base in OK_CODES else '崩溃'}）")

        # 基线就崩是常态——否则就没有二分可做了。
        # 判据统一为：去掉某段之后是否**不再崩溃**。
        for name, _kind in CASES:
            stripped = strip_block(original, name)
            if stripped is None:
                mark(name)
                continue
            open(XAML, "w", encoding="utf-8-sig").write(stripped)
            if not build():
                print(f"[{name}] 去掉后构建失败 —— 这段是必需结构，不是崩溃源")
                continue
            code = run()
            fixed = code in OK_CODES
            print(f"[{name}] 去掉后退出码 {code} -> "
                  f"{'不再崩溃，锁定这一段' if fixed else '仍然崩溃'}")

        open(XAML, "w", encoding="utf-8-sig").write(original)
        build()
        return 0
    finally:
        for src, dst in ((backup_xaml, XAML), (backup_proj, CSPROJ)):
            if os.path.exists(src):
                shutil.copy2(src, dst)
                os.remove(src)
        print("已还原 MainWindow.xaml 与 csproj")


if __name__ == "__main__":
    sys.exit(main())
