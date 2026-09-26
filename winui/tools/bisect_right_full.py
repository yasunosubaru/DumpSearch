"""从"完整的右栏"出发，逐个移除子块，找出组合后才失败的那一处。

之前 bisect_right.py 的每个变体都是独立的（只测一个子块），
全都没问题，但真实 XAML 把它们全放进去就失败 —— 所以要做"减法"，
在完整上下文里逐个拿掉。
"""

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(HERE, "Probe")
XAML = os.path.join(PROBE, "MainWindow.xaml")
ENV = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")

STUB_CS = """using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Probe.ViewModels;

namespace Probe;

public sealed partial class MainWindow : Window
{
    public MainWindow() { InitializeComponent(); }
    public ResultViewModel ViewModel { get; } = new ResultViewModel();
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
"""

SHELL = """<?xml version="1.0" encoding="utf-8"?>
<Window
    x:Class="Probe.MainWindow"
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    xmlns:vm="using:Probe.ViewModels"
    Title="right combined">
    <Grid>
        <Grid.ColumnDefinitions>
            <ColumnDefinition Width="Auto" />
            <ColumnDefinition Width="*" />
        </Grid.ColumnDefinitions>
        <Grid Grid.Column="1" Margin="16,8,16,0">
            <Grid.RowDefinitions>
                <RowDefinition Height="Auto" />
                <RowDefinition Height="Auto" />
                <RowDefinition Height="*" />
                <RowDefinition Height="Auto" />
            </Grid.RowDefinitions>
%s
        </Grid>
    </Grid>
</Window>
"""

# 完整右栏（就是真实 MainWindow.xaml 的右栏）
FULL = """
            <Grid Grid.Row="0" Margin="0,0,0,8">
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="*" />
                    <ColumnDefinition Width="Auto" />
                </Grid.ColumnDefinitions>
                <StackPanel Grid.Column="0">
                    <TextBlock Text="dup" FontSize="24" FontWeight="SemiBold" />
                    <TextBlock x:Name="SummaryText" Style="{StaticResource CaptionStyle}" Margin="0,2,0,0" />
                </StackPanel>
                <StackPanel Grid.Column="1" Orientation="Horizontal"
                            VerticalAlignment="Center" Spacing="8">
                    <Button x:Name="ExportCsvButton" Content="csv" Click="OnExportCsv" Visibility="Collapsed" />
                    <Button x:Name="ExportScriptButton" Content="ps" Click="OnExportScript" Visibility="Collapsed" />
                    <Button x:Name="CleanButton" Content="clean" Click="OnPreviewClean"
                            Style="{StaticResource ToolBarButtonStyle}"
                            Click="OnPreviewClean" Visibility="Collapsed" />
                </StackPanel>
            </Grid>

            <Grid Grid.Row="1" Margin="0,0,0,8" ColumnSpacing="8">
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="Auto" />
                    <ColumnDefinition Width="*" />
                    <ColumnDefinition Width="Auto" />
                    <ColumnDefinition Width="Auto" />
                    <ColumnDefinition Width="Auto" />
                    <ColumnDefinition Width="Auto" />
                    <ColumnDefinition Width="Auto" />
                </Grid.ColumnDefinitions>
                <AutoSuggestBox Grid.Column="0" Width="240" PlaceholderText="f"
                                QueryIcon="Find" TextChanged="OnFilterChanged" />
                <ComboBox Grid.Column="2" Width="150" x:Name="SortBox" SelectionChanged="OnSortChanged" />
                <ToggleButton Grid.Column="3" x:Name="SortDirToggle" Content="desc" Click="OnSortDirectionToggle" />
                <Button Grid.Column="4" Content="all" Click="OnCheckAll" />
                <Button Grid.Column="5" Content="none" Click="OnCheckNone" />
                <StackPanel Grid.Column="6" Orientation="Horizontal" Spacing="6">
                    <Button Content="inv" Click="OnInvert" />
                    <Button Content="exp" Click="OnExpandAll" />
                    <Button Content="col" Click="OnCollapseAll" />
                </StackPanel>
            </Grid>

            <Grid Grid.Row="2">
                <ListView x:Name="ResultsList"
                          Style="{StaticResource ResultsListViewStyle}"
                          ItemContainerStyle="{StaticResource ResultsItemContainerStyle}"
                          SelectionMode="Single"
                          ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}"
                          ScrollViewer.VerticalScrollBarVisibility="Auto">
                    <ListView.ItemTemplate>
                        <DataTemplate x:DataType="vm:ResultRow">
                            <Grid Padding="0,2" ColumnSpacing="10">
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
                                <Grid Grid.Column="0">
                                    <CheckBox IsVisible="{x:Bind IsGroupRow, Mode=OneWay}"
                                              IsChecked="{x:Bind IsChecked, Mode=TwoWay}"
                                              HorizontalAlignment="Left" MinWidth="0" Padding="0"
                                              VerticalAlignment="Center" />
                                    <FontIcon Glyph="{x:Bind RoleGlyph, Mode=OneWay}" FontSize="12"
                                              HorizontalAlignment="Center" VerticalAlignment="Center"
                                              Visibility="{x:Bind FileRoleVisibility, Mode=OneWay}"
                                              Foreground="{x:Bind RoleBrush, Mode=OneWay}" />
                                </Grid>
                                <TextBlock Grid.Column="1" Text="{x:Bind NameText}" FontSize="13"
                                           VerticalAlignment="Center"
                                           TextTrimming="CharacterEllipsis"
                                           FontWeight="{x:Bind NameWeight, Mode=OneWay}"
                                           Margin="{x:Bind NameMargin, Mode=OneWay}" />
                                <TextBlock Grid.Column="2" Text="{x:Bind SizeText}" FontSize="12"
                                           Opacity="0.75" HorizontalAlignment="Right"
                                           VerticalAlignment="Center" />
                                <TextBlock Grid.Column="3" Text="{x:Bind CountText}" FontSize="12"
                                           Opacity="0.75" HorizontalAlignment="Right"
                                           VerticalAlignment="Center" />
                                <TextBlock Grid.Column="4" Text="{x:Bind WastedText}" FontSize="12"
                                           Foreground="{ThemeResource AccentTextFillColorPrimaryBrush}"
                                           HorizontalAlignment="Right" VerticalAlignment="Center" />
                                <TextBlock Grid.Column="5" Text="{x:Bind RoleText}" FontSize="11"
                                           Foreground="{x:Bind RoleBrush, Mode=OneWay}"
                                           HorizontalAlignment="Center" VerticalAlignment="Center" />
                                <TextBlock Grid.Column="6" Text="{x:Bind TimeText}" FontSize="11"
                                           Opacity="0.55" VerticalAlignment="Center" />
                                <TextBlock Grid.Column="7" Text="{x:Bind PathText}"
                                           Style="{StaticResource MonoPathStyle}"
                                           VerticalAlignment="Center" Margin="8,0,0,0" />
                            </Grid>
                        </DataTemplate>
                    </ListView.ItemTemplate>
                </ListView>
                <StackPanel x:Name="EmptyState" VerticalAlignment="Center"
                            HorizontalAlignment="Center" Spacing="10" Visibility="Collapsed">
                    <FontIcon Glyph="&#xE721;" FontSize="40" Opacity="0.25" />
                    <TextBlock Text="empty" FontSize="16" FontWeight="SemiBold"
                               HorizontalAlignment="Center" Opacity="0.7" />
                    <TextBlock x:Name="EmptyStateDetail" Style="{StaticResource CaptionStyle}"
                               HorizontalAlignment="Center" MaxWidth="440"
                               TextAlignment="Center" />
                </StackPanel>
            </Grid>

            <Border Grid.Row="3" Padding="0,10,0,10">
                <StackPanel Spacing="6">
                    <Grid ColumnSpacing="12">
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="*" />
                            <ColumnDefinition Width="Auto" />
                        </Grid.ColumnDefinitions>
                        <StackPanel Grid.Column="0" Spacing="4">
                            <TextBlock x:Name="PhaseText" FontSize="12" TextTrimming="CharacterEllipsis" />
                            <TextBlock x:Name="SelectionText" Style="{StaticResource CaptionStyle}" FontSize="11" />
                        </StackPanel>
                        <TextBlock x:Name="SpeedText" Grid.Column="1" FontSize="12"
                                   Opacity="0.7" VerticalAlignment="Center" />
                    </Grid>
                    <Grid x:Name="ScanProgressPanel" Visibility="Collapsed" ColumnSpacing="10">
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="*" />
                            <ColumnDefinition Width="Auto" />
                        </Grid.ColumnDefinitions>
                        <ProgressBar x:Name="ScanProgress" Grid.Column="0" Minimum="0" Maximum="1" />
                        <TextBlock x:Name="ProgressPercent" Grid.Column="1" FontSize="12"
                                   VerticalAlignment="Center" Opacity="0.8" MinWidth="38"
                                   TextAlignment="Right" />
                    </Grid>
                </StackPanel>
            </Border>
"""

