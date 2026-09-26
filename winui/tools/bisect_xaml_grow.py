"""从最小窗口开始，逐段加回内容，定位 XamlCompiler 崩溃点。

比"从完整版删片段"更有效：XamlCompiler 对某些组合会失败，
逐块删除看不出来，但逐块添加能精确定位到引入问题的那一段。
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ = os.path.join(HERE, "src", "DumpSearch.App")
XAML = os.path.join(PROJ, "MainWindow.xaml")
BACKUP = os.path.join(tempfile.gettempdir(), "MainWindow.pristine.xaml")
ENV = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")

HEAD = '''<?xml version="1.0" encoding="utf-8"?>
<Window
    x:Class="DumpSearch.App.MainWindow"
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    xmlns:vm="using:DumpSearch.App.ViewModels"
    Title="Dump Search">
    <Grid x:Name="RootGrid" Background="{StaticResource PageBackgroundBrush}">
        <Grid.RowDefinitions>
            <RowDefinition Height="32" />
            <RowDefinition Height="*" />
        </Grid.RowDefinitions>
        <Grid Grid.Row="0" Background="Transparent">
            <TextBlock x:Name="DragRegion" Text="Dump Search" FontSize="12"
                       Foreground="{StaticResource SecondaryTextBrush}" />
        </Grid>
        <Grid Grid.Row="1">
'''

TAIL = '''        </Grid>
    </Grid>
</Window>
'''

STAGES = [
    ("最小窗口（仅标题栏）", ""),

    ("+ 左栏 ScrollViewer 骨架", '''
        <Border Grid.Column="0" x:Name="ScopePanel" Width="336"
                BorderThickness="0,0,1,0" BorderBrush="{StaticResource PanelStrokeBrush}">
            <ScrollViewer VerticalScrollBarVisibility="Auto">
                <StackPanel Padding="16" Spacing="16">
                    <TextBlock Text="扫描范围" Style="{StaticResource SectionTitleStyle}" />
                </StackPanel>
            </ScrollViewer>
        </Border>
'''),

    ("+ 卡片样式", '''
                    <Border Style="{StaticResource CardStyle}" Padding="12">
                        <StackPanel Spacing="8">
                            <TextBlock Text="筛选条件" Style="{StaticResource SectionTitleStyle}" />
                            <TextBox x:Name="MinSizeBox" Text="1" />
                            <CheckBox x:Name="VerifyCheck" Content="逐字节校验" IsChecked="True" />
                        </StackPanel>
                    </Border>
'''),

    ("+ 盘符 ItemsControl + DataTemplate", '''
                    <ItemsControl x:Name="DriveList"
                                  ItemsSource="{x:Bind ViewModel.Drives, Mode=OneWay}">
                        <ItemsControl.ItemTemplate>
                            <DataTemplate x:DataType="vm:DriveItem">
                                <TextBlock Text="{x:Bind Title}" FontSize="13" />
                            </DataTemplate>
                        </ItemsControl.ItemTemplate>
                    </ItemsControl>
'''),

    ("+ 盘符模板里的两路绑定", '''
                    <ItemsControl x:Name="DriveList2"
                                  ItemsSource="{x:Bind ViewModel.Drives, Mode=OneWay}">
                        <ItemsControl.ItemTemplate>
                            <DataTemplate x:DataType="vm:DriveItem">
                                <CheckBox IsChecked="{x:Bind IsSelected, Mode=TwoWay}" />
                                <TextBlock Text="{x:Bind Subtitle}" />
                            </DataTemplate>
                        </ItemsControl.ItemTemplate>
                    </ItemsControl>
'''),

    ("+ 右栏：标题 + 工具条", '''
            <Grid Grid.Column="1" Margin="16,8,16,0">
                <Grid.RowDefinitions>
                    <RowDefinition Height="Auto" />
                    <RowDefinition Height="Auto" />
                    <RowDefinition Height="*" />
                    <RowDefinition Height="Auto" />
                </Grid.RowDefinitions>
                <Grid Grid.Row="0">
                    <TextBlock Text="重复文件" FontSize="24" FontWeight="SemiBold" />
                    <Button x:Name="CleanButton" Content="预览并清理…"
                            Style="{StaticResource ToolBarButtonStyle}" />
                </Grid>
                <Grid Grid.Row="1">
                    <AutoSuggestBox Width="240" PlaceholderText="筛选" QueryIcon="Find" />
                    <ComboBox x:Name="SortBox" />
                    <ToggleButton x:Name="SortDirToggle" Content="降序" />
                </Grid>
            </Grid>
'''),

    ("+ 结果 ListView + 样式", '''
            <Grid Grid.Column="1">
                <ListView x:Name="ResultsList"
                          Style="{StaticResource ResultsListViewStyle}"
                          ItemContainerStyle="{StaticResource ResultsItemContainerStyle}"
                          SelectionMode="Single"
                          ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}" />
            </Grid>
'''),

    ("+ 结果 DataTemplate 单绑定", '''
            <Grid Grid.Column="1">
                <ListView x:Name="ResultsList"
                          ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}">
                    <ListView.ItemTemplate>
                        <DataTemplate x:DataType="vm:ResultRow">
                            <TextBlock Text="{x:Bind NameText}" />
                        </DataTemplate>
                    </ListView.ItemTemplate>
                </ListView>
            </Grid>
'''),

    ("+ 结果 DataTemplate 全部绑定", '''
            <Grid Grid.Column="1">
                <ListView x:Name="ResultsList"
                          ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}">
                    <ListView.ItemTemplate>
                        <DataTemplate x:DataType="vm:ResultRow">
                            <Grid ColumnSpacing="10">
                                <Grid.ColumnDefinitions>
                                    <ColumnDefinition Width="24" />
                                    <ColumnDefinition Width="*" />
                                </Grid.ColumnDefinitions>
                                <CheckBox Grid.Column="0"
                                          IsVisible="{x:Bind IsGroupRow, Mode=OneWay}"
                                          IsChecked="{x:Bind IsChecked, Mode=TwoWay}" />
                                <FontIcon Grid.Column="0" Glyph="{x:Bind RoleGlyph, Mode=OneWay}"
                                          Visibility="{x:Bind FileRoleVisibility, Mode=OneWay}"
                                          Foreground="{x:Bind RoleBrush, Mode=OneWay}" />
                                <TextBlock Grid.Column="1" Text="{x:Bind NameText}"
                                           FontWeight="{x:Bind NameWeight, Mode=OneWay}"
                                           Margin="{x:Bind NameMargin, Mode=OneWay}"
                                           Foreground="{x:Bind RoleBrush, Mode=OneWay}" />
                            </Grid>
                        </DataTemplate>
                    </ListView.ItemTemplate>
                </ListView>
            </Grid>
'''),
]


def build_ok():
    r = subprocess.run(["dotnet", "build", "-c", "Release", "-v", "q", "--nologo"],
                       cwd=PROJ, env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    return r.returncode == 0


def main():
    if os.path.exists(BACKUP):
        with open(BACKUP, encoding="utf-8-sig") as fh:
            pristine = fh.read()
    else:
        shutil.copy(XAML, BACKUP)
        with open(XAML, encoding="utf-8-sig") as fh:
            pristine = fh.read()

    acc = ""
    for name, chunk in STAGES:
        acc += chunk
        with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(HEAD + acc + TAIL)
        ok = build_ok()
        print(f"  {name:<36} -> {'通过' if ok else '失败'}")
        if not ok:
            print(f"\n>>> 引入问题的片段: {name}")
            keep = os.path.join(tempfile.gettempdir(), "MainWindow.lastgood.xaml")
            shutil.copy(XAML, keep)
            print(f"    当前版本已存: {keep}")
            return 1

    print("\n全部阶段都通过 —— 说明完整版里的某处写法与分段时不同")
    with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(pristine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
