"""在 block3（左侧 ScopePanel Border）内部继续二分。

已确认整个 block 会失败，但还不知道是哪一行。逐个变体测试，
每个变体都是完整闭合、可独立编译的。
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
    xmlns:vm="using:Probe.ViewModels"
    Title="Dump Search">
    <Grid Background="{StaticResource PageBackgroundBrush}">
%s
    </Grid>
</Window>
"""

WRAP_OPEN = """            <Border Grid.Column="0" x:Name="ScopePanel" Width="336"
                    BorderThickness="0,0,1,0"
                    BorderBrush="{StaticResource PanelStrokeBrush}">
                <ScrollViewer VerticalScrollBarVisibility="Auto"
                              HorizontalScrollBarVisibility="Disabled">
                    <StackPanel Padding="16" Spacing="16">
"""
WRAP_CLOSE = """                    </StackPanel>
                </ScrollViewer>
            </Border>
"""

VARIANTS = [
    ("V1 空 ScrollViewer", WRAP_OPEN + '                        <TextBlock Text="x" />\n' + WRAP_CLOSE),

    ("V2 + 标题 Grid(无按钮)", WRAP_OPEN + """
                        <StackPanel Spacing="8">
                            <Grid>
                                <TextBlock Text="scope" Style="{StaticResource SectionTitleStyle}"
                                           VerticalAlignment="Center" />
                            </Grid>
                        </StackPanel>
""" + WRAP_CLOSE),

    ("V3 + 两个 LinkButtonStyle 按钮", WRAP_OPEN + """
                        <StackPanel Spacing="8">
                            <Grid>
                                <TextBlock Text="scope" Style="{StaticResource SectionTitleStyle}"
                                           VerticalAlignment="Center" />
                                <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
                                    <Button Content="all" Click="OnSelectAllDrives"
                                            Style="{StaticResource LinkButtonStyle}"
                                            Padding="8,2" FontSize="12" />
                                    <Button Content="none" Click="OnSelectNoDrives"
                                            Style="{StaticResource LinkButtonStyle}"
                                            Padding="8,2" FontSize="12" />
                                </StackPanel>
                            </Grid>
                        </StackPanel>
""" + WRAP_CLOSE),

    ("V4 + ItemsControl 纯文本模板", WRAP_OPEN + """
                        <ItemsControl x:Name="DriveList"
                                      ItemsSource="{x:Bind ViewModel.Drives, Mode=OneWay}">
                            <ItemsControl.ItemTemplate>
                                <DataTemplate x:DataType="vm:DriveItem">
                                    <TextBlock Text="{x:Bind Title}" FontSize="13" />
                                </DataTemplate>
                            </ItemsControl.ItemTemplate>
                        </ItemsControl>
""" + WRAP_CLOSE),

    ("V5 + 模板内 CheckBox TwoWay 绑定", WRAP_OPEN + """
                        <ItemsControl x:Name="DriveList"
                                      ItemsSource="{x:Bind ViewModel.Drives, Mode=OneWay}">
                            <ItemsControl.ItemTemplate>
                                <DataTemplate x:DataType="vm:DriveItem">
                                    <CheckBox IsChecked="{x:Bind IsSelected, Mode=TwoWay}" />
                                    <TextBlock Text="{x:Bind Title}" FontSize="13" />
                                </DataTemplate>
                            </ItemsControl.ItemTemplate>
                        </ItemsControl>
""" + WRAP_CLOSE),

    ("V6 + 模板内嵌套 Border/Grid/ColumnDefs", WRAP_OPEN + """
                        <ItemsControl x:Name="DriveList"
                                      ItemsSource="{x:Bind ViewModel.Drives, Mode=OneWay}">
                            <ItemsControl.ItemTemplate>
                                <DataTemplate x:DataType="vm:DriveItem">
                                    <Border Padding="10,6" Margin="0,0,0,2"
                                            Background="Transparent">
                                        <Grid>
                                            <Grid.ColumnDefinitions>
                                                <ColumnDefinition Width="Auto" />
                                                <ColumnDefinition Width="*" />
                                                <ColumnDefinition Width="Auto" />
                                            </Grid.ColumnDefinitions>
                                            <CheckBox Grid.Column="0"
                                                      IsChecked="{x:Bind IsSelected, Mode=TwoWay}" />
                                            <StackPanel Grid.Column="1">
                                                <TextBlock Text="{x:Bind Title}" />
                                                <TextBlock Text="{x:Bind Subtitle}" />
                                            </StackPanel>
                                            <TextBlock Grid.Column="2" Text="{x:Bind FreeText}" />
                                        </Grid>
                                    </Border>
                                </DataTemplate>
                            </ItemsControl.ItemTemplate>
                        </ItemsControl>
""" + WRAP_CLOSE),

    ("V7 附加目录 TextBox(Mono 字体)", WRAP_OPEN + """
                        <TextBox x:Name="ExtraDirsBox" AcceptsReturn="True"
                                 FontFamily="{StaticResource MonoFontFamily}"
                                 FontSize="12" Height="64" />
""" + WRAP_CLOSE),

    ("V8 大小区间 Grid", WRAP_OPEN + """
                        <Border Style="{StaticResource CardStyle}" Padding="12">
                            <StackPanel Spacing="10">
                                <TextBlock Text="f" Style="{StaticResource SectionTitleStyle}" />
                                <Grid ColumnSpacing="8">
                                    <Grid.ColumnDefinitions>
                                        <ColumnDefinition Width="*" />
                                        <ColumnDefinition Width="Auto" />
                                        <ColumnDefinition Width="*" />
                                    </Grid.ColumnDefinitions>
                                    <TextBox Grid.Column="0" Text="1" />
                                    <TextBlock Grid.Column="1" Text="-" />
                                    <TextBox Grid.Column="2" />
                                </Grid>
                            </StackPanel>
                        </Border>
""" + WRAP_CLOSE),

    ("V9 KeepRule ComboBox", WRAP_OPEN + """
                        <Border Style="{StaticResource CardStyle}" Padding="12">
                            <StackPanel Spacing="8">
                                <ComboBox x:Name="KeepRuleBox" HorizontalAlignment="Stretch"
                                          SelectionChanged="OnKeepRuleChanged" />
                                <TextBlock x:Name="KeepRuleHint"
                                           Style="{StaticResource CaptionStyle}" FontSize="11" />
                            </StackPanel>
                        </Border>
""" + WRAP_CLOSE),

    ("V10 按钮区", WRAP_OPEN + """
                        <Button x:Name="StartButton" Content="start" Click="OnStartScan"
                                Style="{StaticResource ToolBarButtonStyle}"
                                HorizontalAlignment="Stretch"
                                HorizontalContentAlignment="Center" MinHeight="36" />
                        <Button x:Name="CancelButton" Content="stop"
                                HorizontalAlignment="Stretch"
                                HorizontalContentAlignment="Center"
                                Visibility="Collapsed" Click="OnCancelScan" />
""" + WRAP_CLOSE),
]