# (名称, 正则) —— 命中则删除该块
CUTS = [
    ("头部 Grid(row0)", r'<Grid Grid\.Row="0" Margin="0,0,0,8">.*?</Grid>\s*</StackPanel>'),
    ("工具条 Grid(row1)", r'<Grid Grid\.Row="1" Margin="0,0,0,8" ColumnSpacing="8">.*?</Grid>\s*\n\s*<Grid Grid\.Row="2">'),
    ("结果 ListView", r'<ListView x:Name="ResultsList".*?</ListView>'),
    ("结果 DataTemplate 体", r'<DataTemplate x:DataType="vm:ResultRow">.*?</DataTemplate>'),
    ("空状态", r'<StackPanel x:Name="EmptyState".*?</StackPanel>\s*\n\s*</Grid>'),
    ("进度区 Border", r'<Border Grid\.Row="3" Padding="0,10,0,10">.*?</Border>'),
]


def build() -> bool:
    r = subprocess.run(["dotnet", "build", "-c", "Release", "-v", "q", "--nologo"],
                       cwd=PROBE, env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1200)
    return r.returncode == 0


def main() -> int:
    with open(os.path.join(PROBE, "MainWindow.xaml.cs"), "w",
              encoding="utf-8-sig", newline="") as fh:
        fh.write(STUB_CS)

    def run(body, label):
        with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(SHELL % body)
        ok = build()
        print(f"  {label:<40} -> {'ok' if ok else 'FAIL'}")
        return ok

    if not run(FULL, "完整右栏"):
        print("\n完整右栏就失败，开始逐块删除：\n")
    else:
        print("\n完整右栏能通过（说明问题在右栏之外）")
        return 0

    for name, pattern in CUTS:
        variant, n = re.subn(pattern, "", FULL, count=1, flags=re.S)
        if n == 0:
            print(f"  移除 {name:<34} 未匹配")
            continue
        run(variant, f"移除 {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
