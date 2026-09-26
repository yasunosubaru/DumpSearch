"""二分定位 MainWindow.xaml 里让 XamlCompiler 崩溃的片段。

XamlCompiler 失败时只打印「错误: 1」，没有任何行号信息，所以只能机械二分。
做法：每次从完整版里删掉一个可疑片段，重新构建，看是否变成 0 错误。
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ = os.path.join(HERE, "src", "DumpSearch.App")
XAML = os.path.join(PROJ, "MainWindow.xaml")
BACKUP = os.path.join(tempfile.gettempdir(), "MainWindow.pristine.xaml")

ENV = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")


def build_ok():
    r = subprocess.run(["dotnet", "build", "-c", "Release", "-v", "q", "--nologo"],
                       cwd=PROJ, env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    return r.returncode == 0


def write(text):
    with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(text)


def cut(text, start_marker, end_marker):
    """删掉 start_marker..end_marker（含）之间的内容。"""
    i = text.find(start_marker)
    if i < 0:
        return None
    j = text.find(end_marker, i)
    if j < 0:
        return None
    return text[:i] + text[j + len(end_marker):]


CANDIDATES = [
    ("Thumb 分隔条", '<Thumb x:Name="ScopeSplitter"', '</Thumb>'),
    ("盘符 ItemsControl 模板", "<ItemsControl.ItemTemplate>", "</ItemsControl.ItemTemplate>"),
    ("AutoSuggestBox", '<AutoSuggestBox Grid.Column="0"', '/>'),
    ("左栏筛选卡片", '<Border Style="{StaticResource CardStyle}" Padding="12">\n                                    <StackPanel Spacing="10">', '</Border>'),
    ("左栏保留策略卡片", '<Border Style="{StaticResource CardStyle}" Padding="12">\n                            <StackPanel Spacing="8">', '</Border>'),
    ("结果工具条", '<Grid Grid.Row="1" Margin="0,0,0,8" ColumnSpacing="8">', '</Grid>'),
    ("进度区", '<Border Grid.Row="3" Padding="0,10,0,10">', '</Border>'),
    ("空状态", '<StackPanel x:Name="EmptyState"', '</StackPanel>'),
    ("InfoBar", '<InfoBar x:Name="StatusBar"', '/>'),
]


def main():
    if not os.path.exists(BACKUP):
        shutil.copy(XAML, BACKUP)
    with open(BACKUP, encoding="utf-8-sig") as fh:
        pristine = fh.read()

    write(pristine)
    print("完整版构建:", "通过" if build_ok() else "失败")
    if build_ok():
        print("?! 完整版就能通过，无需二分")
        return 0

    # 逐个删除候选片段，看哪个删掉后能通过
    for name, start, end in CANDIDATES:
        variant = cut(pristine, start, end)
        if variant is None:
            print(f"  [跳过] {name}: 找不到标记")
            continue
        write(variant)
        ok = build_ok()
        print(f"  删除 {name:<22} -> {'通过 ✅' if ok else '仍失败'}")
        if ok:
            print(f"\n>>> 罪魁祸首: {name}")
            shutil.copy(XAML, os.path.join(tempfile.gettempdir(),
                                           f"MainWindow.min.{name}.xaml"))
            return 0

    write(pristine)
    print("\n没有任何单个片段是原因，可能是组合问题或更细的语法")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
