r"""生成供 README / Release 说明页用的**脱敏**截图。

为什么不能用 tools\shot.py 的输出：那些截图里能看到展开后的用户临时目录
（形如 C:\Users\<本机用户名>\AppData\Local\Temp\...）——左侧「只扫指定文件夹」
列表和结果行的完整路径都会把它带出来，直接放进公开仓库等于公布本机用户名。

做法：把夹具生成到一个**中性路径**（C:\DSDemo），让界面上根本不出现
用户名，而不是事后去涂图。

只截 WinUI 3 前端：它才是 README 主角，Python 版的界面差异较大，
一张图说明不了问题，反而容易误导。

用法::

    python tools/make_screenshots.py              # 生成全部
    python tools/make_screenshots.py --keep       # 生成但保留演示目录
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
WINUI = os.path.join(ROOT, "winui")
SHOTS = os.path.join(ROOT, "docs", "images")

# 中性路径。**不能**放用户目录或 %TEMP%（都含用户名）。
DEMO_DIR = r"C:\DSDemo"

EXE = os.path.join(
    WINUI, "src", "DumpSearch.App", "bin", "Release",
    "net8.0-windows10.0.19041.0", "DumpSearch.exe")

MAKE_FIXTURES = os.path.join(ROOT, "tools", "make_fixtures.py")

# (输出名, 传给程序的额外参数, 等待秒数)
SHOTS_SPEC = [
    ("main-window.png", [], 9),
    ("results.png", ["--autoscan", DEMO_DIR], 11),
    ("progress.png", ["--demo-progress"], 9),
]


def build_fixture() -> bool:
    """在**中性路径**生成受控测试树。"""
    if os.path.isdir(DEMO_DIR):
        print(f"  已有 {DEMO_DIR}，先清理")
        shutil.rmtree(DEMO_DIR, ignore_errors=True)
    r = subprocess.run(
        [sys.executable, MAKE_FIXTURES, DEMO_DIR, "--clean"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, cwd=ROOT)
    if r.returncode != 0:
        print("  夹具生成失败:")
        print((r.stdout or "") + (r.stderr or ""))
        return False
    print(f"  夹具就绪: {DEMO_DIR}")
    return True


def shoot(out: str, extra: list[str], wait: float) -> bool:
    dest = os.path.join(SHOTS, out)
    r = subprocess.run(
        [sys.executable, os.path.join(WINUI, "tools", "shot.py"),
         EXE, dest, str(wait), *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, cwd=WINUI)
    ok = os.path.exists(dest)
    print(f"  {'OK ' if ok else '失败'} {out}"
          + ("" if ok else f"  {(r.stdout or '')[:200]}"))
    return ok


def verify_clean() -> list[str]:
    """确认新截图里没有用户名残留——用字节扫描，不靠肉眼。"""
    needles = [b"SunCh", b"C:" + bytes([92]) + b"Users" + bytes([92])]
    bad: list[str] = []
    for f in os.listdir(SHOTS):
        p = os.path.join(SHOTS, f)
        with open(p, "rb") as fh:
            data = fh.read()
        for n in needles:
            if n in data:
                bad.append(f)
                break
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true",
                    help="保留 %s（默认保留，只在明确要求时清理）" % DEMO_DIR)
    args = ap.parse_args()

    if not os.path.exists(EXE):
        print(f"找不到 exe，先构建:\n  cd {WINUI}\\src\\DumpSearch.App && "
              "dotnet build -c Release")
        return 2

    os.makedirs(SHOTS, exist_ok=True)
    print("准备演示夹具")
    if not build_fixture():
        return 1

    print("截图")
    ok = True
    for out, extra, wait in SHOTS_SPEC:
        ok &= shoot(out, extra, wait)

    if not args.keep:
        print(f"清理演示目录 {DEMO_DIR}")
        shutil.rmtree(DEMO_DIR, ignore_errors=True)

    print("核查截图是否含本机用户名")
    bad = verify_clean()
    if bad:
        print("  [失败] 以下截图含用户名残留，不能放进公开仓库:")
        for b in bad:
            print(f"    {b}")
        return 1
    print("  干净")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())