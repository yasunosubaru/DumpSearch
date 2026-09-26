"""虚拟化压测：模拟真实全盘规模（数万个重复组 / 几十万行）。

直接构造 ResultIndex + VirtualGroupTree，检查：
  * 物化行数始终被压在"可视区 + 一屏缓冲"以内，不随数据量线性增长；
  * 反复滚动 / 展开 / 折叠 / 排序后，物化行数不失控、行内容不串位；
  * locate() 与 Treeview 实际显示的行一一对应（不多不少不错位）。
"""

import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tkinter as tk  # noqa: E402

from dupehunter.gui.theme import build_style, enable_dpi_awareness  # noqa: E402
from dupehunter.gui.viewmodel import ResultIndex  # noqa: E402
from dupehunter.gui.widgets import VirtualGroupTree  # noqa: E402
from dupehunter.model import (  # noqa: E402
    DuplicateGroup, FileEntry, KeepRule, ScanResult,
)

GROUPS = 8000
MIN_MEMBERS, MAX_MEMBERS = 2, 6


def build_result(n=GROUPS, seed=11):
    rng = random.Random(seed)
    groups = []
    for i in range(n):
        count = rng.randint(MIN_MEMBERS, MAX_MEMBERS)
        size = rng.randint(1 << 10, 1 << 30)
        members = [
            FileEntry(rf"D:\data\grp{i}\copy{j}\file_{j}.bin", size,
                      1_600_000_000 + rng.randint(0, 10_000_000))
            for j in range(count)
        ]
        groups.append(
            DuplicateGroup(digest=bytes([i % 251]) * 32, size=size,
                           members=members, verified=(i % 7 != 0))
        )
    res = ScanResult(groups=groups)
    res.config.keep_rule = KeepRule.NEWEST
    return res


