"""主窗口 + 控制器。"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from ..engine import Progress, ScanEngine
from ..model import KeepRule, Phase, ScanResult, format_bytes
from ..ops import report, trash
from .bus import CancelToken, EventBus
from .panels import ScanPanel
from .theme import Fonts, build_style, enable_dpi_awareness
from .viewmodel import ResultIndex, audit_plan, build_plan, keep_rule_label
from .widgets import VirtualGroupTree

_PUMP_MS = 90


class MainWindow:
    """所有 Tk 交互都发生在这里（主线程）。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.style = build_style(root)
        self.fonts = Fonts(root)

        self.index: Optional[ResultIndex] = None
        self.result: Optional[ScanResult] = None
        self.tree: Optional[VirtualGroupTree] = None
        self._bus: Optional[EventBus] = None
        self._token: Optional[CancelToken] = None
        self._worker: Optional[threading.Thread] = None
        self._busy = False
        self._scan_started = 0.0

        self.root.title("Dump Search · 全盘重复文件查找")
        root.geometry("1280x800")
        root.minsize(980, 600)

        self._build()
        self._set_busy(False)
        # PanedWindow 的初始分隔位置：左栏 460px，其余全给结果表。
        # 必须在窗口完成一次布局之后再设——太早调用时几何信息还不准，
        # 会被子控件的请求宽度覆盖。
        self.root.after(60, self._place_sash)
        self.root.after(300, self._place_sash)

    def _place_sash(self) -> None:
        try:
            for child in self.root.winfo_children():
                for sub in child.winfo_children():
                    if isinstance(sub, ttk.PanedWindow):
                        sub.sashpos(0, 460)
                        return
        except tk.TclError:
            pass

    # ==================================================================
    # 布局
    # ==================================================================
    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)

        # 顶部工具条
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(0, 6))
        self.btn_scan = ttk.Button(bar, text="开始扫描", width=12,
                                   command=self.on_scan)
        self.btn_scan.pack(side="left")
        self.btn_cancel = ttk.Button(bar, text="停止", width=8,
                                     command=self.on_cancel)
        self.btn_cancel.pack(side="left", padx=4)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        self.btn_export = ttk.Button(bar, text="导出 CSV", width=10,
                                     command=self.on_export_csv, state="disabled")
        self.btn_export.pack(side="left", padx=2)
        self.btn_script = ttk.Button(bar, text="生成删除脚本", width=14,
                                     command=self.on_export_script, state="disabled")
        self.btn_script.pack(side="left", padx=2)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        self.btn_trash = ttk.Button(bar, text="预览并清理…", width=13,
                                    command=self.on_clean, state="disabled")
        self.btn_trash.pack(side="left", padx=2)

        # 主体：左配置 + 右结果。用 PanedWindow，让用户能自己拖宽度。
        # 之前用 grid + weight 时，"排除目录名"那行很长的输入框会把左栏
        # 撑到 1000px，把结果表挤成一条缝（所有列都被推出可视区）。
        body = ttk.PanedWindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)

        left_holder = ttk.Frame(body)
        self.scan_panel = ScanPanel(left_holder, self.fonts)
        self.scan_panel.pack(fill="both", expand=True)

        right = ttk.Frame(body)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        # 结果工具条
        self.filter_bar = ttk.Frame(right)
        self.filter_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(self.filter_bar, text="筛选：").pack(side="left")
        self.filter_var = tk.StringVar()
        entry = ttk.Entry(self.filter_bar, textvariable=self.filter_var, width=26)
        entry.pack(side="left", padx=2)
        entry.bind("<KeyRelease>", lambda _e: self._apply_filter())
        ttk.Label(self.filter_bar, text="name: / path: / ext:").pack(
            side="left", padx=2)
        ttk.Label(self.filter_bar, text="最小分组体积：").pack(side="left",
                                                               padx=(8, 0))
        self.min_group_var = tk.StringVar(value="")
        me = ttk.Entry(self.filter_bar, textvariable=self.min_group_var, width=7)
        me.pack(side="left", padx=2)
        me.bind("<KeyRelease>", lambda _e: self._apply_filter())
        for text, cmd in (("全选", self._check_all), ("全不选", self._check_none),
                          ("反选", self._invert)):
            ttk.Button(self.filter_bar, text=text, width=6,
                       command=cmd).pack(side="left", padx=2)
        for text, cmd in (("展开", self._expand_all), ("折叠", self._collapse_all)):
            ttk.Button(self.filter_bar, text=text, width=6,
                       command=cmd).pack(side="left", padx=(8 if cmd is
                                                           self._expand_all else 2, 0))

        # 表格容器
        table_frame = ttk.Frame(right)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.table_holder = table_frame

        body.add(left_holder, weight=0)
        body.add(right, weight=1)

        # 进度区
        prog = ttk.Frame(right)
        prog.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        prog.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(prog, mode="determinate", maximum=1000)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.phase_label = ttk.Label(prog, text="就绪")
        self.phase_label.grid(row=1, column=0, sticky="w", pady=(3, 0))
        self.stats_label = ttk.Label(prog, text="", foreground="#666666")
        self.stats_label.grid(row=1, column=1, sticky="e", pady=(3, 0))

        # 底部状态栏
        status = ttk.Frame(outer)
        status.pack(fill="x", pady=(6, 0))
        self.status_var = tk.StringVar(value="就绪。选择盘符后点「开始扫描」。")
        ttk.Label(status, textvariable=self.status_var).pack(side="left")
        self.result_label = ttk.Label(status, text="")
        self.result_label.pack(side="right")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ==================================================================
    # 状态
    # ==================================================================
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.btn_scan.configure(state="disabled" if busy else "normal")
        self.btn_cancel.configure(state="normal" if busy else "disabled")
        for btn in (self.btn_export, self.btn_script, self.btn_trash):
            btn.configure(
                state="disabled" if busy or self.index is None else "normal"
            )

    def status(self, text: str) -> None:
        self.status_var.set(text)

    def _apply_filter(self) -> None:
        if self.index is None:
            return
        from .panels import parse_size

        self.index.set_filter_text(self.filter_var.get())
        self.index.set_min_size(parse_size(self.min_group_var.get(), 0))
        if self.tree:
            self.tree.mark_dirty(full=True)
        self._refresh_result_label()

    def _refresh_result_label(self) -> None:
        if self.index is None:
            self.result_label.configure(text="")
            return
        groups, files = self.index.checked_stats()
        reclaim = sum(
            self.index.groups[i].wasted_bytes
            for i in self.index.view
            if self.index.is_checked(f"g:{i}")
        )
        self.result_label.configure(
            text=(
                f"显示 {len(self.index):,}/{len(self.index.groups):,} 组 · "
                f"已勾选 {groups:,} 组 / {files:,} 个文件 · "
                f"可释放 {format_bytes(reclaim)}"
            )
        )

    def _check_all(self) -> None:
        if self.index:
            self.index.check_all()
            self._after_check_change()

    def _check_none(self) -> None:
        if self.index:
            self.index.check_none()
            self._after_check_change()

    def _invert(self) -> None:
        if self.index:
            self.index.invert()
            self._after_check_change()

    def _expand_all(self) -> None:
        if self.index and self.tree:
            self.index.expand_all()
            self.tree.mark_dirty(full=True)

    def _collapse_all(self) -> None:
        if self.index and self.tree:
            self.index.collapse_all()
            self.tree.mark_dirty(full=True)

    def _after_check_change(self) -> None:
        if self.tree:
            self.tree.mark_dirty(full=False)
        self._refresh_result_label()

    # ==================================================================
    # 扫描
    # ==================================================================
    def on_scan(self) -> None:
        if self._busy:
            return
        config = self.scan_panel.build_config()
        if not config.roots:
            messagebox.showinfo("请选择范围", "请至少勾选一个盘符或添加一个目录。")
            return

        self._busy = True
        self._set_busy(True)
        self.progress.configure(value=0)
        self.result_label.configure(text="")
        self.status("正在扫描…")
        self._scan_started = time.time()

        self._token = CancelToken()
        self._bus = EventBus()
        bus, token = self._bus, self._token

        engine = ScanEngine(
            config, cancel=token.event, on_progress=bus.publish_progress
        )

        def work() -> None:
            # 整个生命周期都在工作线程；只有这一处能碰 bus
            try:
                outcome = engine.run()
            except Exception as exc:  # pragma: no cover
                bus.publish_finished(None, f"{type(exc).__name__}: {exc}")
            else:
                bus.publish_finished(outcome)

        self._worker = threading.Thread(target=work, name="dh-scan", daemon=True)
        self._worker.start()
        self._pump()

    def on_cancel(self) -> None:
        if self._token:
            self._token.cancel()
            self.status("正在停止…（当前系统调用可能需要几秒才能返回）")
            self.btn_cancel.configure(state="disabled")

    def _pump(self) -> None:
        """消费进度快照的 ``root.after`` 泵。

        必须 ``try/finally`` 重挂：一次 dispatch 抛异常就会让泵永久死掉，
        症状是"扫描还在跑但界面再也不更新"。
        """
        try:
            if self._bus is not None:
                snapshot = self._bus.take_progress()
                if snapshot is not None:
                    self._render_progress(snapshot)
                if self._bus.is_finished():
                    self._finish_scan()
                    return
        except Exception as exc:  # pragma: no cover
            self.status(f"界面刷新出错：{exc}")
        finally:
            if self._bus is not None and not self._bus.is_finished():
                self.root.after(_PUMP_MS, self._pump)

    def _render_progress(self, p: Progress) -> None:
        if p.phase is Phase.SCANNING:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            if str(self.progress.cget("mode")) == "indeterminate":
                self.progress.stop()
                self.progress.configure(mode="determinate")
            self.progress.configure(value=int(p.fraction * 1000))

        self.phase_label.configure(text=f"{p.phase.value}  {p.detail}".strip())
        rate = f"{format_bytes(p.speed_bps)}/s" if p.speed_bps else "—"
        eta = f"，剩余约 {int(p.eta_seconds // 60)}分{int(p.eta_seconds % 60)}秒" \
            if p.eta_seconds else ""
        self.stats_label.configure(
            text=f"{p.done:,}/{p.total:,}  {rate}{eta}"
        )

    def _finish_scan(self) -> None:
        assert self._bus is not None
        self.progress.stop()
        self.progress.configure(mode="determinate", value=1000)

        result = self._bus.finish_result()
        error = self._bus.finish_error()
        self._bus = None
        self._set_busy(False)

        if error or result is None:
            self.status(f"扫描出错：{error}")
            messagebox.showerror("扫描出错", str(error))
            return

        self.result = result
        self.index = ResultIndex(result)

        self.tree = VirtualGroupTree(
            self.table_holder, self.index, self.fonts,
            rule=result.config.keep_rule, on_status=self.status,
        )
        self.tree.pack(fill="both", expand=True)
        self.tree.on_check_changed = self._refresh_result_label

        elapsed = time.time() - self._scan_started
        self.phase_label.configure(text=result.phase.value)
        self.status(f"{result.summary()}  用时 {elapsed:.1f}s")
        self._refresh_result_label()
        self._set_busy(False)

        if not result.groups:
            messagebox.showinfo(
                "扫描完成",
                f"没有发现重复文件。\n\n{result.summary()}\n\n"
                "如果预期应该有重复项，可以调低「文件大小」下限、"
                "清空「排除目录名」，或包含 0 字节文件。",
            )

    # ==================================================================
    # 清理
    # ==================================================================
    def on_clean(self) -> None:
        if self.index is None or not self.index.view:
            return
        rule = KeepRule(self.scan_panel.keep_var.get())
        self.index.set_keep_rule(rule)
        if self.tree:
            self.tree.set_rule(rule)

        plan = build_plan(self.index, rule)
        warnings = audit_plan(plan, self.index)
        approved = ConfirmDialog(self.root, self.fonts, plan, rule, warnings).ask()
        if not approved:
            self.status("已取消，未删除任何文件。")
            return

        # 确认框销毁之后再调度，避免在 wait_window 的嵌套事件循环里动文件
        self.root.after(0, lambda: self._run_delete(plan))

    def _run_delete(self, plan) -> None:
        self._set_busy(True)
        self.status("正在移入回收站…")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)

        targets = plan.unique_targets()
        done_box = [0]

        def work() -> None:
            def on_progress(d: int, _t: int) -> None:
                done_box[0] = d
                self.root.after(
                    0,
                    lambda d=d: self.phase_label.configure(
                        text=f"移入回收站  {d:,}/{len(targets):,}"
                    ),
                )

            outcome = trash.trash_files(targets, progress=on_progress)
            self.root.after(0, lambda: self._delete_done(outcome, plan))

        threading.Thread(target=work, name="dh-trash", daemon=True).start()

    def _delete_done(self, outcome, plan) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=1000)
        self._set_busy(False)

        message = outcome.summary()
        if outcome.failed:
            message += f"\n\n失败明细（前 15 条）：\n"
            for path, reason in outcome.failed[:15]:
                message += f"  {path}\n    {reason}\n"
        message += (
            f"\n这些文件已进入回收站，可随时还原。\n"
            f"清空回收站后才会真正释放 {format_bytes(plan.reclaimable)}。"
        )
        self.status(outcome.summary())
        self.phase_label.configure(text="清理完成")

        if outcome.failed:
            messagebox.showwarning("部分失败", message)
        else:
            messagebox.showinfo("清理完成", message)

        # 结果已过期：重新扫描才能拿到准确状态
        self.status("文件已变动，建议重新扫描以刷新结果。")
        self.index = None
        self.result = None
        if self.tree:
            self.tree.destroy()
            self.tree = None
        self._set_busy(False)

    # ==================================================================
    # 导出
    # ==================================================================
    def on_export_csv(self) -> None:
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(
            title="导出 CSV 报告",
            defaultextension=".csv",
            initialfile="duplicates.csv",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            report.export_csv(self.result, path)
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self.status(f"已导出 CSV：{path}")

    def on_export_script(self) -> None:
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(
            title="生成删除脚本",
            defaultextension=".ps1",
            initialfile="remove_duplicates.ps1",
            filetypes=[("PowerShell 脚本", "*.ps1")],
        )
        if not path:
            return
        rule = KeepRule(self.scan_panel.keep_var.get())
        text = report.build_deletion_script(self.result.groups, rule)
        with open(path, "w", encoding="utf-8-sig") as fh:
            fh.write(text)
        self.status(f"已生成脚本（请人工审阅后再执行）：{path}")

    # ==================================================================
    def on_close(self) -> None:
        if self._busy and self._token:
            if not messagebox.askokcancel(
                "仍在运行", "扫描或清理还在进行，确定要退出吗？"
            ):
                return
            self._token.cancel()
        self.root.destroy()


class ConfirmDialog:
    """删除前的预览 + 二次确认。

    设计上刻意让"点错"变难：必须滚动看过清单、勾选确认框、点按钮。
    ``wait_window`` 本身是安全的（嵌套的 Tcl 事件循环照样服务 ``after``
    泵，进度条会继续动）。
    """

    def __init__(self, parent, fonts, plan, rule, warnings) -> None:
        self.parent = parent
        self.fonts = fonts
        self.plan = plan
        self.rule = rule
        self.warnings = warnings
        self.approved = False
        self.dlg: Optional[tk.Toplevel] = None
        self.ack = tk.BooleanVar(value=False)

    def ask(self) -> bool:
        dlg = tk.Toplevel(self.parent)
        self.dlg = dlg
        dlg.title("清理预览与确认")
        dlg.geometry("900x640")
        dlg.transient(self.parent)
        dlg.protocol("WM_DELETE_WINDOW", self._cancel)
        dlg.columnconfigure(0, weight=1)
        dlg.rowconfigure(2, weight=1)

        header = ttk.Frame(dlg, padding=10)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=self.plan.summary(), font=self.fonts.title,
                  wraplength=860, justify="left").pack(anchor="w")
        ttk.Label(header, text=f"保留策略：{keep_rule_label(self.rule)}").pack(
            anchor="w", pady=(4, 0))

        if self.warnings:
            warn_box = ttk.LabelFrame(dlg, text="请注意", padding=8)
            warn_box.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
            for text in self.warnings:
                ttk.Label(warn_box, text=f"• {text}", wraplength=850,
                          justify="left", foreground="#b35c00").pack(anchor="w")

        import tkinter.scrolledtext as st

        text = st.ScrolledText(dlg, font=self.fonts.mono_tuple, wrap="none")
        text.grid(row=2, column=0, sticky="nsew", padx=10)
        lines = self.plan.preview_lines(2000)
        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")

        foot = ttk.Frame(dlg, padding=10)
        foot.grid(row=3, column=0, sticky="ew")
        ttk.Label(
            foot,
            text="这些文件会被移入回收站（可还原），不会立即永久删除。",
            foreground="#666666",
        ).pack(anchor="w")
        ttk.Checkbutton(
            foot, variable=self.ack,
            text="我已核对上面的清单，确认这些副本可以移入回收站",
            command=self._sync_ok,
        ).pack(anchor="w", pady=(6, 0))

        buttons = ttk.Frame(foot)
        buttons.pack(anchor="e", pady=(8, 0))
        ttk.Button(buttons, text="取消", width=10,
                   command=self._cancel).pack(side="right", padx=4)
        self.ok_btn = ttk.Button(buttons, text="确认移入回收站", width=16,
                                 command=self._confirm, state="disabled")
        self.ok_btn.pack(side="right", padx=4)

        dlg.grab_set()
        dlg.focus_set()
        self.parent.wait_window(dlg)
        return self.approved

    def _sync_ok(self) -> None:
        self.ok_btn.configure(
            state="normal" if self.ack.get() and self.plan.file_count else "disabled"
        )

    def _confirm(self) -> None:
        if not self.ack.get():
            return
        self.approved = True
        if self.dlg:
            self.dlg.destroy()

    def _cancel(self) -> None:
        self.approved = False
        if self.dlg:
            self.dlg.destroy()


def main(argv: Optional[list[str]] = None) -> int:
    enable_dpi_awareness()
    root = tk.Tk()
    window = MainWindow(root)
    try:
        root.mainloop()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    return 0
