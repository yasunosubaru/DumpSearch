"""在 DataTemplate 内部做加法二分（先同步最新 Core/ViewModels）。"""

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
    Title="template">
    <Grid>
        <ListView ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}">
            <ListView.ItemTemplate>
                <DataTemplate x:DataType="vm:ResultRow">
%s
                </DataTemplate>
            </ListView.ItemTemplate>
        </ListView>
    </Grid>
</Window>
"""

COLS = """
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="24" />
                        <ColumnDefinition Width="230" />
                        <ColumnDefinition Width="100" />
                        <ColumnDefinition Width="52" />
                        <ColumnDefinition Width="100" />
                        <ColumnDefinition Width="60" />
                        <ColumnDefinition Width="128" />
                        <ColumnDefinition Width="*" />
                    </Grid.ColumnDefinitions>
"""

OPEN = '                    <Grid Padding="0,2" ColumnSpacing="10">\n'
CLOSE = '                    </Grid>\n'
# 空模板必须是良构 XML：之前这里只写了 </Grid>，没有开标签，
# 于是"空模板"这一步必然失败，后面每一步的结论都不可信。
EMPTY = '                    <TextBlock Text="x" />\n'

STEPS = [
    ("空模板", EMPTY),
    ("+ 8 列定义", OPEN + COLS + CLOSE),
    ("+ CheckBox IsGroupRow(OneWay bool)",
     OPEN + COLS + """
                        <CheckBox Grid.Column="0"
                                  IsVisible="{x:Bind IsGroupRow, Mode=OneWay}" />
""" + CLOSE),
    ("+ CheckBox IsChecked(TwoWay bool?)",
     OPEN + COLS + """
                        <CheckBox Grid.Column="0"
                                  IsVisible="{x:Bind IsGroupRow, Mode=OneWay}"
                                  IsChecked="{x:Bind IsChecked, Mode=TwoWay}" />
""" + CLOSE),
    ("+ FontIcon Glyph(OneWay string)",
     OPEN + COLS + """
                        <FontIcon Grid.Column="0" Glyph="{x:Bind RoleGlyph, Mode=OneWay}" />
""" + CLOSE),
    ("+ FontIcon Visibility(枚举)",
     OPEN + COLS + """
                        <FontIcon Grid.Column="0"
                                  Glyph="{x:Bind RoleGlyph, Mode=OneWay}"
                                  Visibility="{x:Bind FileRoleVisibility, Mode=OneWay}" />
""" + CLOSE),
    ("+ FontIcon Foreground(Brush)",
     OPEN + COLS + """
                        <FontIcon Grid.Column="0"
                                  Glyph="{x:Bind RoleGlyph, Mode=OneWay}"
                                  Visibility="{x:Bind FileRoleVisibility, Mode=OneWay}"
                                  Foreground="{x:Bind RoleBrush, Mode=OneWay}" />
""" + CLOSE),
    ("+ NameText(OneWay string)",
     OPEN + COLS + """
                        <TextBlock Grid.Column="1" Text="{x:Bind NameText}" />
""" + CLOSE),
    ("+ FontWeight(结构体)",
     OPEN + COLS + """
                        <TextBlock Grid.Column="1" Text="{x:Bind NameText}"
                                   FontWeight="{x:Bind NameWeight, Mode=OneWay}" />
""" + CLOSE),
    ("+ Margin(Thickness)",
     OPEN + COLS + """
                        <TextBlock Grid.Column="1" Text="{x:Bind NameText}"
                                   Margin="{x:Bind NameMargin, Mode=OneWay}" />
""" + CLOSE),
    ("+ 其余 7 个文本列",
     OPEN + COLS + """
                        <TextBlock Grid.Column="1" Text="{x:Bind NameText}" />
                        <TextBlock Grid.Column="2" Text="{x:Bind SizeText}" />
                        <TextBlock Grid.Column="3" Text="{x:Bind CountText}" />
                        <TextBlock Grid.Column="4" Text="{x:Bind WastedText}" />
                        <TextBlock Grid.Column="5" Text="{x:Bind RoleText}" />
                        <TextBlock Grid.Column="6" Text="{x:Bind TimeText}" />
                        <TextBlock Grid.Column="7" Text="{x:Bind PathText}" />
""" + CLOSE),
    ("+ MonoPathStyle 样式",
     OPEN + COLS + """
                        <TextBlock Grid.Column="7" Text="{x:Bind PathText}"
                                   Style="{StaticResource MonoPathStyle}" />
""" + CLOSE),
    ("+ AccentTextFillColorPrimaryBrush(ThemeResource)",
     OPEN + COLS + """
                        <TextBlock Grid.Column="4" Text="{x:Bind WastedText}"
                                   Foreground="{ThemeResource AccentTextFillColorPrimaryBrush}" />
""" + CLOSE),
    ("+ Grid 里嵌套 Grid(第0列图标区)",
     OPEN + COLS + """
                        <Grid Grid.Column="0">
                            <CheckBox IsVisible="{x:Bind IsGroupRow, Mode=OneWay}"
                                      IsChecked="{x:Bind IsChecked, Mode=TwoWay}" />
                            <FontIcon Glyph="{x:Bind RoleGlyph, Mode=OneWay}"
                                      Visibility="{x:Bind FileRoleVisibility, Mode=OneWay}"
                                      Foreground="{x:Bind RoleBrush, Mode=OneWay}" />
                        </Grid>
""" + CLOSE),
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

    for name, body in STEPS:
        with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(SHELL % body)
        ok = build()
        print(f"  {name:<44} -> {'ok' if ok else 'FAIL'}")
        if not ok:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
