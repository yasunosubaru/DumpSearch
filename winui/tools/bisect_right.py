"""bisect block 7（右侧结果区）内部，找出下一个让 XamlCompiler 失败的写法。"""

import os
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
    Title="right side">
    <Grid>
        <Grid.ColumnDefinitions>
            <ColumnDefinition Width="Auto" />
            <ColumnDefinition Width="*" />
        </Grid.ColumnDefinitions>
%s
    </Grid>
</Window>
"""

RIGHT_OPEN = """        <Grid Grid.Column="1" Margin="16,8,16,0">
            <Grid.RowDefinitions>
                <RowDefinition Height="Auto" />
                <RowDefinition Height="Auto" />
                <RowDefinition Height="*" />
                <RowDefinition Height="Auto" />
            </Grid.RowDefinitions>
"""
RIGHT_CLOSE = """        </Grid>
"""

VARIANTS = [
    ("R0 空右栏", RIGHT_OPEN + RIGHT_CLOSE),

    ("R1 标题 + 摘要", RIGHT_OPEN + """
            <StackPanel Grid.Row="0">
                <TextBlock Text="dup" FontSize="24" FontWeight="SemiBold" />
                <TextBlock x:Name="SummaryText" Style="{StaticResource CaptionStyle}" />
            </StackPanel>
""" + RIGHT_CLOSE),

    ("R2 标题 + 三个按钮(Collapsed)", RIGHT_OPEN + """
            <Grid Grid.Row="0">
                <StackPanel>
                    <TextBlock Text="dup" FontSize="24" FontWeight="SemiBold" />
                    <TextBlock x:Name="SummaryText" Style="{StaticResource CaptionStyle}" />
                </StackPanel>
                <StackPanel Orientation="Horizontal" Spacing="8">
                    <Button x:Name="ExportCsvButton" Content="csv" Click="OnExportCsv"
                            Visibility="Collapsed" />
                    <Button x:Name="ExportScriptButton" Content="ps" Click="OnExportScript"
                            Visibility="Collapsed" />
                    <Button x:Name="CleanButton" Content="clean" Click="OnPreviewClean"
                            Style="{StaticResource ToolBarButtonStyle}"
                            Visibility="Collapsed" />
                </StackPanel>
            </Grid>
""" + RIGHT_CLOSE),

    ("R3 工具条 AutoSuggest", RIGHT_OPEN + """
            <Grid Grid.Row="1" ColumnSpacing="8">
                <AutoSuggestBox Width="240" PlaceholderText="f"
                                QueryIcon="Find" TextChanged="OnFilterChanged" />
            </Grid>
""" + RIGHT_CLOSE),

    ("R4 工具条 ComboBox + ToggleButton", RIGHT_OPEN + """
            <Grid Grid.Row="1" ColumnSpacing="8">
                <ComboBox x:Name="SortBox" SelectionChanged="OnSortChanged" />
                <ToggleButton x:Name="SortDirToggle" Content="desc"
                              Click="OnSortDirectionToggle" />
            </Grid>
""" + RIGHT_CLOSE),

    ("R5 工具条 7 列定义", RIGHT_OPEN + """
            <Grid Grid.Row="1" ColumnSpacing="8">
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="Auto" />
                    <ColumnDefinition Width="*" />
                    <ColumnDefinition Width="Auto" />
                    <ColumnDefinition Width="Auto" />
                    <ColumnDefinition Width="Auto" />
                    <ColumnDefinition Width="Auto" />
                    <ColumnDefinition Width="Auto" />
                </Grid.ColumnDefinitions>
            </Grid>
""" + RIGHT_CLOSE),

    ("R6 结果 ListView（无模板）", RIGHT_OPEN + """
            <ListView x:Name="ResultsList" Grid.Row="2"
                      Style="{StaticResource ResultsListViewStyle}"
                      ItemContainerStyle="{StaticResource ResultsItemContainerStyle}"
                      SelectionMode="Single"
                      ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}"
                      ScrollViewer.VerticalScrollBarVisibility="Auto" />
""" + RIGHT_CLOSE),

    ("R7 结果模板 纯文本绑定", RIGHT_OPEN + """
            <ListView x:Name="ResultsList" Grid.Row="2"
                      ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}">
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
""" + RIGHT_CLOSE),

    ("R8 结果模板 + 8 列定义", RIGHT_OPEN + """
            <ListView x:Name="ResultsList" Grid.Row="2"
                      ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}">
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
                            <TextBlock Grid.Column="1" Text="{x:Bind NameText}" />
                        </Grid>
                    </DataTemplate>
                </ListView.ItemTemplate>
            </ListView>
