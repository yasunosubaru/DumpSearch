"""给 .cs / .xaml / .csproj 等源文件补 UTF-8 BOM。

背景：csc 在没有 BOM 时会按系统 ANSI 代码页（本机是 GBK）尝试解码源文件，
中文注释里的字节会被误解码，进而把后面的 '{' 之类判成"意外字符"，
报出与真实原因毫无关系的 CS1056。XAML 编译器同理。
加上 BOM 后解码方式就确定了，错误信息也随之变得可读。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOM = b"\xef\xbb\xbf"
EXTS = {".cs", ".xaml", ".csproj", ".props", ".targets", ".config", ".json", ".xml"}


def main() -> int:
    changed, checked = [], 0
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in ("bin", "obj", ".vs", ".git")]
        for name in files:
            if os.path.splitext(name)[1].lower() not in EXTS:
                continue
            path = os.path.join(base, name)
            with open(path, "rb") as fh:
                data = fh.read()
            checked += 1
            if data.startswith(BOM):
                continue
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                print(f"跳过（非 UTF-8）: {os.path.relpath(path, ROOT)}")
                continue
            with open(path, "wb") as fh:
                fh.write(BOM + data)
            changed.append(os.path.relpath(path, ROOT))

    for c in changed:
        print(f"已加 BOM: {c}")
    print(f"\n检查 {checked} 个文件，补 BOM {len(changed)} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
