"""列出 MainWindow.xaml / App.xaml 里引用的所有资源键，并标出哪些没有定义。

XamlCompiler 对"引用了不存在的资源键"只会报无信息的「错误: 1」，
不给出键名，所以必须静态核对一遍。
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# WinUI 3 内置样式/画刷键（只列本项目用得到的）
BUILTIN_STYLES = {
    "AccentButtonStyle", "TextButtonStyle", "SubtleButtonStyle",
    "LinkButtonStyle", "StandardButtonStyle", "RevealButtonStyle",
    "CardButtonStyle", "IconButtonStyle", "DropDownButtonStyle",
    "SplitButtonStyle", "ToggleButtonStyle", "CheckBoxStyle",
    "RadioButtonStyle", "ToolTipStyle", "FlyoutStyle",
    "ListViewItemStyle", "GridViewItemStyle", "HeaderTextBlockStyle",
    "SubtitleTextBlockStyle", "BodyTextBlockStyle", "CaptionTextBlockStyle",
    "TitleTextBlockStyle", "BaseButtonStyle", "DefaultListViewItemStyle",
    "NavigationViewItemStyle", "ScrollViewerStyle",
}

BUILTIN_BRUSHES = {
    "ApplicationPageBackgroundThemeBrush", "LayerFillColorDefaultBrush",
    "CardStrokeColorDefaultBrush", "CardBackgroundFillColorDefaultBrush",
    "DividerStrokeColorDefaultBrush", "TextFillColorPrimaryBrush",
    "TextFillColorSecondaryBrush", "TextFillColorTertiaryBrush",
    "SystemFillColorCriticalBrush", "SystemFillColorCautionBrush",
    "SystemFillColorSuccessBrush", "AccentTextFillColorPrimaryBrush",
    "ControlFillColorDefaultBrush", "SolidBackgroundFillColorBaseBrush",
    "SystemControlBackgroundListLowBrush", "ControlStrokeColorDefaultBrush",
}


def keys_in(path):
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    out = []
    for m in re.finditer(r"\{(Static|Theme)Resource\s+([A-Za-z0-9_.]+)\}", text):
        out.append((m.group(1), m.group(2)))
    return out


def defined_in(path):
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    return set(re.findall(r'x:Key="([A-Za-z0-9_.]+)"', text))


def main() -> int:
    tokens = os.path.join(HERE, "src", "DumpSearch.App", "Styles", "Tokens.xaml")
    win = os.path.join(HERE, "src", "DumpSearch.App", "MainWindow.xaml")
    app = os.path.join(HERE, "src", "DumpSearch.App", "App.xaml")

    local = defined_in(tokens)
    print(f"Tokens.xaml 定义了 {len(local)} 个键\n")

    unknown = []
    for path in (win, app):
        print(f"--- {os.path.basename(path)} ---")
        for kind, key in keys_in(path):
            if key in local:
                state = "本地定义"
            elif key in (BUILTIN_STYLES if kind == "Static" else BUILTIN_BRUSHES):
                state = "WinUI 内置(推测)"
            else:
                state = "!! 未定义"
                unknown.append((os.path.basename(path), kind, key))
            print(f"  {kind}Resource {key:<42} {state}")
        print()

    # Tokens.xaml 自己引用的 ThemeResource
    print("--- Tokens.xaml 内部引用 ---")
    for kind, key in keys_in(tokens):
        state = "已知" if key in BUILTIN_BRUSHES else "!! 可能不存在"
        if key not in BUILTIN_BRUSHES:
            unknown.append(("Tokens.xaml", kind, key))
        print(f"  {kind}Resource {key:<42} {state}")

    print()
    if unknown:
        print("!! 以下资源键可能不存在（XamlCompiler 只会报「错误: 1」）：")
        for f, k, key in unknown:
            print(f"   {f}: {{{k}Resource {key}}}")
        return 1
    print("所有资源键都能解析")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
