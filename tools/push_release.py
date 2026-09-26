"""一键推送：检测通道、推代码、打 tag、建 Release。

分开写是因为三件事的成功条件不同：
- 仓库创建走 `api.github.com`（本机通）
- `git push` 走 `github.com:443`（本机被阻）
- `gh release` 走 `api.github.com`（本机通）

所以**仓库可能已建好但代码还没推上去**，不能当成一个原子操作。

用法::

    python tools\\push_release.py --check           # 只体检，报告通道情况
    python tools\\push_release.py --proxy 7890      # 配好代理后全套跑完
    python tools\\push_release.py                   # 用已有配置直接试
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "v1.0.0"
REPO = "yasunosubaru/DumpSearch"
FIXTURE = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"),
                       "opencode", "dupecsharp")


def sh(cmd, timeout=1800, cwd=ROOT) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       timeout=timeout, cwd=str(cwd))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def port_open(host: str, port: int, timeout: float = 4.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check() -> dict:
    """通道体检。结果要区分清楚：API 通用不代表 git 能用。"""
    out: dict[str, object] = {}

    out["api_443"] = port_open("api.github.com", 443)
    out["git_https_443"] = port_open("github.com", 443)
    out["git_ssh_22"] = port_open("github.com", 22)
    out["git_ssh_443"] = port_open("ssh.github.com", 443)

    rc, text = sh(["git", "ls-remote", f"https://github.com/{REPO}.git"], timeout=60)
    out["ls_remote_ok"] = rc == 0
    out["ls_remote_err"] = "" if rc == 0 else text.strip().splitlines()[-1:] and \
        text.strip().splitlines()[-1] or text

    rc, _ = sh(["gh", "auth", "status"], timeout=60)
    out["gh_auth"] = rc == 0

    rc, text = sh(["gh", "api", f"repos/{REPO}", "--jq", ".visibility"], timeout=60)
    out["repo_visibility"] = text.strip() if rc == 0 else "?"
    return out


def report(info: dict) -> bool:
    print("通道体检")
    print(f"  api.github.com:443        {'通' if info['api_443'] else '不通'}"
          "   <- gh 命令走这条")
    print(f"  github.com:443            {'通' if info['git_https_443'] else '不通'}"
          "   <- git push 走这条")
    print(f"  github.com:22             {'通' if info['git_ssh_22'] else '不通'}"
          "   <- SSH")
    print(f"  ssh.github.com:443        {'通' if info['git_ssh_443'] else '不通'}")
    print(f"  git ls-remote             {'成功' if info['ls_remote_ok'] else '失败'}")
    if not info["ls_remote_ok"]:
        print(f"    {info['ls_remote_err']}")
    print(f"  gh 登录                    {'正常' if info['gh_auth'] else '异常'}")
    print(f"  仓库可见性                 {info['repo_visibility']}")
    print()
    if info["ls_remote_ok"]:
        print("-> git 通道可用，可以直接推送")
        return True
    print("-> git 通道不通。需要先开代理（--proxy <端口>）或改走 SSH。")
    return False


def set_proxy(port: int) -> None:
    proxy = f"http://127.0.0.1:{port}"
    sh(["git", "config", "--global", "http.proxy", proxy])
    sh(["git", "config", "--global", "https.proxy", proxy])
    print(f"已设置 git 代理 = {proxy}")


def do_push() -> int:
    rc, out = sh(["git", "push", "-u", "origin", "main"], timeout=3600)
    print(out.strip())
    return rc


def do_tag() -> int:
    rc, out = sh(["git", "tag", "-a", VERSION, "-m", f"首个版本 {VERSION}"], timeout=120)
    if rc != 0:
        print(out.strip())
        return rc
    rc, out = sh(["git", "push", "origin", VERSION], timeout=3600)
    print(out.strip())
    return rc


def do_release(rel_dir: str) -> int:
    zip_path = os.path.join(rel_dir,
                            f"DumpSearch-{VERSION}-win-x64-selfcontained.zip")
    if not os.path.exists(zip_path):
        print(f"找不到发布包: {zip_path}")
        print("先跑: python winui\\tools\\package_release.py "
              f"--out \"{rel_dir}\" --fixture \"{FIXTURE}\"")
        return 1

    notes = os.path.join(rel_dir, "RELEASE_NOTES.md")
    if not os.path.exists(notes):
        print(f"找不到发布说明: {notes}")
        return 1

    rc, out = sh([
        "gh", "release", "create", VERSION, zip_path,
        "--title", f"Dump Search {VERSION}",
        "--notes-file", notes,
    ], timeout=1800)
    print(out.strip())
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只做通道体检")
    ap.add_argument("--proxy", type=int, default=None,
                    help="本地 HTTP 代理端口，配好后再推")
    ap.add_argument("--rel", default=os.path.join(
        os.environ.get("TEMP", r"C:\Windows\Temp"), "ds_rel"),
        help="发布包所在目录")
    ap.add_argument("--skip-release", action="store_true",
                    help="只推代码和 tag，不建 Release")
    args = ap.parse_args()

    if args.proxy:
        set_proxy(args.proxy)
        print()

    info = check()
    ok = report(info)

    if args.check:
        return 0 if ok else 1
    if not ok:
        print("\n没推。代理开好后重跑，或加 --proxy <端口>。")
        return 1

    print("\n推送 main …")
    if do_push() != 0:
        return 1

    print(f"\n打 tag {VERSION} …")
    if do_tag() != 0:
        return 1

    if args.skip_release:
        print("\n已跳过 Release。")
        return 0

    print("\n建 Release …")
    if do_release(args.rel) != 0:
        return 1

    print(f"\n完成: https://github.com/{REPO}/releases/tag/{VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
