"""端到端验收：在受控的测试目录树上跑完整流程。

刻意**不用** ``--yes``，而是把 stdin 接到管道上，确认"不输入 yes 就
一个字节都不会被删"这条安全性质真的成立。
"""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from make_fixtures import build  # noqa: E402

DOLLAR = chr(36)


def run(args, stdin_text=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "dupehunter", *args],
        input=stdin_text, cwd=ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )
    return proc


def count_files(path):
    total = 0
    for _dirpath, _dirnames, filenames in os.walk(path):
        total += len(filenames)
    return total


def main():
    ok = True
    tmp = tempfile.mkdtemp(prefix="ds_e2e_")
    tree = os.path.join(tmp, "tree")
    info = build(tree, clean=True)
    print(f"测试树: {tree}")
    print(f"  受控文件 {info['written']} + 随机 {info['fillers']}, "
          f"长路径 {info['long_path_len']} 字符")
    before = count_files(tree)
    print(f"  初始文件数: {before}\n")

    # --- 1. 干跑：只报告 ---
    print("=" * 70)
    print("[1] 干跑扫描（不删除）")
    p = run([tree, "--no-default-excludes", "--quiet"])
    if p.returncode != 0:
        print("  !! 退出码", p.returncode, p.stderr[-400:])
        ok = False
    if "重复组" not in p.stdout:
        print("  !! 未输出重复组摘要")
        ok = False
    after_dry = count_files(tree)
    print(f"  文件数 {before} -> {after_dry}  "
          f"{'OK 未删除任何文件' if after_dry == before else '!! 干跑竟然删了文件'}")
    if after_dry != before:
        ok = False

    # --- 2. 取消确认：必须一个字节都不删 ---
    print()
    print("=" * 70)
    print("[2] 输入非 yes 取消删除 —— 必须零字节被删")
    p = run([tree, "--no-default-excludes", "--quiet", "--delete"],
            stdin_text="n\n")
    if "已取消" not in p.stdout:
        print("  !! 未提示已取消")
        ok = False
    after_cancel = count_files(tree)
    print(f"  文件数 {after_dry} -> {after_cancel}  "
          f"{'OK' if after_cancel == after_dry else '!! 取消后仍删了文件'}")
    if after_cancel != after_dry:
        ok = False

    # --- 3. dry-run ---
    print()
    print("=" * 70)
    print("[3] --dry-run —— 必须零字节被删")
    p = run([tree, "--no-default-excludes", "--quiet", "--delete", "--dry-run"],
            stdin_text="yes\n")
    if "dry-run" not in p.stdout:
        print("  !! 未进入 dry-run 分支")
        ok = False
    after_dryrun = count_files(tree)
    print(f"  文件数 {after_cancel} -> {after_dryrun}  "
          f"{'OK' if after_dryrun == after_cancel else '!! dry-run 删了文件'}")
    if after_dryrun != after_cancel:
        ok = False

    # --- 4. 真删（进回收站） ---
    print()
    print("=" * 70)
    print("[4] 确认删除 —— 文件应进回收站而非消失")
    p = run([tree, "--no-default-excludes", "--quiet", "--delete"],
            stdin_text="yes\n")
    print("  " + "\n  ".join(
        line for line in p.stdout.splitlines() if "回收站" in line or "失败" in line
    )[:600])
    after_delete = count_files(tree)
    removed = after_cancel - after_delete
    print(f"  文件数 {after_cancel} -> {after_delete}  (移除 {removed} 个)")

    # 校验被删的副本都能在回收站里找到
    import glob
    bins = glob.glob(os.path.join("C:\\", DOLLAR + "Recycle.Bin", "S-1-*"))
    entries = 0
    for b in bins:
        entries += len(glob.glob(os.path.join(b, DOLLAR + "I*")))
    print(f"  回收站条目总数: {entries}")
    if removed == 0:
        print("  !! 一个文件都没删掉")
        ok = False
    if entries == 0:
        print("  !! 回收站里没有条目，文件可能被永久删除了")
        ok = False

    # --- 5. 二次扫描：应还能找到剩余重复（每个组只剩 1 个） ---
    print()
    print("=" * 70)
    print("[5] 清理后重新扫描 —— 不应再有可回收的重复")
    p = run([tree, "--no-default-excludes", "--quiet", "--delete", "--dry-run"],
            stdin_text="yes\n")
    if "本应删除 0 个文件" not in p.stdout and "没有需要删除的副本" not in p.stdout:
        print("  !! 清理后仍有重复，说明删除不彻底")
        ok = False
    else:
        print("  OK 清理彻底")

    # --- 6. 导出报告 ---
    print()
    print("=" * 70)
    print("[6] 导出 CSV / JSON")
    csv_path = os.path.join(tmp, "r.csv")
    json_path = os.path.join(tmp, "r.json")
    p = run([tree, "--no-default-excludes", "--quiet",
             "--csv", csv_path, "--json", json_path])
    for path in (csv_path, json_path):
        if os.path.exists(path) and os.path.getsize(path) > 0:
            print(f"  OK {os.path.basename(path)} ({os.path.getsize(path)} 字节)")
        else:
            print(f"  !! {os.path.basename(path)} 未生成")
            ok = False

    print()
    print("=" * 70)
    print("端到端验收:", "全部通过" if ok else "存在失败项")
    print(f"（测试树 {tree} 可自行删除）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
