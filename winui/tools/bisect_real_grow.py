"""在真实项目里逐段加回 MainWindow.xaml，定位让 XamlCompiler 失败的写法。

设计要点：每个 stage 都是**完整闭合**的元素，统一塞进一个容器 Grid 里。
（前一版把片段直接拼到同一个父级下，产生了未闭合标签，
测出来的"失败"全是假的。）

前提：代码后置用桩（不引用任何 x:Name），Core/ViewModels/Tokens 全部就位，
所以每次构建的成败只反映刚加进来的那段 XAML。
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ = os.path.join(HERE, "src", "DumpSearch.App")
XAML = os.path.join(PROJ, "MainWindow.xaml")
ENV = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")

SHELL = """<?xml version="1.0" encoding="utf-8"?>
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
            <TextBlock Text="Dump Search" FontSize="12"
                       Foreground="{StaticResource SecondaryTextBrush}" />
        </Grid>
        <Grid x:Name="Body" Grid.Row="1">
%s
        </Grid>
    </Grid>
</Window>
"""

STAGES = [
    ("left border + scroller + section", """
            <Border Grid.Column="0" Width="336" BorderThickness="0,0,1,0"
                    BorderBrush="{StaticResource PanelStrokeBrush}">
                <ScrollViewer VerticalScrollBarVisibility="Auto">
                    <StackPanel Padding="16" Spacing="16">
                        <TextBlock Text="scope" Style="{StaticResource SectionTitleStyle}" />
                    </StackPanel>
                </ScrollViewer>
            </Border>
"""),
    ("link buttons row", """
            <Border Grid.Column="0" Width="336">
                <StackPanel Orientation="Horizontal">
                    <Button Content="all" Click="OnSelectAllDrives"
                            Style="{StaticResource LinkButtonStyle}"
                            Padding="8,2" FontSize="12" />
                    <Button Content="none" Click="OnSelectNoDrives"
                            Style="{StaticResource LinkButtonStyle}"
                            Padding="8,2" FontSize="12" />
                </StackPanel>
            </Border>
"""),
    ("card + inputs + checkboxes", """
            <Border Grid.Column="0" Width="336" Style="{StaticResource CardStyle}" Padding="12">
                <StackPanel Spacing="10">
                    <TextBlock Text="filter" Style="{StaticResource SectionTitleStyle}" />
                    <TextBox Text="1" />
                    <TextBox PlaceholderText="none" />
                    <CheckBox Content="verify" IsChecked="True" />
                    <CheckBox Content="hardlink" IsChecked="True" />
                </StackPanel>
            </Border>
"""),
    ("mono-font TextBox", """
            <Border Grid.Column="0" Width="336">
                <TextBox AcceptsReturn="True"
                         FontFamily="{StaticResource MonoFontFamily}"
                         FontSize="12" Height="64" PlaceholderText="D:\\Backup" />
            </Border>
"""),
    ("keep-rule card", """
            <Border Grid.Column="0" Width="336" Style="{StaticResource CardStyle}" Padding="12">
                <StackPanel Spacing="8">
                    <TextBlock Text="keep" Style="{StaticResource SectionTitleStyle}" />
                    <ComboBox HorizontalAlignment="Stretch"
                              SelectionChanged="OnKeepRuleChanged" />
                    <TextBlock Style="{StaticResource CaptionStyle}" FontSize="11" />
                </StackPanel>
            </Border>
"""),
    ("start/cancel buttons", """
            <Border Grid.Column="0" Width="336">
                <StackPanel>
                    <Button Content="start" Click="OnStartScan"
                            Style="{StaticResource ToolBarButtonStyle}"
                            HorizontalAlignment="Stretch"
                            HorizontalContentAlignment="Center" MinHeight="36" />
                    <Button Content="stop" HorizontalAlignment="Stretch"
                            HorizontalContentAlignment="Center"
                            Visibility="Collapsed" Click="OnCancelScan" />
                </StackPanel>
            </Border>
"""),
    ("drive ItemsControl (text only)", """
            <ItemsControl Grid.Column="0" Width="336"
                          ItemsSource="{x:Bind ViewModel.Drives, Mode=OneWay}">
                <ItemsControl.ItemTemplate>
                    <DataTemplate x:DataType="vm:DriveItem">
                        <TextBlock Text="{x:Bind Title}" FontSize="13" />
                    </DataTemplate>
                </ItemsControl.ItemTemplate>
            </ItemsControl>
