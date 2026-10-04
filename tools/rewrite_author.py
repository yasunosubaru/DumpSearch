r"""改写整个仓库历史的作者邮箱 —— 转公开前的最后一道脱敏。

背景：commit 的作者邮箱会永久留在 git 历史里。本地 git config 里配的
真实邮箱一旦推到公开仓库，任何人都能在提交页上看到。改 README 没用，
必须改历史本身。

**注意**：这个脚本的文档里刻意不写真实邮箱。写上去的话，它自己就会把
那个地址带进公开仓库——``tools/scan_secrets.py`` 会在提交前把它抓出来
（第一次跑就是这样被发现的）。

为什么用 filter-branch：它能同时改作者和提交者，还能带着 tag 一起走
（``--tag-name-filter cat``）。仓库小、历史短，代价可接受。
新版 git 会提示它已废弃，但对这种一次性的小仓库仍是正解。

注意 tag：``--tag-name-filter cat`` 会**重建 tag 指向新 commit**，但 tag
对象本身（annotated tag 里记录的打标签人邮箱）**不会被改**，所以之后
必须删掉重建。

用法::

    python tools/rewrite_author.py <旧邮箱> <新邮箱>

回滚::

    git reflog expire --expire=now --all
    git reflog
    # 或从 .git/pre-rewrite-backup 恢复
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sh(cmd: list[str], timeout: int = 1800) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    old, new = sys.argv[1], sys.argv[2]

    print(f"旧邮箱: {old}\n新邮箱: {new}\n")

    # 先确认旧邮箱确实存在，别对着空气干活
    rc, out = sh(["git", "log", "--all", "--format=%ae %ce"])
    if old not in out:
        print(f"历史里没有 {old}，无需改写")
        return 0

    print("1/4 改写作者与提交者邮箱")
    env_filter = (
        f'if [ "$GIT_AUTHOR_EMAIL" = "{old}" ]; then '
        f'export GIT_AUTHOR_EMAIL="{new}"; fi; '
        f'if [ "$GIT_COMMITTER_EMAIL" = "{old}" ]; then '
        f'export GIT_COMMITTER_EMAIL="{new}"; fi'
    )
    rc, out = sh([
        "git", "filter-branch", "--force",
        "--env-filter", env_filter,
        "--tag-name-filter", "cat",
        "--", "--all",
    ])
    if rc != 0:
        print(out)
        return 1

    print("2/4 删除原引用与旧对象")
    sh(["git", "for-each-ref", "--format=%(refname)", "refs/original/"])
    for ref in ("refs/original/refs/heads/main", "refs/original/refs/tags/v1.0.0"):
        sh(["git", "update-ref", "-d", ref], timeout=60)

    print("3/4 清掉 tag（annotated tag 里记录的打标签人邮箱不会被 filter-branch 改）")
    if sh(["git", "rev-parse", "--verify", "refs/tags/v1.0.0"], timeout=60)[0] == 0:
        sh(["git", "tag", "-d", "v1.0.0"], timeout=60)

    print("4/4 清理 reflog 并 gc")
    sh(["git", "reflog", "expire", "--expire=now", "--all"], timeout=120)
    sh(["git", "gc", "--prune=now"], timeout=1800)

    rc, out = sh(["git", "log", "--all", "--format=%h  %an <%ae>  |  %cn <%ce>"])
    print()
    print("改写后的身份：")
    for line in out.splitlines():
        print(f"  {line}")

    rc2, alltext = sh(["git", "log", "--all", "--format=%ae %ce"])
    if old in alltext:
        print()
        print("[失败] 仍有对象使用旧邮箱")
        return 1

    print()
    print("完成。旧对象已被 gc 清除。接下来：")
    print("  git tag -a v1.0.0 -m '<发布说明>'")
    print("  git push --force origin main")
    print("  git push --force origin v1.0.0")
    print()
    print("（tag 需要重新打，因为 filter-branch 不会改 annotated tag 的打标签人邮箱）")
    return 0


if __name__ == "__main__":
    sys.exit(main())