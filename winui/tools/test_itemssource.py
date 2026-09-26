"""只改 ItemsSource（Rows vs Drives），模板与代码后置完全相同。

前两个 bisect 脚本给出的结论互相矛盾：
  bisect_type  : Drives + ResultRow 空模板 -> ok
  bisect_template: Rows   + ResultRow 空模板 -> FAIL
唯一差别就是 ItemsSource，所以直接对比这一项。
"""

import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(HERE, "src", "DumpSearch.App")
PROBE = os.path.join(HERE, "Probe")
XAML = os.path.join(PROBE, "MainWindow.xaml")
ENV = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")

STUB_CS = """using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Probe.ViewModels;

namespace Probe;

public sealed partial class MainWindow : Window
{
    public MainWindow() { InitializeComponent(); }
    public ResultViewModel ViewModel { get; } = new ResultViewModel();
}
"""

SHELL = """<?xml version="1.0" encoding="utf-8"?>
<Window
    x:Class="Probe.MainWindow"
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    xmlns:vm="using:Probe.ViewModels"
    Title="items source">
    <Grid>
        <ListView ItemsSource="{x:Bind ViewModel.%s, Mode=OneWay}">
            <ListView.ItemTemplate>
                <DataTemplate x:DataType="vm:%s">
                    <TextBlock Text="x" />
                </DataTemplate>
            </ListView.ItemTemplate>
        </ListView>
    </Grid>
</Window>
"""

CASES = [
    ("Drives  + DriveItem", "Drives", "DriveItem"),
    ("Drives  + ResultRow", "Drives", "ResultRow"),
    ("Rows    + ResultRow", "Rows", "ResultRow"),
    ("Rows    + DriveItem", "Rows", "DriveItem"),
]


def sync() -> None:
    for sub in ("Core", "ViewModels", "Styles"):
        dst = os.path.join(PROBE, sub)
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(os.path.join(APP, sub), dst)
    for cs in (glob.glob(os.path.join(PROBE, "Core", "**", "*.cs"), recursive=True)
               + glob.glob(os.path.join(PROBE, "ViewModels", "**", "*.cs"), recursive=True)):
        with open(cs, encoding="utf-8-sig") as fh:
            t = fh.read()
        t = (t.replace("namespace DumpSearch.App.Core", "namespace Probe.Core")
             .replace("namespace DumpSearch.App.ViewModels", "namespace Probe.ViewModels")
             .replace("using DumpSearch.App.Core", "using Probe.Core"))
        with open(cs, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(t)


def build() -> bool:
    r = subprocess.run(["dotnet", "build", "-c", "Release", "-v", "q", "--nologo"],
                       cwd=PROBE, env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1200)
    return r.returncode == 0


def main() -> int:
    sync()
    with open(os.path.join(PROBE, "MainWindow.xaml.cs"), "w",
              encoding="utf-8-sig", newline="") as fh:
        fh.write(STUB_CS)
    for label, source, dtype in CASES:
        with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(SHELL % (source, dtype))
        print(f"  {label:<26} -> {'ok' if build() else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
