"""WinUI 3 XAML 静态检查：把"编译能过、运行时才崩"的坑挡在构建之前。

这个项目在同一天里撞了三次同一类的坑——XamlCompiler **一声不吭**地放过去，
进程启动时才抛 STATUS_STOWED_RESOURCES(0xC000027B) 崩在 Microsoft.UI.Xaml.dll，
堆栈里没有任何指向 XAML 的信息。三次的根因分别是：

1. ``ColumnDefinition Width="{StaticResource ColXxx}"``，资源是 ``x:Double``。
   GridLength 拿不到值 -> 运行时崩。
2. ``x:Double`` 资源里写 ``*``。星号是 GridLength 的字面量语法，不是合法数字。
3. 给 ``TextBlock`` 设 ``Background`` / ``Padding`` / ``CornerRadius``。
   这三个是 WPF 的 TextBlock 才有的，WinUI 3 没有 -> 编译期就只剩「错误: 1」。

外加一条结构性检查：结果表的列宽在表头和数据行各写一份（原因见 Tokens.xaml
的注释），必须机械校验两处一致，不能靠自觉同步。

用法::

    python tools\\lint_xaml.py           # 有问题返回 1
    python tools\\lint_xaml.py -v        # 打印通过的检查项
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(os.path.join(HERE, "..", "src", "DumpSearch.App"))
TOKENS = os.path.join(APP, "Styles", "Tokens.xaml")
MAIN = os.path.join(APP, "MainWindow.xaml")

# WinUI 3 的 TextBlock 没有这些（WPF 的 TextBlock 才有）
TEXTBLOCK_FORBIDDEN = ("Background", "Padding", "CornerRadius",
                       "BorderBrush", "BorderThickness")

# 允许出现在 x:Double 资源里的值：纯数字（可带小数）
NUMERIC = re.compile(r"^\s*-?\d+(\.\d+)?\s*$")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def ok(self, msg: str) -> None:
        self.passed.append(msg)


def read(path: str) -> str:
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def check_double_resources(tokens: str, rep: Report) -> None:
    """x:Double 资源里不允许出现 '*' 等非数字。"""
    bad = []
    for m in re.finditer(r"<x:Double\s+x:Key=\"([^\"]+)\"\s*>(.*?)</x:Double>",
                         tokens, re.S):
        key, value = m.group(1), m.group(2)
        if not NUMERIC.match(value):
            bad.append(f"{key} = {value.strip()!r}")
    if bad:
        rep.err("x:Double 资源含非数字值（'*' 只在 GridLength 字面量里合法）: "
                + ", ".join(bad))
    else:
        rep.ok("x:Double 资源全为合法数字")


def check_textblock_attrs(main: str, rep: Report) -> None:
    """TextBlock 上不能出现 WPF 遗留属性。"""
    hits = []
    for m in re.finditer(r"<TextBlock\b.*?>", main, re.S):
        tag = m.group(0)
        line = main[:m.start()].count("\n") + 1
        for attr in TEXTBLOCK_FORBIDDEN:
            if re.search(rf"\b{attr}\s*=", tag):
                hits.append(f"第 {line} 行 TextBlock.{attr}")
    if hits:
        rep.err("TextBlock 使用了 WinUI 3 不存在的属性（WPF 遗留）: " + "; ".join(hits))
    else:
        rep.ok("TextBlock 未使用 WPF 遗留属性")


def check_column_widths(main: str, rep: Report) -> None:
    """ColumnDefinition.Width 不能引用资源；且表头与数据行的宽度必须一致。"""
    bad = []
    for m in re.finditer(r"<ColumnDefinition\b[^>]*>", main):
        tag = m.group(0)
        line = main[:m.start()].count("\n") + 1
        if "StaticResource" in tag or "ThemeResource" in tag:
            bad.append(f"第 {line} 行引用了资源（{tag.strip()}）")
    if bad:
        rep.err("ColumnDefinition.Width 引用了资源 —— GridLength 运行时解析不到值就会"
                "抛 0xC000027B: " + "; ".join(bad))
    else:
        rep.ok("ColumnDefinition.Width 全为字面量")

    # 表头网格与数据行网格的列宽必须逐列相等。
    # 只看 CASE 标记圈定的两段——按出现顺序切 8 个一组会把驱动列表、
    # 提示浮层里的无关 Grid 也算进来，误报一片。
    marked = {}
    for name in ("colheader", "rowcols"):
        m = re.search(
            rf"<!--\s*CASE:{name}\s*-->(.*?)<!--\s*/CASE:{name}\s*-->",
            main, re.S)
        if m:
            marked[name] = re.findall(
                r'<ColumnDefinition\s+Width="([^"]+)"', m.group(1))
    if len(marked) < 2:
        rep.ok("结果表列宽标记不全（跳过一致性比对）")
        return
    head, row = marked["colheader"], marked["rowcols"]
    if head != row:
        rep.err("结果表列宽不一致，会导致表头与数据错位:\n"
                f"          表头 {head}\n"
                f"          数据 {row}")
    else:
        rep.ok(f"表头与数据行列宽一致（{len(head)} 列）: {head}")


def check_resource_keys(tokens: str, main: str, rep: Report) -> None:
    """MainWindow 里引用的 StaticResource 必须在 Tokens.xaml 里有定义。"""
    defined = set(re.findall(r'x:Key="([^"]+)"', tokens))
    used = set(re.findall(r"\{StaticResource\s+([A-Za-z_][\w.]*)\s*\}", main))
    missing = sorted(used - defined)
    if missing:
        rep.err("MainWindow 引用了 Tokens.xaml 未定义的 StaticResource: "
                + ", ".join(missing))
    else:
        rep.ok(f"{len(used)} 个 StaticResource 引用全部有定义")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true", help="打印通过的检查项")
    args = ap.parse_args()

    for p in (TOKENS, MAIN):
        if not os.path.exists(p):
            print(f"[错误] 找不到 {p}")
            return 2

    tokens, main = read(TOKENS), read(MAIN)
    rep = Report()

    check_double_resources(tokens, rep)
    check_textblock_attrs(main, rep)
    check_column_widths(main, rep)
    check_resource_keys(tokens, main, rep)

    if args.verbose:
        for p in rep.passed:
            print(f"  PASS  {p}")
    if rep.errors:
        print()
        print("XAML 检查未通过：")
        for e in rep.errors:
            print(f"  FAIL  {e}")
        return 1
    print(f"XAML 检查全部通过（{len(rep.passed)} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
