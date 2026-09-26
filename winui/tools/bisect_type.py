"""x:DataType 指向 ResultRow 时，即使空模板也失败；
DriveItem 的同款模板却能通过。所以问题出在 **类型本身**。

做法：同步最新源码到 Probe，然后逐步删掉 ResultRow 的属性，
找出是哪一个属性的类型让 XamlCompiler 生成类型信息时失败。
"""

import glob
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(HERE, "src", "DumpSearch.App")
PROBE = os.path.join(HERE, "Probe")
XAML = os.path.join(PROBE, "MainWindow.xaml")
VM = os.path.join(PROBE, "ViewModels", "ResultViewModel.cs")
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

SHELL_DRIVE = """<?xml version="1.0" encoding="utf-8"?>
<Window
    x:Class="Probe.MainWindow"
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    xmlns:vm="using:Probe.ViewModels"
    Title="datatype">
    <Grid>
        <ListView ItemsSource="{x:Bind ViewModel.Drives, Mode=OneWay}">
            <ListView.ItemTemplate>
                <DataTemplate x:DataType="vm:%s">
%s
                </DataTemplate>
            </ListView.ItemTemplate>
        </ListView>
    </Grid>
</Window>
"""

# ResultRow 里可疑的属性（类型越"重"越可疑）
SUSPECTS = [
    "public Brush RoleBrush =>",
    "public Windows.UI.Text.FontWeight NameWeight =>",
    "public Thickness NameMargin =>",
    "public Visibility FileRoleVisibility =>",
    "public DuplicateGroup Group { get; init; } = new()",
    "public FileEntry? File { get; init; }",
    "private static readonly Brush Transparent",
    "private static readonly Brush DangerBrush",
    "private static readonly Brush KeepBrush",
    "private static readonly Windows.UI.Text.FontWeight Bold",
    "private static readonly Windows.UI.Text.FontWeight Normal",
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


def strip(patterns):
    """把包含指定模式的整行/整块注释掉。"""
    with open(VM, encoding="utf-8-sig") as fh:
        text = fh.read()
    for p in patterns:
        text = re.sub(r"(?m)^[ \t]*//\s*STRIP:[^\n]*\n", "", text)
        lines = text.splitlines(keepends=True)
        out, skip = [], False
        for line in lines:
            if p in line and not line.lstrip().startswith("//"):
                if line.rstrip().endswith("=>") or line.rstrip().endswith(";") \
                        or line.rstrip().endswith("}"):
                    out.append("// STRIP:" + line)
                    continue
                skip = True
            if skip:
                if line.rstrip().endswith(";"):
                    skip = False
                continue
            out.append(line)
        text = "".join(out)
    with open(VM, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(text)


def run(datatype, body, label):
    with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(SHELL_DRIVE % (datatype, body))
    print(f"  {label:<52} -> {'ok' if build() else 'FAIL'}")


def main() -> int:
    sync()
    with open(os.path.join(PROBE, "MainWindow.xaml.cs"), "w",
              encoding="utf-8-sig", newline="") as fh:
        fh.write(STUB_CS)

    empty = '                    <TextBlock Text="x" />\n'
    run("DriveItem", empty, "基线: DriveItem 空模板")
    run("ResultRow", empty, "基线: ResultRow 空模板")

    print("\n逐个剥离 ResultRow 的属性：")
    removed = []
    for pat in SUSPECTS:
        with open(VM, encoding="utf-8-sig") as fh:
            backup = fh.read()
        strip([pat])
        with open(XAML, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(SHELL_DRIVE % ("ResultRow", empty))
        ok = build()
        print(f"  剥离 {pat[:44]:<46} -> {'ok ✅' if ok else '仍失败'}")
        if ok:
            print(f"\n>>> 就是这个属性: {pat}")
            return 0
        # 不还原：继续在已剥离的基础上试下一个（累积剥离）
    print("\n全部剥离后仍失败")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
