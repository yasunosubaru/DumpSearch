"""Tkinter 主窗口与控制器。

线程纪律（本项目最关键的一条）：

* **只有主线程碰任何 Tk 对象。** ``_tkinter`` 不是"线程不安全"这么简单——
  它有一把全局 Tcl 锁，跨线程调用会被**串行化**而不是踩内存，但仍然不受
  支持：主线程不在 mainloop 时抛 ``RuntimeError: main thread is not in
  main loop``；工作线程里调 ``Dialog`` / ``mainloop`` / ``grab_set`` 会
  扣住解释器锁，界面直接冻住、连取消按钮都没反应。
* 扫描、哈希、删除全部在 ``threading.Thread`` 里跑，只通过
  :class:`EventBus` 往外发**不可变快照**。
* UI 侧由一个 ``root.after`` 泵消费快照。**泵必须用 ``try/finally`` 重挂**：
  一次 dispatch 抛异常就会让泵永久死掉，症状是"扫描还在跑但界面再也不
  更新"——极难定位。
* 不用 ``asyncio``：``root.mainloop()`` 是阻塞的 Tcl ``vwait``，进去以后
  再也不会回到 Python 字节码，事件循环因此停摆。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk

from .. import winpath
from ..model import KeepRule, ScanConfig, parse_size
from .theme import Fonts
from .viewmodel import keep_rule_label

# 盘符复选框每行放几个（太少会让本栏太宽，挤掉右侧结果表）
_DRIVE_COLUMNS = 3


class ScanPanel(ttk.LabelFrame):
    """扫描范围与筛选条件。"""

    def __init__(self, master: tk.Misc, fonts: Fonts) -> None:
        super().__init__(master, text="扫描范围", padding=8)
        self.fonts = fonts
        self.drive_vars: dict[str, tk.BooleanVar] = {}
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)

        # --- 盘符 ---
        # 必须换行排布：7 个盘符排成一行会把本栏请求宽度顶到 900+px，
        # 从而把右侧结果表挤成一条缝（所有数据列都被挤出可视区）。
        drives_box = ttk.Frame(self)
        drives_box.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        drives_box.columnconfigure(0, weight=1)

        grid = ttk.Frame(drives_box)
        grid.grid(row=0, column=0, sticky="ew")
        for col in range(_DRIVE_COLUMNS):
            grid.columnconfigure(col, weight=1, uniform="drive")

        for i, drive in enumerate(winpath.list_drives()):
            var = tk.BooleanVar(value=drive.type_name != "光驱")
            self.drive_vars[drive.root] = var
            text = f"{drive.root} {drive.label}".strip()
            ttk.Checkbutton(grid, text=text, variable=var,
                            width=max(12, len(text) + 1)).grid(
                row=i // _DRIVE_COLUMNS, column=i % _DRIVE_COLUMNS,
                sticky="w", padx=1, pady=1)

        ttk.Button(drives_box, text="全选", width=6,
                   command=self.select_all_drives).grid(row=1, column=0,
                                                        sticky="w", pady=(2, 0))
        ttk.Button(drives_box, text="全不选", width=8,
                   command=self.clear_drives).grid(row=1, column=0, sticky="w",
                                                   padx=(60, 0), pady=(2, 0))
        ttk.Button(drives_box, text="添加目录…", width=10,
                   command=self.pick_dirs).grid(row=1, column=0, sticky="w",
                                                padx=(130, 0), pady=(2, 0))

        # --- 手动目录 ---
        ttk.Label(self, text="附加目录（每行一个）：").grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        import tkinter.scrolledtext as st

        self.dirs_text = st.ScrolledText(self, height=3, width=34,
                                         font=self.fonts.mono_tuple, wrap="none")
        self.dirs_text.grid(row=2, column=0, sticky="ew", pady=(2, 6))

        # --- 排除目录 ---
        exclude_row = ttk.Frame(self)
        exclude_row.grid(row=3, column=0, sticky="ew")
        ttk.Label(exclude_row, text="排除目录名：").pack(side="left")
        self.exclude_var = tk.StringVar(value=", ".join(winpath.DEFAULT_EXCLUDE_DIRS))
        ttk.Entry(exclude_row, textvariable=self.exclude_var).pack(
            side="left", fill="x", expand=True, padx=4)
        ttk.Button(exclude_row, text="清空", width=5,
                   command=lambda: self.exclude_var.set("")).pack(side="left")

        # --- 大小区间 ---
        size_row = ttk.Frame(self)
        size_row.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(size_row, text="文件大小：").pack(side="left")
        self.min_size_var = tk.StringVar(value="1")
        ttk.Entry(size_row, textvariable=self.min_size_var, width=7).pack(side="left")
        ttk.Label(size_row, text="—").pack(side="left", padx=2)
        self.max_size_var = tk.StringVar(value="")
        ttk.Entry(size_row, textvariable=self.max_size_var, width=7).pack(side="left")
        ttk.Label(size_row, text="（如 10M；留空表示不限）").pack(side="left", padx=4)

        # --- 扩展名 ---
        ext_row = ttk.Frame(self)
        ext_row.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(ext_row, text="只查扩展名：").pack(side="left")
        self.include_var = tk.StringVar(value="")
        ttk.Entry(ext_row, textvariable=self.include_var, width=18).pack(side="left")
        ttk.Label(ext_row, text="  排除：").pack(side="left")
        self.exclude_ext_var = tk.StringVar(value=".tmp,.log,.bak")
        ttk.Entry(ext_row, textvariable=self.exclude_ext_var, width=18).pack(
            side="left", fill="x", expand=True, padx=4)

        # --- 保留规则 ---
        keep_row = ttk.Frame(self)
        keep_row.grid(row=6, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(keep_row, text="每组保留：").pack(side="left")
        self.keep_var = tk.StringVar(value=KeepRule.NEWEST.value)
        combo = ttk.Combobox(keep_row, textvariable=self.keep_var, width=16,
                             state="readonly", values=[r.value for r in KeepRule])
        combo.pack(side="left")
        self.keep_hint = ttk.Label(keep_row, text=keep_rule_label(KeepRule.NEWEST))
        self.keep_hint.pack(side="left", padx=6)

        # --- 选项 ---
        opt_row = ttk.Frame(self)
        opt_row.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        self.verify_var = tk.BooleanVar(value=True)
        self.empty_var = tk.BooleanVar(value=False)
        self.symlink_var = tk.BooleanVar(value=False)
        self.hardlink_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_row, text="逐字节校验", variable=self.verify_var).pack(
            side="left")
        ttk.Checkbutton(opt_row, text="含 0 字节", variable=self.empty_var).pack(
            side="left")
        ttk.Checkbutton(opt_row, text="跟随链接", variable=self.symlink_var).pack(
            side="left")
        ttk.Checkbutton(opt_row, text="折叠硬链接",
                        variable=self.hardlink_var).pack(side="left")

        worker_row = ttk.Frame(self)
        worker_row.grid(row=8, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(worker_row, text="哈希线程：").pack(side="left")
        self.worker_var = tk.StringVar(value="0（自动）")
        ttk.Combobox(worker_row, textvariable=self.worker_var, width=10,
                     state="readonly",
                     values=["0（自动）", "4", "8", "16", "24", "32"]).pack(side="left")
        self.worker_hint = ttk.Label(worker_row, text="")
        self.worker_hint.pack(side="left", padx=6)

    # -- 交互 ------------------------------------------------------------
    def select_all_drives(self) -> None:
        for var in self.drive_vars.values():
            var.set(True)

    def clear_drives(self) -> None:
        for var in self.drive_vars.values():
            var.set(False)

    def pick_dirs(self) -> None:
        chosen = filedialog.askdirectory(title="选择要扫描的目录", mustexist=True)
        if chosen:
            current = self.dirs_text.get("1.0", "end").strip()
            self.dirs_text.delete("1.0", "end")
            self.dirs_text.insert("1.0", (current + "\n" if current else "") + chosen)

    def selected_roots(self) -> list[str]:
        roots = [d for d, var in self.drive_vars.items() if var.get()]
        text = self.dirs_text.get("1.0", "end")
        roots.extend(line.strip() for line in text.splitlines() if line.strip())
        return winpath.unique_paths(roots)

    def build_config(self) -> ScanConfig:
        def split(text: str) -> set[str]:
            out = set()
            for part in (text or "").split(","):
                part = part.strip().lower()
                if part:
                    out.add(part if part.startswith(".") else "." + part)
            return out

        excludes = {
            part.strip() for part in self.exclude_var.get().split(",")
            if part.strip()
        }
        workers = parse_size(self.worker_var.get().split("（")[0], 0)

        return ScanConfig(
            roots=self.selected_roots(),
            exclude_dirs=excludes,
            include_suffixes=split(self.include_var.get()),
            exclude_suffixes=split(self.exclude_ext_var.get()),
            min_size=max(1, parse_size(self.min_size_var.get(), 1)),
            max_size=parse_size(self.max_size_var.get(), 0) or None,
            include_empty_files=self.empty_var.get(),
            follow_symlinks=self.symlink_var.get(),
            verify_bytes=self.verify_var.get(),
            treat_hardlinks_as_one=self.hardlink_var.get(),
            workers=workers,
            keep_rule=KeepRule(self.keep_var.get()),
        )