def build() -> bool:
    r = subprocess.run(["dotnet", "build", "-c", "Release", "-v", "q", "--nologo"],
                       cwd=PROBE, env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1200)
    return r.returncode == 0


def main() -> int:
    # 桩里必须也有 ViewModel，否则 x:Bind ViewModel.Drives 必然失败
    with open(os.path.join(PROBE, "MainWindow.xaml.cs"), "w",
              encoding="utf-8-sig", newline="") as fh:
        fh.write("""using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Probe.ViewModels;

namespace Probe;

public sealed partial class MainWindow : Window
{
    public MainWindow() { InitializeComponent(); }
    public ResultViewModel ViewModel { get; } = new ResultViewModel();
    public void OnSelectAllDrives(object s, RoutedEventArgs e) { }
    public void OnSelectNoDrives(object s, RoutedEventArgs e) { }
    public void OnBrowseDir(object s, RoutedEventArgs e) { }
    public void OnKeepRuleChanged(object s, SelectionChangedEventArgs e) { }
    public void OnStartScan(object s, RoutedEventArgs e) { }
    public void OnCancelScan(object s, RoutedEventArgs e) { }
    public void OnSplitterDragCompleted(object s, DragCompletedEventArgs e) { }
    public void OnPreviewClean(object s, RoutedEventArgs e) { }
    public void OnExportCsv(object s, RoutedEventArgs e) { }
    public void OnExportScript(object s, RoutedEventArgs e) { }
    public void OnFilterChanged(AutoSuggestBox s, AutoSuggestBoxTextChangedEventArgs e) { }
    public void OnSortChanged(object s, SelectionChangedEventArgs e) { }
    public void OnSortDirectionToggle(object s, RoutedEventArgs e) { }
    public void OnCheckAll(object s, RoutedEventArgs e) { }
    public void OnCheckNone(object s, RoutedEventArgs e) { }
    public void OnInvert(object s, RoutedEventArgs e) { }
    public void OnExpandAll(object s, RoutedEventArgs e) { }
    public void OnCollapseAll(object s, RoutedEventArgs e) { }
}
""")

    bad = []
    for name, body in VARIANTS:
        with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(SHELL % body)
        ok = build()
        print(f"  {name:<42} -> {'ok' if ok else 'FAIL'}")
        if not ok:
            bad.append(name)
    print()
    if bad:
        print("失败的变体:", bad)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
