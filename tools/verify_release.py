"""发布后的核对清单：把"推上去了"和"该有的都有"分开验证。

为什么单独写：发布涉及四件互相独立的事——仓库可见性、代码、tag、
Release 附件——它们走**不同的网络通道**（api.github.com / github.com:443），
一条通了不代表另一条也通。而且 PowerShell 里 `gh --jq` 的引号转义
极易踩雷（`\(` 会被当成 PowerShell 转义而不是 jq 语法），
所以这里用 `--json` 拿原始 JSON，再交给 Python 解析。

用法::

    python tools\\verify_release.py            # 全部核对
    python tools\\verify_release.py --public   # 额外验证"匿名不可访问"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "yasunosubaru/DumpSearch"
VERSION = "v1.0.0"


def gh(args: list[str], timeout: int = 120) -> tuple[int, str, object]:
    """跑 gh 并解析 JSON 输出。返回 (退出码, 原始文本, 解析结果)。

    调用方负责自己带 `--json ...`；这里不做任何参数改写。
    不要在 PowerShell 里直接给 gh 传 `--jq`：jq 表达式里的 `\(`
    会被 PowerShell 当转义处理，报出莫名其妙的 "failed to parse jq"，
    改用 Python 解析 JSON 稳得多。
    """
    r = subprocess.run(["gh", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    out = (r.stdout or "").strip()
    parsed: object = None
    if out.startswith(("{", "[")):
        try:
            parsed = json.loads(out)
        except json.JSONDecodeError:
            parsed = None
    return r.returncode, (out + (r.stderr or "")), parsed


def git(args: list[str], timeout: int = 120) -> tuple[int, str]:
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout,
                       cwd=str(ROOT))
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def curl_code(url: str, timeout: int = 30) -> str:
    r = subprocess.run(["curl.exe", "-s", "-o", "NUL", "-w", "%{http_code}",
                        "--max-time", str(timeout), "-L", url],
                       capture_output=True, text=True, timeout=timeout + 15)
    return (r.stdout or "").strip() or "ERR"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--public", action="store_true",
                    help="额外用匿名请求验证不可访问")
    args = ap.parse_args()

    problems: list[str] = []
    print("1. 仓库")
    rc, out, data = gh(["repo", "view", REPO, "--json",
                        "name,visibility,url,defaultBranchRef"])
    if rc != 0 or not isinstance(data, dict):
        print(f"   读取失败: {out[:200]}")
        problems.append("无法读取仓库信息")
    else:
        vis = data.get("visibility")
        print(f"   {data['name']}  可见性={vis}")
        print(f"   {data['url']}")
        print(f"   默认分支={data.get('defaultBranchRef', {}).get('name')}")
        if vis not in ("PRIVATE", "PUBLIC"):
            problems.append(f"可见性异常: {vis}")

    print("\n2. 代码是否推上去了")
    rc, local = git(["rev-parse", "HEAD"])
    rc2, out, data = gh(["api", f"repos/{REPO}/commits/main"])
    if rc2 == 0 and isinstance(data, dict):
        remote = data.get("sha", "")
        print(f"   本地 {local[:7]}   远端 {remote[:7]}")
        if local and remote and local != remote:
            problems.append("本地 HEAD 与远端 main 不一致，代码没推完")
    else:
        print(f"   读取远端失败: {out[:200]}")
        problems.append("无法读取远端 main")

    print("\n3. tag")
    rc, out, data = gh(["api", f"repos/{REPO}/git/ref/tags/{VERSION}"])
    if rc == 0 and isinstance(data, dict):
        print(f"   {data['ref']} -> {data['object']['sha'][:7]}")
    else:
        print(f"   {VERSION} 不存在: {out[:200]}")
        problems.append(f"tag {VERSION} 缺失")

    print("\n4. Release 与附件")
    # 必须显式带 --json：不给的话 gh 输出的是人类可读文本，解析不了。
    # 用 release view --json 的结构化字段，别去解析那段文本。
    rc, out, data = gh(["release", "view", VERSION, "--json",
                        "name,isDraft,isPrerelease,url,assets"])
    if rc == 0 and isinstance(data, dict):
        print(f"   {data['name']}   draft={data['isDraft']}  "
              f"prerelease={data['isPrerelease']}")
        if data["isDraft"]:
            problems.append("Release 还是草稿状态")
        for a in data.get("assets", []):
            print(f"   附件 {a['name']}  {a['size']/1024/1024:.1f} MB")
        if not data.get("assets"):
            problems.append("Release 没有附件")
    else:
        print(f"   读取失败: {out[:200]}")
        problems.append("无法读取 Release")

    if args.public:
        print("\n5. 匿名可访问性（私有仓库应全部 404/000）")
        for url, label in (
            (f"https://github.com/{REPO}", "仓库网页"),
            (f"https://api.github.com/repos/{REPO}", "API"),
            (f"https://github.com/{REPO}/releases/tag/{VERSION}", "Release 页"),
            (f"https://raw.githubusercontent.com/{REPO}/main/README.md", "raw 文件"),
        ):
            code = curl_code(url)
            print(f"   {code:>4}  {label}")
            if code not in ("404", "000", "403", "401"):
                problems.append(f"{label} 匿名返回 {code}，可能仍是公开的")

    print()
    if problems:
        print(f"发现 {len(problems)} 个问题：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("发布核对全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
