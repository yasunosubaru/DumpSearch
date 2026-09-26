"""一键跑完全部验证：单元测试 + 回收站安全 + $I 结构 + 端到端 + GUI + 虚拟化压测。

用法::

    python tools\\verify_all.py
"""

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SUITES = [
    ("单元测试", [sys.executable, os.path.join("tests", "test_core.py")]),
    ("回收站安全（长路径不得被静默永久删除）",
     [sys.executable, os.path.join("tools", "verify_trash_safety.py")]),
    ("回收站 $I 记录结构",
     [sys.executable, os.path.join("tools", "verify_irecord.py")]),
    ("虚拟化压测（4 万行）",
     [sys.executable, os.path.join("tools", "virtual_list_stress.py")]),
    ("GUI 冒烟",
     [sys.executable, os.path.join("tools", "gui_smoke_test.py")]),
    ("端到端（取消 / dry-run 必须零删除）",
     [sys.executable, os.path.join("tools", "e2e_check.py")]),
]


def main():
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    results = []
    for name, cmd in SUITES:
        print("=" * 74)
        print(f"▶ {name}")
        print("=" * 74)
        t0 = time.time()
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        elapsed = time.time() - t0
        tail = [ln for ln in proc.stdout.splitlines() if ln.strip()][-3:]
        for line in tail:
            print("   ", line)
        if proc.returncode != 0:
            print("    !! 失败，输出末尾：")
            for line in (proc.stdout + proc.stderr).splitlines()[-25:]:
                print("     ", line)
        results.append((name, proc.returncode == 0, elapsed))
        print(f"    -> {'通过' if proc.returncode == 0 else '失败'} ({elapsed:.1f}s)\n")

    print("=" * 74)
    print("汇总")
    print("=" * 74)
    for name, ok, elapsed in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<42} {elapsed:6.1f}s")
    failed = [n for n, ok, _ in results if not ok]
    print()
    if failed:
        print(f"!! {len(failed)} 项失败: {failed}")
        return 1
    print(f"全部 {len(results)} 项验证通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
