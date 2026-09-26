"""在 Probe 里定位 XAML 崩溃点。

做法：把 MainWindow.xaml.cs 换成桩（不引用任何 x:Name），然后逐段加回 XAML。
这样每次构建的成败只取决于刚加进来的那一段 XAML，不会被代码后置牵连。
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(HERE, "Probe")
XAML = os.path.join(PROBE, "MainWindow.xaml")
STASH = os.path.join(HERE, ".bisect_stash")
ENV = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")

STUB_CS = """using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using WinRT.Interop;

namespace Probe;

public sealed partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();
        SystemBackdrop = new MicaBackdrop();
        var hwnd = WindowNative.GetWindowHandle(this);
        AppWindow.GetFromWindowId(Win32Interop.GetWindowIdFromWindow(hwnd))
                 .Resize(new Windows.Graphics.SizeInt32(1200, 800));
    }
}
"""

HEAD = """<?xml version="1.0" encoding="utf-8"?>
<Window
    x:Class="Probe.MainWindow"
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    xmlns:vm="using:Probe.ViewModels"
    Title="Dump Search">
    <Grid Background="{StaticResource PageBackgroundBrush}">
"""

STAGES = [
    ("root Grid", ""),
    ("+ rows + titlebar", """
        <Grid.RowDefinitions>
            <RowDefinition Height="32" />
            <RowDefinition Height="*" />
        </Grid.RowDefinitions>
        <Grid Grid.Row="0" Background="Transparent">
            <TextBlock Text="Dump Search" FontSize="12"
                       Foreground="{StaticResource SecondaryTextBrush}" />
        </Grid>
"""),
    ("+ left ScrollViewer", """
        <Grid Grid.Row="1">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto" />
                <ColumnDefinition Width="*" />
            </Grid.ColumnDefinitions>
            <Border Grid.Column="0" Width="336" BorderThickness="0,0,1,0"
                    BorderBrush="{StaticResource PanelStrokeBrush}">
                <ScrollViewer VerticalScrollBarVisibility="Auto">
                    <StackPanel Padding="16" Spacing="16">
                        <TextBlock Text="scope" Style="{StaticResource SectionTitleStyle}" />
                    </StackPanel>
                </ScrollViewer>
            </Border>
"""),
    ("+ card + inputs", """
                        <Border Style="{StaticResource CardStyle}" Padding="12">
                            <StackPanel Spacing="8">
                                <TextBlock Text="filter" Style="{StaticResource SectionTitleStyle}" />
                                <TextBox Text="1" />
                                <TextBox PlaceholderText="none" />
                                <CheckBox Content="verify" IsChecked="True" />
                                <ComboBox HorizontalAlignment="Stretch" />
                            </StackPanel>
                        </Border>
"""),
    ("+ buttons", """
                        <Button Content="start"
                                Style="{StaticResource ToolBarButtonStyle}"
                                HorizontalAlignment="Stretch" MinHeight="36" />
"""),
    ("+ drive ItemsControl (text tmpl)", """
                        <ItemsControl ItemsSource="{x:Bind ViewModel.Drives, Mode=OneWay}">
                            <ItemsControl.ItemTemplate>
                                <DataTemplate x:DataType="vm:DriveItem">
                                    <TextBlock Text="{x:Bind Title}" />
                                </DataTemplate>
                            </ItemsControl.ItemTemplate>
                        </ItemsControl>
"""),
    ("+ drive tmpl: bool binding", """
                        <ItemsControl ItemsSource="{x:Bind ViewModel.Drives, Mode=OneWay}">
                            <ItemsControl.ItemTemplate>
                                <DataTemplate x:DataType="vm:DriveItem">
                                    <CheckBox IsChecked="{x:Bind IsSelected, Mode=TwoWay}" />
                                    <TextBlock Text="{x:Bind Subtitle}" />
                                </DataTemplate>
                            </ItemsControl.ItemTemplate>
                        </ItemsControl>
"""),
    ("+ right header + toolbar", """
            <Grid Grid.Column="1" Margin="16,8,16,0">
                <Grid.RowDefinitions>
                    <RowDefinition Height="Auto" />
                    <RowDefinition Height="Auto" />
                    <RowDefinition Height="*" />
                    <RowDefinition Height="Auto" />
                </Grid.RowDefinitions>
                <StackPanel Grid.Row="0">
                    <TextBlock Text="dup" FontSize="24" FontWeight="SemiBold" />
                    <TextBlock Style="{StaticResource CaptionStyle}" />
                    <Button Content="clean"
                            Style="{StaticResource ToolBarButtonStyle}" />
                </StackPanel>
                <StackPanel Grid.Row="1" Orientation="Horizontal" Spacing="8">
                    <AutoSuggestBox Width="240" QueryIcon="Find" />
                    <ComboBox Width="150" />
                    <ToggleButton Content="desc" />
                    <Button Content="all" />
                </StackPanel>