"""),
    ("drive template + bool binding", """
            <ItemsControl Grid.Column="0" Width="336"
                          ItemsSource="{x:Bind ViewModel.Drives, Mode=OneWay}">
                <ItemsControl.ItemTemplate>
                    <DataTemplate x:DataType="vm:DriveItem">
                        <CheckBox IsChecked="{x:Bind IsSelected, Mode=TwoWay}" />
                        <TextBlock Text="{x:Bind Subtitle}"
                                   Style="{StaticResource CaptionStyle}" />
                        <TextBlock Text="{x:Bind FreeText}" />
                    </DataTemplate>
                </ItemsControl.ItemTemplate>
            </ItemsControl>
"""),
    ("thumb splitter", """
            <Thumb Grid.Column="0" Width="6" HorizontalAlignment="Right" Opacity="0"
                   Cursor="SizeWestEast" DragCompleted="OnSplitterDragCompleted">
                <Thumb.Resources>
                    <SolidColorBrush x:Key="ThumbBackground" Color="Transparent" />
                </Thumb.Resources>
            </Thumb>
"""),
    ("right header + clean button", """
            <Grid Grid.Column="1" Margin="16,8,16,0">
                <StackPanel>
                    <TextBlock Text="dup" FontSize="24" FontWeight="SemiBold" />
                    <TextBlock Style="{StaticResource CaptionStyle}" />
                    <Button Content="clean" Click="OnPreviewClean"
                            Style="{StaticResource ToolBarButtonStyle}" />
                </StackPanel>
            </Grid>
"""),
    ("toolbar: autosuggest + combo + toggle", """
            <Grid Grid.Column="1" ColumnSpacing="8">
                <AutoSuggestBox Width="240" PlaceholderText="filter"
                                QueryIcon="Find" TextChanged="OnFilterChanged" />
                <ComboBox Width="150" SelectionChanged="OnSortChanged" />
                <ToggleButton Content="desc" Click="OnSortDirectionToggle" />
            </Grid>
"""),
    ("results ListView (no template)", """
            <ListView Grid.Column="1" Style="{StaticResource ResultsListViewStyle}"
                      ItemContainerStyle="{StaticResource ResultsItemContainerStyle}"
                      SelectionMode="Single"
                      ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}"
                      ScrollViewer.VerticalScrollBarVisibility="Auto" />
"""),
    ("results template: text bindings", """
            <ListView Grid.Column="1"
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
"""),
    ("results template: bool/enum/struct", """
            <ListView Grid.Column="1"
                      ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}">
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
"""),
    ("results template: FontIcon + styled path", """
            <ListView Grid.Column="1"
                      ItemsSource="{x:Bind ViewModel.Rows, Mode=OneWay}">
                <ListView.ItemTemplate>
                    <DataTemplate x:DataType="vm:ResultRow">
                        <Grid>
                            <FontIcon Glyph="{x:Bind RoleGlyph, Mode=OneWay}" FontSize="12"
                                      Visibility="{x:Bind FileRoleVisibility, Mode=OneWay}"
                                      Foreground="{x:Bind RoleBrush, Mode=OneWay}" />
                            <TextBlock Text="{x:Bind PathText}"
                                       Style="{StaticResource MonoPathStyle}" />
                        </Grid>
                    </DataTemplate>
                </ListView.ItemTemplate>
            </ListView>
"""),
    ("empty state + progress + infobar", """
            <StackPanel Grid.Column="1" VerticalAlignment="Center"
                        HorizontalAlignment="Center" Spacing="10" Visibility="Collapsed">
                <FontIcon Glyph="&#xE721;" FontSize="40" Opacity="0.25" />
                <TextBlock Text="empty" FontSize="16" FontWeight="SemiBold" />
                <TextBlock Style="{StaticResource CaptionStyle}" />
            </StackPanel>
            <ProgressBar Grid.Column="1" Minimum="0" Maximum="1" />
            <InfoBar Grid.Row="1" VerticalAlignment="Bottom" IsOpen="False" IsClosable="True" />
"""),
]


def build():
    r = subprocess.run(["dotnet", "build", "-c", "Release", "-v", "q", "--nologo"],
                       cwd=PROJ, env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1200)
    return r.returncode == 0


def main() -> int:
    # 先用空 body 验证壳子本身没问题
    with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(SHELL % "")
    print(f"  {'(empty body shell)':<40} -> {'ok' if build() else 'FAIL'}")
    if not build():
        print("壳子本身就有问题")
        return 1

    acc = ""
    for name, chunk in STAGES:
        acc += chunk
        with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(SHELL % acc)
        ok = build()
        print(f"  {name:<40} -> {'ok' if ok else 'FAIL'}")
        if not ok:
            print(f"\n>>> culprit: {name}")
            keep = os.path.join(HERE, ".bisect_stash", "culprit_real.xaml")
            os.makedirs(os.path.dirname(keep), exist_ok=True)
            with open(keep, "w", encoding="utf-8-sig", newline="") as fh:
                fh.write(SHELL % acc)
            print(f"    saved: {keep}")
            return 1

    print("\nall stages ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
