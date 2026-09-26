"""聚焦 CheckBox.IsVisible + x:Bind 组合，找出确切写法。"""

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
    Title="c">
    <Grid>
        <ListView ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}">
            <ListView.ItemTemplate>
                <DataTemplate x:DataType="vm:ResultRow">
                    <Grid>
%s
                    </Grid>
                </DataTemplate>
            </ListView.ItemTemplate>
        </ListView>
    </Grid>
</Window>
"""

CASES = [
    ("C1 纯文本绑定(基线)", '                        <TextBlock Text="{x:Bind NameText}" />'),
    ("C2 CheckBox IsVisible OneWay",
     '                        <CheckBox IsVisible="{x:Bind IsGroupRow, Mode=OneWay}" />'),
    ("C3 CheckBox IsVisible 无 Mode",
     '                        <CheckBox IsVisible="{x:Bind IsGroupRow}" />'),
    ("C4 TextBlock IsVisible OneWay",
     '                        <TextBlock Text="a" IsVisible="{x:Bind IsGroupRow, Mode=OneWay}" />'),
    ("C5 CheckBox IsChecked TwoWay",
     '                        <CheckBox IsChecked="{x:Bind IsChecked, Mode=TwoWay}" />'),
    ("C6 CheckBox IsChecked OneWay",
     '                        <CheckBox IsChecked="{x:Bind IsChecked, Mode=OneWay}" />'),
    ("C7 CheckBox 常量 IsVisible",
     '                        <CheckBox IsVisible="True" />'),
    ("C8 CheckBox Visibility 枚举绑定",
     '                        <CheckBox Visibility="{x:Bind FileRoleVisibility, Mode=OneWay}" />'),
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
    for label, body in CASES:
        with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(SHELL % body)
        print(f"  {label:<36} -> {'ok' if build() else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
