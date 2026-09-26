"""虚拟化结果表格 + 扫描配置面板 + 进度显示。

为什么必须虚拟化：全盘扫描可能产出**几万个重复组、几十万个文件行**。
``ttk.Treeview`` 一次性 insert 这么多行会直接把 Tk 拖死（每行都是一个
Tcl 对象 + 一个 Python 侧的 item dict）。

:class:`VirtualGroupTree` 的做法是：数据全在 :class:`ResultIndex` 里，
Treeview 里**只物化可视区域 ± 一屏**的行。滚动时只增删窗口边缘的那几行，
中间部分完全不碰。
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from ..model import KeepRule, format_bytes
from .theme import Fonts
from .viewmodel import (
    ResultIndex,
    RowSpec,
    ROW_GROUP,
    SortMode,
)

# 物化窗口在可视区上下各多留一屏，避免快速滚动时露白
_BUFFER_SCREENS = 1.0

CHECKED = "☑"
UNCHECKED = "☐"
PARTIAL = "◪"

ROLE_KEEP = "保留"
ROLE_DELETE = "删除"


def format_mtime(ts: float) -> str:
    import time

    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


class VirtualGroupTree(ttk.Frame):
    """按 :class:`ResultIndex` 渲染的虚拟滚动表格。"""

    # 勾选放在**独立窄列**里，而不是塞进树列的文本前缀：
    # 树列自带展开指示器，两个方框挨在一起会让人分不清哪个是"勾选"。
    # 这是一个"删文件"的工具，勾选状态必须一眼可辨。
    SEL_COLUMN = "sel"

    COLUMNS = (
        (SEL_COLUMN, "选", 36, "center"),
        ("size", "单文件大小", 92, "e"),
        ("count", "数量", 56, "e"),
        ("reclaim", "可释放", 92, "e"),
        ("role", "处置", 62, "center"),
        ("mtime", "修改时间", 128, "w"),
        ("path", "路径", 420, "w"),
    )

    def __init__(
        self,
        master: tk.Misc,
        index: ResultIndex,
        fonts: Fonts,
        rule: KeepRule = KeepRule.NEWEST,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(master)
        self.index = index
        self.fonts = fonts
        self.rule = rule
        self.on_status = on_status or (lambda _msg: None)

        self._lo: Optional[int] = None
        self._hi: Optional[int] = None
        self._offset = 0          # 首个物化行的逻辑行号（**自己维护**）
        self._force_rebuild = False
        self._dirty = True
        self._rowheight = 22
        self._suspend = False

        self._build()
        self.bind_events()

    # -- 构建 ------------------------------------------------------------
    def _build(self) -> None:
        columns = [name for name, _t, _w, _a in self.COLUMNS]
        self.tree = ttk.Treeview(
            self,
            columns=columns,
            show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="文件", anchor="w")
        self.tree.column("#0", width=330, minwidth=180, stretch=True, anchor="w")
        for name, title, width, anchor in self.COLUMNS:
            self.tree.heading(name, text=title, anchor=anchor,
                              command=lambda n=name: self._on_heading(n))
            self.tree.column(name, width=width, minwidth=50, stretch=False,
                             anchor=anchor)

        # 滚动条必须由**我们自己**驱动。
        #
        # 绝对不能依赖 Treeview 自己的 yview()/yscrollcommand：它的比例是
        # 相对"当前已插入的行数"算的，而不是逻辑总行数。于是当树里只有几十
        # 行时 yview() 会返回 (0.0, 1.0)，据此反推窗口就会得到
        # "整份数据集都要物化"——实测 4 万行数据会一次性插入近 2 万行。
        # 虚拟化的前提就是滚动位置由外部模型自己管。
        self.vsb = ttk.Scrollbar(self, orient="vertical",
                                 command=self._on_scrollbar)
        hsb = ttk.Scrollbar(self, orient="horizontal",
                             command=self.tree.xview)
        self.tree.configure(xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._measure_rowheight()

    def _measure_rowheight(self) -> None:
        try:
            value = ttk.Style(self).lookup("Treeview", "rowheight")
            if value:
                self._rowheight = int(value)
        except (tk.TclError, ValueError, TypeError):
            pass

    def bind_events(self) -> None:
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<<TreeviewOpen>>", self._on_open_close)
        self.tree.bind("<<TreeviewClose>>", self._on_open_close)
        self.tree.bind("<MouseWheel>", self._on_wheel, add="+")
        self.tree.bind("<Button-1>", self._on_click, add="+")
        self.tree.bind("<Key>", self._on_key, add="+")
        self.tree.bind("<Configure>", self._on_configure, add="+")

    # -- 脏标记 ----------------------------------------------------------
    def mark_dirty(self, full: bool = True) -> None:
        """请求重绘。

        ``full=True`` 表示行数或顺序变了，必须整体重建物化窗口。

        注意**不能**在这里把 ``_lo`` / ``_hi`` 清成 None：那两个值是
        "当前 Treeview 里到底有哪些行"的唯一记录，清掉之后 ``_rebuild``
        就没有依据去删除旧行，结果是折叠后残留一堆陈旧行。
        """
        self._dirty = True
        if full:
            self._force_rebuild = True
        self.after_idle(self.pump)

    def set_rule(self, rule: KeepRule) -> None:
        self.rule = rule
        self.mark_dirty(full=False)

    def relayout(self) -> None:
        """DPI / 字号变化后行高变了，可视行数随之变化。"""
        self._measure_rowheight()
        self.mark_dirty(full=True)

    # -- 窗口计算 --------------------------------------------------------
    def _screen_rows(self) -> int:
        height = self.tree.winfo_height()
        if height <= 1:
            height = 400
        return max(1, int(height // max(1, self._rowheight)))

    def _max_offset(self) -> int:
        """最大可滚动偏移，保证最后一屏填满。"""
        total = self.index.total_rows()
        return max(0, total - self._screen_rows())

    def _clamp_offset(self) -> int:
        return max(0, min(self._offset, self._max_offset()))

    def _target_window(self) -> tuple[int, int]:
        """由**自己维护的** ``_offset`` 推出物化窗口。

        绝不使用 ``tree.yview()``——见 ``_build`` 里的说明。
        """
        total = self.index.total_rows()
        if total <= 0:
            return 0, 0
        pad = int(self._screen_rows() * _BUFFER_SCREENS)
        start = self._clamp_offset()
        lo = max(0, start - pad)
        hi = min(total, start + self._screen_rows() + pad)
        return lo, max(hi, lo)

    # -- 滚动 ------------------------------------------------------------
    def _on_scrollbar(self, *args):
        if not args:
            return
        action = str(args[0])
        total = self.index.total_rows()
        if total <= 0:
            return
        if action == "moveto":
            frac = self._vsb.get()[0]
            self._offset = int(frac * total)
        elif action == "scroll":
            amount = int(args[1]) if len(args) > 1 else 0
            unit = str(args[2]) if len(args) > 2 else "units"
            step = amount * (self._screen_rows() if unit == "pages" else 3)
            self._offset += step
        else:
            return
        self._offset = self._clamp_offset()
        self.mark_dirty(full=True)

    def _scroll_by(self, rows: int) -> None:
        self._offset = self._clamp_offset() + rows
        self._offset = self._clamp_offset()
        self.mark_dirty(full=True)

    def scroll_to_group(self, group_index: int) -> None:
        """把某个组滚到可视区顶部（"定位到文件"用）。"""
        row = 0
        for idx in self.index.view:
            if idx == group_index:
                self._offset = row
                break
            row += 1
            if not self.index.is_collapsed(idx):
                row += len(self.index.groups[idx].members)
        self._offset = self._clamp_offset()
        self.mark_dirty(full=True)

    def scroll_to_fraction(self, frac: float) -> None:
        """按比例滚动（0=顶部, 1=底部）。供测试与外部定位使用。"""
        total = self.index.total_rows()
        if total <= 0:
            return
        self._offset = int(max(0.0, min(1.0, frac)) * total)
        self._offset = self._clamp_offset()
        self.mark_dirty(full=True)

    def _sync_scrollbar(self) -> None:
        total = self.index.total_rows()
        if total <= 0:
            try:
                self.vsb.set(0.0, 1.0)
            except tk.TclError:
                pass
            return
        screen = max(1, self._screen_rows())
        start = self._clamp_offset()
        first = min(1.0, start / total)
        last = min(1.0, (start + screen) / total)
        if last <= first:
            last = min(1.0, first + 1.0 / total)
        try:
            self.vsb.set(first, last)
        except tk.TclError:
            pass

    # -- 渲染 ------------------------------------------------------------
    def _row_values(self, spec: RowSpec) -> tuple[str, ...]:
        group = self.index.groups[spec.group_index]
        if spec.kind == ROW_GROUP:
            mark = CHECKED if self.index.is_checked(spec.key) else UNCHECKED
            return (
                mark,
                f"{format_bytes(spec.size)}",
                f"{group.count}",
                format_bytes(group.wasted_bytes),
                "",
                "",
                os.path.dirname(spec.path) if spec.path else "",
            )
        member = group.members[spec.file_index]
        keeper, _ = group.split_by_keeper(self.rule)
        role = ROLE_KEEP if member.path == keeper.path else ROLE_DELETE
        return (
            "",
            format_bytes(member.size),
            "",
            format_bytes(member.size) if role == ROLE_DELETE else "",
            role,
            format_mtime(member.mtime),
            member.parent,
        )

    def _row_text(self, spec: RowSpec) -> str:
        if spec.kind == ROW_GROUP:
            return f" {spec.label}"
        return f"    {spec.label}"

    def _insert(self, row: int, text: str, values: tuple[str, ...],
                open_: bool = False) -> None:
        self.tree.insert(
            "", "end", iid=f"r{row}", text=text, values=values, open=open_
        )

    def _delete_range(self, lo: int, hi: int) -> None:
        for row in range(lo, hi):
            iid = f"r{row}"
            if self.tree.exists(iid):
                self.tree.delete(iid)

    def pump(self) -> None:
        """把目标窗口物化到 Treeview。必须跑在 UI 线程。"""
        if self._suspend or not self._dirty:
            return
        self._dirty = False

        total = self.index.total_rows()
        if total <= 0:
            self._delete_range(0, self._hi or 0)
            self._lo = self._hi = 0
            self._offset = 0
            self._sync_scrollbar()
            self._update_empty_state()
            return

        self._offset = self._clamp_offset()
        lo, hi = self._target_window()

        need_rebuild = (
            self._force_rebuild
            or self._lo is None
            or self._hi is None
            or hi <= self._lo
            or lo >= self._hi
            or lo > self._hi
            or hi < self._lo
        )
        if need_rebuild:
            self._rebuild(lo, hi)
        else:
            self._slide(lo, hi)
            # 滑窗只保证"行在正确的位置"，不保证"内容是最新的"。
            # 勾选状态、保留规则变化都走这条路径，必须原地刷新文本与列值。
            self._refresh_cells(lo, hi)
        self._force_rebuild = False
        self._sync_scrollbar()
        self._update_empty_state()

    def _refresh_cells(self, lo: int, hi: int) -> None:
        """就地刷新已物化行的文本与列值（只处理可视区，很便宜）。"""
        self._suspend = True
        try:
            for row in range(lo, hi):
                iid = f"r{row}"
                if not self.tree.exists(iid):
                    continue
                spec = self.index.locate(row)
                if spec is None:
                    continue
                self.tree.item(iid, text=self._row_text(spec),
                               values=self._row_values(spec))
        finally:
            self._suspend = False

    def _rebuild(self, lo: int, hi: int) -> None:
        # 依据 _hi（而非树里实际有多少行）删除旧窗口，再插入新窗口
        self._delete_range(0, max(self._hi or 0, hi))
        self._lo = lo
        self._hi = hi
        self._suspend = True
        try:
            for row in range(lo, hi):
                spec = self.index.locate(row)
                if spec is None:
                    continue
                self._insert(row, self._row_text(spec), self._row_values(spec),
                             open_=not self.index.is_collapsed(spec.group_index)
                             if spec.kind == ROW_GROUP else False)
        finally:
            self._suspend = False

    def _slide(self, lo: int, hi: int) -> None:
        assert self._lo is not None and self._hi is not None
        self._suspend = True
        try:
            # 前面补：在 index 0..k 逐个前插（倒序插，保证顺序正确）
            if lo < self._lo:
                for row in range(lo, self._lo):
                    spec = self.index.locate(row)
                    if spec is None:
                        continue
                    self._insert(row, self._row_text(spec), self._row_values(spec),
                                 open_=not self.index.is_collapsed(spec.group_index)
                                 if spec.kind == ROW_GROUP else False)
            # 后面补
            if hi > self._hi:
                for row in range(self._hi, hi):
                    spec = self.index.locate(row)
                    if spec is None:
                        continue
                    self._insert(row, self._row_text(spec), self._row_values(spec),
                                 open_=not self.index.is_collapsed(spec.group_index)
                                 if spec.kind == ROW_GROUP else False)
        finally:
            self._suspend = False

        # 裁掉越界的两端
        if lo > self._lo:
            self._delete_range(self._lo, lo)
        if hi < self._hi:
            self._delete_range(hi, self._hi)
        self._lo, self._hi = lo, hi

    def _update_empty_state(self) -> None:
        if len(self.index) == 0:
            self.on_status("没有匹配的重复组（试试放宽筛选条件）")

    # -- 事件 ------------------------------------------------------------
    def _row_of_event(self, event) -> Optional[RowSpec]:
        iid = self.tree.identify_row(event.y)
        if not iid or not iid.startswith("r"):
            return None
        try:
            return self.index.locate(int(iid[1:]))
        except ValueError:
            return None

    def _on_click(self, event) -> None:
        spec = self._row_of_event(event)
        if spec is None:
            return
        # 用 identify_column 判断点在哪一列，别用像素宽度猜——
        # 列宽会被用户拖动、也会随 DPI 变。
        column = self.tree.identify_column(event.x)
        if column == f"#{self.COLUMNS[0][0]}" or column == self.COLUMNS[0][0]:
            self.toggle_check(spec)
            return
        if spec.kind == ROW_GROUP and column == "#0":
            # 树列：点箭头附近展开/折叠，点行内其它位置不改变状态
            if event.x < 22:
                self.index.toggle_collapsed(spec.group_index)
                self.mark_dirty(full=True)

    def _on_wheel(self, event) -> None:
        num = getattr(event, "num", None)
        if num == 4:
            self._scroll_by(-3)
        elif num == 5:
            self._scroll_by(3)
        else:
            delta = getattr(event, "delta", 0)
            # Windows 上 delta 是 ±120 的倍数；Linux/旧 macOS 可能更大
            steps = -3 if delta > 0 else 3
            self._scroll_by(steps * max(1, abs(delta) // 120))

    def _on_key(self, event) -> None:
        keysym = event.keysym
        if keysym in ("Right", "plus", "add"):
            self._expand_current()
        elif keysym in ("Left", "minus", "subtract"):
            self._collapse_current()
        elif keysym == "space":
            spec = self._current_spec()
            if spec is not None and spec.kind == ROW_GROUP:
                self.toggle_check(spec)
        elif keysym == "Prior":      # PageUp
            self._scroll_by(-self._screen_rows())
        elif keysym == "Next":       # PageDown
            self._scroll_by(self._screen_rows())
        elif keysym == "Home":
            self._offset = 0
            self.mark_dirty(full=True)
        elif keysym == "End":
            self._offset = self._max_offset()
            self.mark_dirty(full=True)
        else:
            self._scroll_by(3 if keysym == "Down" else -3)

    def _on_select(self, _event=None) -> None:
        self.mark_dirty(full=False)

    def _on_open_close(self, _event=None) -> None:
        spec = self._current_spec()
        if spec is not None and spec.kind == ROW_GROUP:
            self.index.set_collapsed(
                spec.group_index, bool(self.index.is_collapsed(spec.group_index))
            )
            self.mark_dirty(full=True)

    def _on_configure(self, _event=None) -> None:
        # 窗口尺寸变了 -> 可视行数变了 -> 物化窗口与滚动条都要重算
        self.mark_dirty(full=True)

    def _on_heading(self, column: str) -> None:
        mapping = {
            "size": SortMode.SIZE,
            "count": SortMode.COUNT,
            "reclaim": SortMode.RECLAIMABLE,
            "mtime": SortMode.MTIME,
            "path": SortMode.PATH,
        }
        mode = mapping.get(column)
        if mode is None:
            return
        if self.index.sort_mode == mode:
            self.index.toggle_sort_direction()
        else:
            self.index.set_sort(mode)
        self.mark_dirty(full=True)
        self.on_status(
            f"排序：{SortMode.LABELS[mode]} "
            f"{'降序' if self.index.sort_reverse else '升序'}"
        )

    def _current_spec(self) -> Optional[RowSpec]:
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        if not iid.startswith("r"):
            return None
        try:
            return self.index.locate(int(iid[1:]))
        except ValueError:
            return None

    def _expand_current(self) -> None:
        spec = self._current_spec()
        if spec is not None and spec.kind == ROW_GROUP:
            self.index.set_collapsed(spec.group_index, False)
            self.mark_dirty(full=True)

    def _collapse_current(self) -> None:
        spec = self._current_spec()
        if spec is not None and spec.kind == ROW_GROUP:
            self.index.set_collapsed(spec.group_index, True)
            self.mark_dirty(full=True)

    def toggle_check(self, spec: RowSpec) -> None:
        if spec.kind != ROW_GROUP:
            return
        self.index.set_checked(spec.key, not self.index.is_checked(spec.key))
        # 只刷新该组及其成员行的可见部分，不动其它行的位置
        if self._lo is not None and self._hi is not None:
            self._refresh_cells(self._lo, self._hi)
        if hasattr(self, "on_check_changed"):
            self.on_check_changed()