def pump(root, seconds=0.05):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.005)


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    root.geometry("1200x700")
    build_style(root)

    print(f"构造 {GROUPS} 个重复组 …")
    t0 = time.time()
    res = build_result()
    index = ResultIndex(res)
    print(f"  用时 {time.time() - t0:.2f}s")

    total_rows = index.total_rows()
    print(f"  展平总行数: {total_rows:,}  (数据全在内存，Treeview 只物化可视区)")

    from dupehunter.gui.theme import Fonts
    tree = VirtualGroupTree(root, index, Fonts(root))
    tree.pack(fill="both", expand=True)
    root.update()
    pump(root, 0.2)

    materialized = len(tree.tree.get_children())
    screen = tree._screen_rows()
    budget = screen * 3
    print(f"\n可视行数 {screen}, 物化 {materialized} 行, 预算 {budget}")
    assert materialized <= budget, f"初始物化 {materialized} 超出预算 {budget}"
    print("OK  初始物化在预算内")

    # --- 逐屏滚动，检查不串位 ---
    print("\n全范围随机滚动 60 次 …")
    rng = random.Random(5)
    worst = 0
    mismatch_total = 0
    for step in range(60):
        frac = rng.random()
        tree.scroll_to_fraction(frac)
        pump(root, 0.03)
        count = len(tree.tree.get_children())
        worst = max(worst, count)
        if count > budget:
            print(f"  !! 第 {step} 步 (frac={frac:.2f}) 物化 {count} 行，"
                  f"超出预算 {budget}")
            mismatch_total += 1
        # 抽查首尾两行的内容是否与 locate() 一致
        kids = tree.tree.get_children()
        for iid in (kids[0] if kids else None, kids[-1] if kids else None):
            if not iid or not iid.startswith("r"):
                mismatch_total += 1
                continue
            row = int(iid[1:])
            spec = index.locate(row)
            if spec is None:
                print(f"  !! 第 {step} 步 row {row} 越界却仍物化")
                mismatch_total += 1
                continue
            shown = tree.tree.item(iid, "text")
            expect = tree._row_text(spec)
            if shown != expect:
                mismatch_total += 1
                if mismatch_total < 4:
                    print(f"  !! 第 {step} 步 row {row} 内容不符")
                    print(f"     frac={frac:.2f} 显示: {shown!r}")
                    print(f"     期望: {expect!r}")
    print(f"  最大物化 {worst} 行, 问题 {mismatch_total} 次")
    assert worst <= budget, f"滚动中物化 {worst} 超出预算 {budget}"
    assert mismatch_total == 0, f"发现 {mismatch_total} 次不一致"
    print("OK  滚动过程中物化受控且内容不串位")

    # --- 滚动条位置应与 offset 同步 ---
    print("\n检查滚动条同步 …")
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        tree.scroll_to_fraction(frac)
        pump(root, 0.03)
        first, _last = tree.vsb.get()
        expect = min(1.0, tree._offset / max(1, index.total_rows()))
        if abs(first - expect) > 0.01:
            print(f"  !! frac={frac}: 滚动条 {first:.3f} != offset {expect:.3f}")
            mismatch_total += 1
    assert mismatch_total == 0, "滚动条与 offset 不同步"
    print("OK  滚动条位置与逻辑偏移一致")

    # --- 折叠全部应立刻收缩 ---
    print("\n折叠全部 …")
    index.collapse_all()
    tree.mark_dirty(full=True)
    pump(root, 0.2)
    after_collapse = len(tree.tree.get_children())
    print(f"  总行数 {total_rows:,} -> {index.total_rows():,}, "
          f"物化 {after_collapse} 行")
    assert after_collapse <= budget, f"折叠后仍物化 {after_collapse} 行"
    # 关键：折叠把 4 万行压成 8000 行后，Treeview 里必须**同步收缩**，
    # 不能残留上一轮的旧行（曾经因为 mark_dirty 清掉 _lo/_hi 导致残留）。
    assert after_collapse <= min(index.total_rows(), budget), \
        f"折叠后物化 {after_collapse}，超过 min(总行数, 预算)"
    print("OK  折叠后旧行已清除")

    # --- 展开全部 ---
    print("\n展开全部 …")
    index.expand_all()
    tree.mark_dirty(full=True)
    pump(root, 0.2)
    after_expand = len(tree.tree.get_children())
    print(f"  总行数 {index.total_rows():,}, 物化 {after_expand} 行")
    assert after_expand <= budget, f"展开后物化 {after_expand} 行"
    print("OK  展开后未退化为全量插入")

    # --- 排序 / 过滤 ---
    print("\n排序与过滤 …")
    for mode in ("size", "count", "name", "path", "mtime", "reclaimable"):
        index.set_sort(mode)
        tree.mark_dirty(full=True)
        pump(root, 0.05)
        count = len(tree.tree.get_children())
        assert count <= budget, f"{mode} 排序后物化 {count} 行"
    print("OK  六种排序后物化均受控")

    index.set_filter_text("name:file_1")
    tree.mark_dirty(full=True)
    pump(root, 0.1)
    filtered = len(index.view)
    print(f"  筛选 name:file_1 -> {filtered:,} 组")
    index.set_filter_text("")
    tree.mark_dirty(full=True)
    pump(root, 0.1)
    assert len(index.view) == GROUPS, "清空筛选未恢复全集"
    print("OK  筛选可收可放")

    # --- 勾选 ---
    print("\n勾选 …")

    def first_group_row():
        """取当前物化区里第一行**组行**的 (iid, 勾选列值)。

        勾选标记画在独立的"选"列里，成员行该列为空。
        """
        for iid in tree.tree.get_children():
            if not iid.startswith("r"):
                continue
            spec = index.locate(int(iid[1:]))
            if spec is not None and spec.kind == "group":
                return iid, tree.tree.item(iid, "values")[0]
        raise AssertionError("可视区里没有组行，无法验证勾选显示")

    index.check_all()
    tree.mark_dirty(full=False)
    pump(root, 0.1)
    _iid, mark = first_group_row()
    assert mark == "☑", f"全选后勾选列未显示: {mark!r}"
    print("OK  全选后勾选标记已刷新到可见组行")

    index.check_none()
    tree.mark_dirty(full=False)
    pump(root, 0.1)
    _iid, mark = first_group_row()
    assert mark == "☐", f"全不选后勾选列仍显示勾选: {mark!r}"
    print("OK  全不选后勾选标记已刷新")

    # 成员行不应有勾选标记（避免误以为能单独勾某个副本）
    for iid in tree.tree.get_children():
        row = int(iid[1:])
        spec = index.locate(row)
        if spec is not None and spec.kind == "file":
            assert tree.tree.item(iid, "values")[0] == "", \
                "成员行不应显示勾选标记"
    print("OK  成员行不显示勾选标记（勾选以组为单位）")

    index.check_all()
    tree.mark_dirty(full=False)
    pump(root, 0.1)
    gi = index.view[0]
    index.set_checked(f"g:{gi}", False)
    tree.mark_dirty(full=False)
    pump(root, 0.1)
    assert not index.is_checked(f"g:{gi}")
    print("OK  单组勾选切换正常")

    root.destroy()
    print()
    print("虚拟化压测: 全部通过")
    print(f"（数据量 {total_rows:,} 行，Treeview 峰值物化 {max(worst, after_expand)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
