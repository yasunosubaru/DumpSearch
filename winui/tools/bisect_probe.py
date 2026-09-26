"""在 Probe（XamlCompiler 可用）里定位 MainWindow.xaml 的崩溃片段。

只删掉 MainWindow.xaml.cs **没有引用**的部件（DataTemplate 内部、Thumb 等），
保证 x:Name 字段仍然存在，代码后置始终能编译 —— 这样每次失败的差异
就只可能来自被删掉的那段 XAML。
"""

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(HERE, "Probe")
XAML = os.path.join(PROBE, "MainWindow.xaml")
ENV = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1",
           PYTHONIOENCODING="utf-8")

# (名称, 正则) —— 命中则整块替换成注释掉的等价物
CUTS = [
    ("结果 DataTemplate 整体",
     r"<ListView\.ItemTemplate>.*?</ListView\.ItemTemplate>"),
    ("盘符 DataTemplate 整体",
     r"<ItemsControl\.ItemTemplate>.*?</ItemsControl\.ItemTemplate>"),
    ("Thumb 分隔条", r"<Thumb x:Name=\"ScopeSplitter\".*?</Thumb>"),
    ("AutoSuggestBox", r"<AutoSuggestBox\b.*?/>"),
    ("ToggleButton 排序方向", r"<ToggleButton\b.*?/>"),
    ("FontIcon 角色图标", r"<FontIcon\b.*?/>"),
    ("EmptyState", r"<StackPanel x:Name=\"EmptyState\".*?</StackPanel>\s*</StackPanel>"),
]


def build():
    r = subprocess.run(["dotnet", "build", "-c", "Release", "-v", "q", "--nologo"],
                       cwd=PROBE, env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    return r.returncode == 0


def main() -> int:
    with open(XAML, encoding="utf-8-sig") as fh:
        pristine = fh.read()

    backup = os.path.join(tempfile.gettempdir(), "probe_mainwindow.xaml")
    with open(backup, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(pristine)

    print("完整版:", "通过" if build() else "失败")
    for name, pattern in CUTS:
        variant, n = re.subn(pattern, "<!-- CUT: " + name + " -->", pristine,
                             count=1, flags=re.S)
        if n == 0:
            print(f"  {name:<24} 未匹配到")
            continue
        with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(variant)
        ok = build()
        print(f"  删除 {name:<24} -> {'通过 ✅' if ok else '仍失败'}")
        if ok:
            print(f"\n>>> 罪魁祸首: {name}")
            return 0

    with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(pristine)
    print("\n没有单个片段是原因")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
