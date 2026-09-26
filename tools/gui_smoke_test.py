"""GUI 冒烟测试：真实创建窗口、跑一次扫描、驱动事件循环、截图。

这不是 mock —— 会真的建 Tk 窗口、真的起扫描线程、真的走 after 泵。
目的是抓出"模块能 import 但界面一开就炸"这类问题。
"""

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from make_fixtures import build  # noqa: E402

from dupehunter.gui.app import MainWindow  # noqa: E402
from dupehunter.gui.theme import enable_dpi_awareness  # noqa: E402

import tkinter as tk  # noqa: E402


def pump(root, seconds):
    """跑事件循环若干秒（让 after 泵有机会工作）。"""
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def main():
    enable_dpi_awareness()
    tmp = tempfile.mkdtemp(prefix="ds_gui_")
    tree = os.path.join(tmp, "tree")
    build(tree, clean=True)
    print(f"测试树: {tree}")

    root = tk.Tk()
    root.withdraw()  # 先隐藏，避免干扰；截图前再显示
    win = MainWindow(root)
    print("OK  主窗口构建完成")

    # 选一个盘符为 False，改用附加目录，避免真的全盘扫描
    for var in win.scan_panel.drive_vars.values():
        var.set(False)
    win.scan_panel.dirs_text.delete("1.0", "end")
    win.scan_panel.dirs_text.insert("1.0", tree)
    win.scan_panel.min_size_var.set("1")
    print("OK  扫描范围已设置为测试树")

    # 触发扫描
    win.on_scan()
    print("OK  扫描已启动，扫描线程存活:",
          win._worker is not None and win._worker.is_alive())

    # 等扫描完成
    deadline = time.time() + 60
    while time.time() < deadline and win._busy:
        root.update()
        time.sleep(0.02)
    pump(root, 0.5)

    if win._busy:
        print("!! 60 秒内扫描未完成")
        return 1
    if win.result is None:
        print("!! 扫描结束但 result 为 None")
        return 1
    if win.index is None:
        print("!! 未构建 ResultIndex")
        return 1

    print(f"OK  扫描完成: {win.result.summary()}")
    print(f"    重复组 {len(win.index.groups)}, 当前视图 {len(win.index)} 组")

    if win.tree is None:
        print("!! 未创建结果表格")
        return 1

    # 驱动虚拟表格：物化窗口 + 展开/折叠 + 滚动
    root.deiconify()
    root.geometry("1400x860")
    root.update()
    pump(root, 0.4)

    # 结果表必须有足够宽度，否则所有数据列都被挤出可视区
    table_w = win.tree.tree.winfo_width()
    print(f"OK  结果表宽度 {table_w}px (左侧配置栏 {win.scan_panel.winfo_width()}px)")
    if table_w < 500:
        print(f"!! 结果表过窄 ({table_w}px)，数据列会不可见")
        return 1

    # 列宽检查
    cols = {name: win.tree.tree.column(name, "width")
            for name, _t, _w, _a in win.tree.COLUMNS}
    print("    列宽:", {k: int(v) for k, v in cols.items()})
    total_cols = win.tree.tree.column("#0", "width") + sum(cols.values())
    print(f"    列总宽 {int(total_cols)}px / 可视 {table_w}px "
          f"(超出的部分可用横向滚动条查看)")

    win.tree.index.expand_all()
    win.tree.mark_dirty(full=True)
    pump(root, 0.4)
    materialized = len(win.tree.tree.get_children())
    total_rows = win.tree.index.total_rows()
    print(f"OK  展开全部: 总行数 {total_rows}, 实际物化 {materialized} 行 "
          f"(虚拟化生效: {materialized < total_rows})")
    if materialized == 0:
        print("!! 表格一行都没渲染出来")
        return 1
    if total_rows > 0 and materialized >= total_rows:
        print("   注意: 测试树行数太少，虚拟化未被压测")

    win.tree.index.collapse_all()
    win.tree.mark_dirty(full=True)
    pump(root, 0.3)
    print(f"OK  折叠全部: 物化 {len(win.tree.tree.get_children())} 行")

    # 滚动到底，验证滑窗
    win.tree.index.expand_all()
    win.tree.mark_dirty(full=True)
    pump(root, 0.2)
    win.tree.tree.yview_moveto(1.0)
    pump(root, 0.3)
    after_scroll = len(win.tree.tree.get_children())
    print(f"OK  滚动到底: 仍只物化 {after_scroll} 行（未退化成全量插入）")
    if after_scroll > materialized * 4 + 40:
        print("!! 物化行数失控")
        return 1

    # 勾选 / 过滤 / 排序
    win.index.check_none()
    win._after_check_change()
    print("OK  全不选 -> 勾选组数", win.index.checked_group_count())
    win.index.check_all()
    win._after_check_change()
    print("OK  全选   -> 勾选组数", win.index.checked_group_count())

    for mode in ("size", "count", "name", "path", "mtime", "reclaimable"):
        win.index.set_sort(mode)
        win.tree.mark_dirty(full=True)
        root.update()
    print("OK  六种排序全部可用")

    win.filter_var.set("name:report")
    win._apply_filter()
    pump(root, 0.3)
    print(f"OK  筛选 name:report -> {len(win.index)} 组")
    win.filter_var.set("")
    win._apply_filter()
    pump(root, 0.2)
    print(f"OK  清空筛选 -> {len(win.index)} 组")

    # 删除计划 + 审计（不真的执行）
    from dupehunter.gui.viewmodel import audit_plan, build_plan
    plan = build_plan(win.index)
    warnings = audit_plan(plan, win.index)
    print(f"OK  删除计划: {plan.summary()}")
    for w in warnings:
        print("    审计提醒:", w)
    if plan.file_count and not warnings:
        print("    (无提醒)")

    # 截图：必须把窗口提到最前，否则会被别的窗口挡住（ImageGrab 抓的是屏幕）
    shot = os.path.join(tmp, "gui.png")
    try:
        import PIL.ImageGrab  # type: ignore
        import PIL.Image  # noqa: F401

        root.attributes("-topmost", True)
        root.lift()
        root.focus_force()
        root.update()
        time.sleep(0.5)
        root.update()

        x, y = root.winfo_rootx(), root.winfo_rooty()
        w, h = root.winfo_width(), root.winfo_height()
        img = PIL.ImageGrab.grab(bbox=(x, y, x + w, y + h))
        img.save(shot)
        root.attributes("-topmost", False)
        print(f"OK  截图已保存: {shot}  ({img.width}x{img.height})")
    except Exception as exc:
        print(f"    跳过截图（缺少 PIL 或无桌面会话）: {exc}")

    root.destroy()
    print()
    print("GUI 冒烟测试: 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
