"""在干净的 Probe 镜像里逐段加回 MainWindow.xaml，定位 XamlCompiler 失败的写法。

相比之前的版本，这次做了三件让结论可信的事：
  1. Probe 目录彻底清理，只保留必要文件（之前残留的 Core/ViewModels/Styles
     造成了一连串假失败）。
  2. 代码后置的桩里**补齐全部事件处理方法**（Click / SelectionChanged /
     TextChanged / DragCompleted），否则 xaml:Click 指向不存在的方法会直接
     让 XamlCompiler 失败，测出来的结论全是假的。
  3. 每个 stage 都是完整闭合的元素，挂在同一个容器 Grid 下。
"""

import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(HERE, "src", "DumpSearch.App")
PROBE = os.path.join(HERE, "Probe")
ENV = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")

STUB_CS = """using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Probe.ViewModels;

namespace Probe;

// 桩：补齐 MainWindow.xaml 里用到的全部事件处理方法 + x:Bind 的根对象。
// 缺任何一个，xaml:Click="OnXxx" 或 x:Bind ViewModel.X 都会让
// XamlCompiler 失败（且不报任何错误信息），测出来的结论全是假的。
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
"""

STUB_APP_CS = """using Microsoft.UI.Xaml;

namespace Probe;

public partial class App : Application
{
    public App() { InitializeComponent(); }
    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        var w = new MainWindow();
        w.Activate();
    }
}
"""

APP_XAML = """<?xml version="1.0" encoding="utf-8"?>
<Application
    x:Class="Probe.App"
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
    <Application.Resources>
        <ResourceDictionary>
            <ResourceDictionary.MergedDictionaries>
                <XamlControlsResources xmlns="using:Microsoft.UI.Xaml.Controls" />
                <ResourceDictionary Source="ms-appx:///Styles/Tokens.xaml" />
            </ResourceDictionary.MergedDictionaries>
        </ResourceDictionary>
    </Application.Resources>
</Application>
"""

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


def sync_sources() -> None:
    """把真实项目的 Core/ViewModels/Styles 拷到 Probe 并改命名空间。"""
    for sub in ("Core", "ViewModels", "Styles"):
        dst = os.path.join(PROBE, sub)
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(os.path.join(APP, sub), dst)

    for cs in glob.glob(os.path.join(PROBE, "Core", "**", "*.cs"), recursive=True) + \
              glob.glob(os.path.join(PROBE, "ViewModels", "**", "*.cs"), recursive=True):
        with open(cs, encoding="utf-8-sig") as fh:
            text = fh.read()
        text = (text
                .replace("namespace DumpSearch.App.Core", "namespace Probe.Core")
                .replace("namespace DumpSearch.App.ViewModels", "namespace Probe.ViewModels")
                .replace("using DumpSearch.App.Core", "using Probe.Core"))
        with open(cs, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(text)

    shutil.copy2(os.path.join(APP, "Styles", "Tokens.xaml"),
                 os.path.join(PROBE, "Styles", "Tokens.xaml"))


def write(name, text) -> None:
    with open(os.path.join(PROBE, name), "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(text)


def build() -> bool:
    r = subprocess.run(["dotnet", "build", "-c", "Release", "-v", "q", "--nologo"],
                       cwd=PROBE, env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1200)
    return r.returncode == 0


# 从真实 MainWindow.xaml 里抽出"行/块"，按出现顺序逐个加回。
def real_blocks():
    """抽出 <Grid Grid.Row="1"> 内部的顶层块（按 Grid 列的兄弟顺序）。

    用标签深度扫描找 body Grid 的结束位置，不用脆弱的字符串标记
    （之前用 `</Grid>\\n    </Grid>` 定位，文件里根本没有这个字符串，
    结果切出空内容，测出来的"全部通过"毫无意义）。
    """
    import re
    with open(os.path.join(APP, "MainWindow.xaml"), encoding="utf-8-sig") as fh:
        text = fh.read()
    lines = text.splitlines()

    # body Grid 的起始行：缩进 8 空格 + <Grid Grid.Row="1" ...>
    start = None
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("<Grid Grid.Row=\"1\""):
            continue
        if len(line) - len(line.lstrip()) == 8:
            start = i
            break
    if start is None:
        return []

    depth = 0
    end = None
    # 注意：必须用前瞻 (?=[\s>/])，否则 <Grid.ColumnDefinitions> 也会被算成
    # <Grid 的开标签，深度永远回不到 0，end 就一直是 None。
    open_re = re.compile(r"<Grid(?=[\s>/])")
    close_re = re.compile(r"</Grid>")
    for i in range(start, len(lines)):
        line = lines[i]
        depth += len(open_re.findall(line)) - len(close_re.findall(line))
        if depth == 0:
            end = i
            break
    if end is None:
        return []

    body = lines[start + 1:end]
    # 按 12 空格缩进的"顶层元素起点"切块；跳掉以闭合标签开头的碎片，
    # 保证每一块自身标签平衡，可以单独放进 XAML 编译。
    chunks, cur = [], []
    for line in body:
        is_top = line.startswith("            <") and not line.strip().startswith("</")
        if is_top and cur:
            chunks.append("\n".join(cur))
            cur = [line]
        elif is_top:
            cur = [line]
        else:
            if cur:
                cur.append(line)
    if cur:
        chunks.append("\n".join(cur))
    return [c for c in chunks if c.strip()]


def main() -> int:
    shutil.rmtree(os.path.join(PROBE, "obj"), ignore_errors=True)
    shutil.rmtree(os.path.join(PROBE, "bin"), ignore_errors=True)
    for f in glob.glob(os.path.join(PROBE, "*.bak")):
        os.remove(f)

    sync_sources()
    write("MainWindow.xaml.cs", STUB_CS)
    write("App.xaml.cs", STUB_APP_CS)
    write("App.xaml", APP_XAML)
    write("MainWindow.xaml", SHELL % "")
    print(f"  {'(empty body)':<44} -> {'ok' if build() else 'FAIL'}")
    if not build():
        print("壳子/base 就有问题，先看这里")
        return 1

    acc = ""
    for i, block in enumerate(real_blocks(), 1):
        acc += "\n" + block
        write("MainWindow.xaml", SHELL % acc)
        ok = build()
        head = " ".join(block.strip().split()[:4])[:42]
        print(f"  block {i:>2}: {head:<44} -> {'ok' if ok else 'FAIL'}")
        if not ok:
            keep = os.path.join(HERE, ".bisect_stash", f"culprit_block{i}.xaml")
            os.makedirs(os.path.dirname(keep), exist_ok=True)
            with open(keep, "w", encoding="utf-8-sig", newline="") as fh:
                fh.write(SHELL % acc)
            print(f"\n>>> culprit block {i}, saved: {keep}")
            return 1

    print("\n所有块都能通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
