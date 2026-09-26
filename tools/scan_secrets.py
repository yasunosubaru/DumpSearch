"""提交前的敏感信息扫描：确认暂存区里没有会公开出去的东西。

为什么要单独写一个脚本：仓库要公开推送，而 Windows 工具特别容易在
不经意间把本机信息写进文件里——

- ``.lnk`` 快捷方式里嵌的是工作目录和目标 exe 的**绝对路径**
  （``V:\\Developer\\...``），提交上去既泄露目录结构，又在别人机器上必然失效；
- 截图里能看到 ``%TEMP%`` 展开后的 ``C:\\Users\\<用户名>\\...``；
- 测试脚本用 ``os.environ.get("TEMP", r"C:\\Users\\<用户名>\\AppData\\Local\\Temp")``
  做兜底默认值，等于把用户名写进公开仓库的源码。

只扫描**暂存区**（不是工作区）：要问的问题是"git 提交里有什么"。

用法::

    python tools\\scan_secrets.py            # 扫描暂存区
    python tools\\scan_secrets.py --staged  # 同上（显式）
    python tools\\scan_secrets.py --all      # 扫整个工作区，忽略 .gitignore
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# (标签, 正则)。全部按字节匹配，忽略编码差异。
PATTERNS: list[tuple[str, re.Pattern[bytes]]] = [
    ("Windows 用户名", re.compile(rb"C:\\Users\\(?!WindowsApps)[A-Za-z0-9_.-]+", re.I)),
    ("Unix 用户目录", re.compile(rb"/home/[A-Za-z0-9_.-]+", re.I)),
    ("邮箱", re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("GitHub 令牌", re.compile(rb"gh[pousr]_[A-Za-z0-9]{16,}")),
    ("GitHub PAT", re.compile(rb"github_pat_[A-Za-z0-9_]{20,}")),
    ("AWS AKIA", re.compile(rb"AKIA[0-9A-Z]{16}")),
    ("私钥块", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Azure/AWS 风格密钥", re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}")),
]

# 明确知道可以放过的：占位符、示例、文档里讨论这些模式本身的地方
ALLOW = [
    (rb"C:\\Users\\WindowsApps", "Windows 自身的应用目录"),
    (rb"gh\[pousr\]_", "文档里讨论令牌模式"),
    (rb"github_pat_", "文档里讨论令牌模式"),
    (rb"AKIA\[0-9A-Z\]", "文档里讨论 AKIA 模式"),
    # 文档里说明「不要这样写」时会原样引用占位符形式，按 ASCII 匹配即可
    (rb"C:\\Users\\<", "文档里的占位符说明"),
    (rb"%TEMP%", "环境变量占位"),
]


def staged_files() -> list[str]:
    r = subprocess.run(["git", "diff", "--cached", "--name-only", "-z"],
                       capture_output=True)
    if r.returncode != 0:
        return []
    out = r.stdout.decode("utf-8", "replace")
    return [f for f in out.split("\0") if f]


def all_files() -> list[str]:
    r = subprocess.run(["git", "ls-files", "-z", "--cached", "--others",
                        "--exclude-standard"],
                       capture_output=True)
    out = r.stdout.decode("utf-8", "replace")
    return [f for f in out.split("\0") if f]


def is_allowed(data: bytes, pos: int, end: int) -> str | None:
    ctx = data[max(0, pos - 30):end + 30]
    for pat, why in ALLOW:
        if re.search(pat, ctx, re.I):
            return why
    return None


def scan(files: list[str], base: Path) -> int:
    findings: list[tuple[str, str, str]] = []
    scanned = 0
    for rel in files:
        p = base / rel
        if not p.is_file():
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        scanned += 1
        # 二进制跳过：.lnk / .png 里出现 `V:\...` 是格式噪声，不是泄露
        if b"\0" in data[:2048]:
            continue
        for label, pat in PATTERNS:
            for m in pat.finditer(data):
                if is_allowed(data, m.start(), m.end()):
                    continue
                s = max(0, m.start() - 32)
                ctx = data[s:m.end() + 32].decode("utf-8", "replace")
                ctx = " ".join(ctx.split())
                findings.append((label, rel, ctx))

    print(f"扫描 {scanned} 个文件（二进制已跳过）")
    if not findings:
        print("未发现敏感信息")
        return 0

    print()
    print(f"发现 {len(findings)} 处需要确认：")
    seen = set()
    for label, rel, ctx in findings:
        key = (label, rel)
        if key in seen:
            continue
        seen.add(key)
        print(f"  [{label}] {rel}")
        print(f"      …{ctx}…")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true", help="只扫暂存区（默认）")
    ap.add_argument("--all", action="store_true", help="扫整个仓库（含未跟踪）")
    args = ap.parse_args()

    base = Path(__file__).resolve().parent.parent
    files = all_files() if args.all else staged_files()
    if not files:
        print("没有文件可扫（暂存区是空的？）")
        return 0
    return scan(files, base)


if __name__ == "__main__":
    sys.exit(main())