"""),
    ("+ results ListView (no tmpl)", """
                <Grid Grid.Row="2">
                    <ListView Style="{StaticResource ResultsListViewStyle}"
                              ItemContainerStyle="{StaticResource ResultsItemContainerStyle}"
                              ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}" />
                </Grid>
"""),
    ("+ results tmpl: text binds", """
                <Grid Grid.Row="2">
                    <ListView ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}">
                        <ListView.ItemTemplate>
                            <DataTemplate x:DataType="vm:ResultRow">
                                <Grid>
                                    <TextBlock Text="{x:Bind NameText}" />
                                    <TextBlock Text="{x:Bind SizeText}" />
                                    <TextBlock Text="{x:Bind CountText}" />
                                    <TextBlock Text="{x:Bind WastedText}" />
                                    <TextBlock Text="{x:Bind RoleText}" />
                                    <TextBlock Text="{x:Bind TimeText}" />
                                    <TextBlock Text="{x:Bind PathText}" />
                                </Grid>
                            </DataTemplate>
                        </ListView.ItemTemplate>
                    </ListView>
                </Grid>
"""),
    ("+ results tmpl: bool/enum/struct", """
                <Grid Grid.Row="2">
                    <ListView ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}">
                        <ListView.ItemTemplate>
                            <DataTemplate x:DataType="vm:ResultRow">
                                <Grid>
                                    <CheckBox IsVisible="{x:Bind IsGroupRow, Mode=OneWay}"
                                              IsChecked="{x:Bind IsChecked, Mode=TwoWay}" />
                                    <TextBlock Text="{x:Bind NameText}"
                                               FontWeight="{x:Bind NameWeight, Mode=OneWay}"
                                               Margin="{x:Bind NameMargin, Mode=OneWay}" />
                                    <TextBlock Text="{x:Bind RoleText}"
                                               Foreground="{x:Bind RoleBrush, Mode=OneWay}" />
                                </Grid>
                            </DataTemplate>
                        </ListView.ItemTemplate>
                    </ListView>
                </Grid>
"""),
    ("+ FontIcon Glyph bind", """
                <Grid Grid.Row="2">
                    <ListView ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}">
                        <ListView.ItemTemplate>
                            <DataTemplate x:DataType="vm:ResultRow">
                                <Grid>
                                    <FontIcon Glyph="{x:Bind RoleGlyph, Mode=OneWay}"
                                              Visibility="{x:Bind FileRoleVisibility, Mode=OneWay}"
                                              Foreground="{x:Bind RoleBrush, Mode=OneWay}" />
                                    <TextBlock Text="{x:Bind NameText}" />
                                </Grid>
                            </DataTemplate>
                        </ListView.ItemTemplate>
                    </ListView>
                </Grid>
"""),
    ("+ progress + InfoBar", """
            <StackPanel Grid.Row="3">
                <ProgressBar Minimum="0" Maximum="1" />
                <InfoBar IsOpen="False" IsClosable="True" />
            </StackPanel>
        </Grid>
"""),
]


def build():
    r = subprocess.run(["dotnet", "build", "-c", "Release", "-v", "q", "--nologo"],
                       cwd=PROBE, env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    return r.returncode == 0


def write(text):
    with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(text)


def main() -> int:
    os.makedirs(STASH, exist_ok=True)
    for name in ("MainWindow.xaml.cs", "MainWindow.xaml"):
        src = os.path.join(PROBE, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(STASH, name))
            os.remove(src)
    with open(os.path.join(PROBE, "MainWindow.xaml.cs"), "w",
              encoding="utf-8-sig", newline="") as fh:
        fh.write(STUB_CS)

    acc = ""
    for name, chunk in STAGES:
        acc += chunk
        write(HEAD + acc + "\n    </Grid>\n</Window>\n")
        ok = build()
        print(f"  {name:<32} -> {'ok' if ok else 'FAIL'}")
        if not ok:
            print(f"\n>>> culprit stage: {name}")
            keep = os.path.join(HERE, ".bisect_stash", "culprit.xaml")
            with open(keep, "w", encoding="utf-8-sig", newline="") as fh:
                fh.write(HEAD + acc + "\n    </Grid>\n</Window>\n")
            print(f"    saved: {keep}")
            return 1

    print("\nall stages ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
