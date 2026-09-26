"""确认 UIElement.Cursor 不能在 XAML 里直接写字符串游标名。"""

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
    Title="Cursor test">
    <Grid>
        <Thumb x:Name="T" Grid.Column="0" Width="6"
               HorizontalAlignment="Right" Opacity="0"
CURSORATTR />
    </Grid>
</Window>
"""

CASES = [
    ("无 Cursor", ""),
    ('Cursor="SizeWestEast"', '               Cursor="SizeWestEast"'),
    ('Cursor="Hand"', '               Cursor="Hand"'),
    ('Cursor={x:Null}', '               Cursor="{x:Null}"'),
    ('只留 Opacity（去掉 Cursor）', '               Cursor=""'),
]

STUB = """using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls.Primitives;
namespace Probe;
public sealed partial class MainWindow : Window
{
    public MainWindow() { InitializeComponent(); }
    public void OnSplitterDragCompleted(object s, DragCompletedEventArgs e) { }
}
"""


def build() -> bool:
    r = subprocess.run(["dotnet", "build", "-c", "Release", "-v", "q", "--nologo"],
                       cwd=PROBE, env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1200)
    return r.returncode == 0


def main() -> int:
    with open(os.path.join(PROBE, "MainWindow.xaml.cs"), "w",
              encoding="utf-8-sig", newline="") as fh:
        fh.write(STUB)
    for name, attr in CASES:
        with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(SHELL.replace("CURSORATTR />", attr + " />"))
        print(f"  {name:<32} -> {'ok' if build() else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