""" + RIGHT_CLOSE),

    ("R9 结果模板 + CheckBox TwoWay", RIGHT_OPEN + """
            <ListView x:Name="ResultsList" Grid.Row="2"
                      ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}">
                <ListView.ItemTemplate>
                    <DataTemplate x:DataType="vm:ResultRow">
                        <Grid>
                            <CheckBox IsVisible="{x:Bind IsGroupRow, Mode=OneWay}"
                                      IsChecked="{x:Bind IsChecked, Mode=TwoWay}" />
                        </Grid>
                    </DataTemplate>
                </ListView.ItemTemplate>
            </ListView>
""" + RIGHT_CLOSE),

    ("R10 结果模板 + FontIcon Glyph", RIGHT_OPEN + """
            <ListView x:Name="ResultsList" Grid.Row="2"
                      ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}">
                <ListView.ItemTemplate>
                    <DataTemplate x:DataType="vm:ResultRow">
                        <Grid>
                            <FontIcon Glyph="{x:Bind RoleGlyph, Mode=OneWay}" FontSize="12" />
                        </Grid>
                    </DataTemplate>
                </ListView.ItemTemplate>
            </ListView>
""" + RIGHT_CLOSE),

    ("R11 结果模板 + Visibility/Brush 绑定", RIGHT_OPEN + """
            <ListView x:Name="ResultsList" Grid.Row="2"
                      ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}">
                <ListView.ItemTemplate>
                    <DataTemplate x:DataType="vm:ResultRow">
                        <Grid>
                            <FontIcon Visibility="{x:Bind FileRoleVisibility, Mode=OneWay}"
                                      Foreground="{x:Bind RoleBrush, Mode=OneWay}" />
                        </Grid>
                    </DataTemplate>
                </ListView.ItemTemplate>
            </ListView>
""" + RIGHT_CLOSE),

    ("R12 结果模板 + FontWeight/Thickness 绑定", RIGHT_OPEN + """
            <ListView x:Name="ResultsList" Grid.Row="2"
                      ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}">
                <ListView.ItemTemplate>
                    <DataTemplate x:DataType="vm:ResultRow">
                        <Grid>
                            <TextBlock Text="{x:Bind NameText}"
                                       FontWeight="{x:Bind NameWeight, Mode=OneWay}"
                                       Margin="{x:Bind NameMargin, Mode=OneWay}" />
                        </Grid>
                    </DataTemplate>
                </ListView.ItemTemplate>
            </ListView>
""" + RIGHT_CLOSE),

    ("R13 空状态", RIGHT_OPEN + """
            <StackPanel x:Name="EmptyState" Grid.Row="2" VerticalAlignment="Center"
                        HorizontalAlignment="Center" Spacing="10" Visibility="Collapsed">
                <FontIcon Glyph="&#xE721;" FontSize="40" Opacity="0.25" />
                <TextBlock Text="empty" FontSize="16" FontWeight="SemiBold" />
                <TextBlock x:Name="EmptyStateDetail" Style="{StaticResource CaptionStyle}" />
            </StackPanel>
""" + RIGHT_CLOSE),

    ("R14 进度区", RIGHT_OPEN + """
            <Border Grid.Row="3" Padding="0,10,0,10">
                <StackPanel Spacing="6">
                    <Grid ColumnSpacing="12">
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="*" />
                            <ColumnDefinition Width="Auto" />
                        </Grid.ColumnDefinitions>
                        <StackPanel Grid.Column="0">
                            <TextBlock x:Name="PhaseText" FontSize="12" />
                            <TextBlock x:Name="SelectionText"
                                       Style="{StaticResource CaptionStyle}" FontSize="11" />
                        </StackPanel>
                        <TextBlock x:Name="SpeedText" Grid.Column="1" FontSize="12" />
                    </Grid>
                    <Grid x:Name="ScanProgressPanel" Visibility="Collapsed" ColumnSpacing="10">
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="*" />
                            <ColumnDefinition Width="Auto" />
                        </Grid.ColumnDefinitions>
                        <ProgressBar x:Name="ScanProgress" Grid.Column="0" Minimum="0" Maximum="1" />
                        <TextBlock x:Name="ProgressPercent" Grid.Column="1" MinWidth="38"
                                   TextAlignment="Right" />
                    </Grid>
                </StackPanel>
            </Border>
""" + RIGHT_CLOSE),
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
    bad = []
    for name, body in VARIANTS:
        with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(SHELL % body)
        ok = build()
        print(f"  {name:<40} -> {'ok' if ok else 'FAIL'}")
        if not ok:
            bad.append(name)
    print()
    if bad:
        print("失败:", bad)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
