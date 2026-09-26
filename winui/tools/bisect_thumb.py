"""验证 Thumb 分隔条是不是让 XamlCompiler 失败的元凶。

怀疑点：
  * <Thumb.Resources> 里覆盖 ThumbBackground 系列键
  * Cursor="SizeWestEast"
  * DragCompleted="..." 事件绑定
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(HERE, "Probe")
XAML = os.path.join(PROBE, "MainWindow.xaml")
ENV = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")

SHELL = """<?xml version="1.0" encoding="utf-8"?>
<Window
    x:Class="Probe.MainWindow"
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Title="Dump Search">
    <Grid>
%s
    </Grid>
</Window>
"""

VARIANTS = [
    ("T0 无 Thumb", ""),
    ("T1 裸 Thumb（无属性）", '        <Thumb />\n'),
    ("T2 Thumb + x:Name/Grid.Column/Width",
     '        <Thumb x:Name="ScopeSplitter" Grid.Column="0" Width="6" />\n'),
    ("T3 Thumb + Opacity/Cursor",
     '        <Thumb x:Name="ScopeSplitter" Grid.Column="0" Width="6"\n'
     '               HorizontalAlignment="Right" Opacity="0" Cursor="SizeWestEast" />\n'),
    ("T4 Thumb + DragCompleted 事件",
     '        <Thumb x:Name="ScopeSplitter" Grid.Column="0" Width="6"\n'
     '               HorizontalAlignment="Right" Opacity="0" Cursor="SizeWestEast"\n'
     '               DragCompleted="OnSplitterDragCompleted" />\n'),
    ("T5 Thumb + Resources 覆盖 3 个键",
     '        <Thumb x:Name="ScopeSplitter" Grid.Column="0" Width="6"\n'
     '               HorizontalAlignment="Right" Opacity="0" Cursor="SizeWestEast"\n'
     '               DragCompleted="OnSplitterDragCompleted">\n'
     '            <Thumb.Resources>\n'
     '                <SolidColorBrush x:Key="ThumbBackground" Color="Transparent" />\n'
     '                <SolidColorBrush x:Key="ThumbBackgroundPointerOver" Color="Transparent" />\n'
     '                <SolidColorBrush x:Key="ThumbBackgroundPressed" Color="Transparent" />\n'
     '            </Thumb.Resources>\n'
     '        </Thumb>\n'),
    ("T6 只 Resources 覆盖 1 个键",
     '        <Thumb x:Name="ScopeSplitter" Grid.Column="0" Width="6">\n'
     '            <Thumb.Resources>\n'
     '                <SolidColorBrush x:Key="ThumbBackground" Color="Transparent" />\n'
     '            </Thumb.Resources>\n'
     '        </Thumb>\n'),
]


def build() -> bool:
    r = subprocess.run(["dotnet", "build", "-c", "Release", "-v", "q", "--nologo"],
                       cwd=PROBE, env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1200)
    return r.returncode == 0


def main() -> int:
    for name, body in VARIANTS:
        with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(SHELL % body)
        print(f"  {name:<40} -> {'ok' if build() else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
