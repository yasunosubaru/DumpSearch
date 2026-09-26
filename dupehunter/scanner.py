"""阶段 0/1：目录遍历。

用 ``os.scandir`` 做显式栈迭代遍历，原因：

* ``scandir`` 在 Windows 上能直接拿到 ``st_size`` / ``st_mtime``，
  **避免对每个文件额外 ``stat()``**（全盘场景下这是最大的常数开销）。
* 显式栈比递归更可控：可以随时检查取消标志、限制深度、避免深目录爆栈。

遍历同时完成"按大小预筛"，把不可能重复的文件当场丢掉，
不进入后续的哈希阶段。
"""

from __future__ import annotations

import os
import threading
from typing import Callable, Iterable, Iterator, Optional

from .model import FileEntry, ScanConfig, ScanStats
from . import winpath

ProgressFn = Callable[[ScanStats], None]

# 每处理这么多目录就回调一次进度（避免回调过于频繁）
_PROGRESS_INTERVAL = 400

# 名称里出现这些片段时通常也跳过（可被用户覆盖）
_HIDDEN_ATTR = 0x02  # FILE_ATTRIBUTE_HIDDEN
_SYSTEM_ATTR = 0x04  # FILE_ATTRIBUTE_SYSTEM
_DIR_ATTR = 0x10  # FILE_ATTRIBUTE_DIRECTORY


class Scanner:
    """把若干根目录遍历成 :class:`FileEntry` 序列。"""

    def __init__(
        self,
        config: ScanConfig,
        stats: Optional[ScanStats] = None,
        cancel: Optional[threading.Event] = None,
        on_progress: Optional[ProgressFn] = None,
    ) -> None:
        self.config = config
        self.stats = stats if stats is not None else ScanStats()
        self.cancel = cancel if cancel is not None else threading.Event()
        self.on_progress = on_progress
        self._exclude_names = {name.lower() for name in config.exclude_dirs}
        self._exclude_paths = {
            os.path.normcase(winpath.normalize(p)) for p in config.exclude_dirs
        }
        self._include = {s.lower() if s.startswith(".") else s.lower()
                         for s in config.include_suffixes}
        self._exclude_suffixes = {s.lower() for s in config.exclude_suffixes}

    # -- 硬链接折叠 ------------------------------------------------------
    #
    # 为什么放在这里而不是遍历时：Windows 上 ``os.DirEntry.stat()`` 只用
    # ``FindFirstFile`` 的结果填充字段，**``st_ino`` 和 ``st_nlink`` 恒为 0**
    # （实测硬链接文件也一样），所以遍历阶段拿不到任何硬链接线索。
    # 必须对候选文件再做一次完整的 ``os.stat()``。
    #
    # 代价控制：只有"大小相同、可能重复"的文件才需要这次额外 stat，
    # 相对全盘几十万文件可忽略不计。
    @staticmethod
    def collapse_hardlinks(
        groups: list[list[FileEntry]], stats: ScanStats
    ) -> list[list[FileEntry]]:
        """把指向同一物理文件的多个路径折叠成一个。"""
        out: list[list[FileEntry]] = []
        for group in groups:
            seen: set[tuple[int, int]] = set()
            kept: list[FileEntry] = []
            for entry in group:
                try:
                    st = os.stat(entry.path)
                except OSError:
                    kept.append(entry)  # 取不到就保守保留
                    continue
                nlink = getattr(st, "st_nlink", 1)
                if nlink > 1:
                    key = (st.st_dev, st.st_ino)
                    if key in seen:
                        stats.hardlinks_collapsed += 1
                        continue
                    seen.add(key)
                kept.append(entry)
            if len(kept) >= 2:
                out.append(kept)
        return out

    # -- 过滤谓词 --------------------------------------------------------
    def _dir_excluded(self, path: str, name: str) -> bool:
        if name.lower() in self._exclude_names:
            return True
        if self._exclude_paths and os.path.normcase(path) in self._exclude_paths:
            return True
        return False

    def _file_accepted(self, path: str, name: str, size: int) -> bool:
        cfg = self.config
        # 0 字节文件必须先判：min_size 默认是 1，若先按 min_size 过滤，
        # include_empty_files=True 就永远不会生效。
        if size == 0:
            return bool(cfg.include_empty_files)
        if size < cfg.min_size:
            return False
        if cfg.max_size is not None and size > cfg.max_size:
            return False

        lowered = name.lower()
        if self._include and not any(lowered.endswith(s) for s in self._include):
            return False
        if self._exclude_suffixes and any(lowered.endswith(s) for s in self._exclude_suffixes):
            return False
        # 显式写了备用数据流的路径（file.txt:stream）不参与比对
        if winpath.has_alternate_data_stream(path):
            return False
        return True

    # -- 遍历 ------------------------------------------------------------
    def iter_files(self, roots: Iterable[str]) -> Iterator[FileEntry]:
        cfg = self.config
        since_progress = 0

        for root in roots:
            if self.cancel.is_set():
                return
            try:
                display_root = winpath.normalize(root)
            except ValueError:
                continue

            # 栈里只保存**常规展示路径**：CPython 的 python.exe 已是
            # longPathAware，实测 700+ 字符路径 os.scandir 可直接处理；
            # 用 \\?\ 前缀反而会让继承的 entry.path 带上前缀、污染数据。
            stack: list[str] = [display_root]

            while stack:
                if self.cancel.is_set():
                    return

                current = stack.pop()
                try:
                    with winpath.scandir_safe(current) as it:
                        for entry in it:
                            if self.cancel.is_set():
                                return

                            # entry.path 已是常规路径；这里只做一次防御性归一
                            disp_path = winpath.display(entry.path)

                            try:
                                is_dir = entry.is_dir(follow_symlinks=False)
                            except OSError:
                                self.stats.broken_entries += 1
                                continue

                            if is_dir:
                                if self._dir_excluded(disp_path, entry.name):
                                    continue
                                if not cfg.follow_symlinks and winpath.is_reparse_point(entry):
                                    self.stats.reparse_points_skipped += 1
                                    continue
                                stack.append(disp_path)
                                self.stats.dirs_visited += 1
                                continue

                            try:
                                if not entry.is_file(follow_symlinks=False):
                                    # 既不是普通文件也不是普通目录：符号链接 / junction /
                                    # 未挂载的卷挂载点。不跟随，直接跳过——这正是
                                    # 避免 junction 环路的关键。
                                    self.stats.reparse_points_skipped += 1
                                    continue
                                st = entry.stat(follow_symlinks=False)
                            except PermissionError:
                                self.stats.permission_errors += 1
                                continue
                            except OSError:
                                self.stats.broken_entries += 1
                                continue

                            size = st.st_size
                            mtime = st.st_mtime
                            self.stats.files_seen += 1
                            self.stats.bytes_seen += size

                            if not self._file_accepted(disp_path, entry.name, size):
                                continue

                            # 云端占位符：逻辑大小可用，但读内容会触发下载
                            if cfg.skip_cloud_placeholders and winpath.is_cloud_placeholder(st):
                                self.stats.cloud_placeholders_skipped += 1
                                continue

                            # 超大稀疏/压缩文件：读一遍要好几秒，且几乎不会有副本
                            if (cfg.skip_sparse_over and size > cfg.skip_sparse_over
                                    and winpath.is_sparse_or_compressed(st)):
                                self.stats.sparse_skipped += 1
                                continue

                            self.stats.files_kept += 1
                            self.stats.bytes_kept += size
                            yield FileEntry(path=disp_path, size=size, mtime=mtime)
                except PermissionError:
                    self.stats.permission_errors += 1
                except (FileNotFoundError, NotADirectoryError):
                    # 目录在遍历过程中被删除 / 卷已卸载，静默跳过
                    self.stats.broken_entries += 1
                except OSError:
                    self.stats.permission_errors += 1

                since_progress += 1
                if self.on_progress is not None and since_progress >= _PROGRESS_INTERVAL:
                    since_progress = 0
                    self.on_progress(self.stats)


def scan_to_list(
    config: ScanConfig,
    cancel: Optional[threading.Event] = None,
    on_progress: Optional[ProgressFn] = None,
) -> list[FileEntry]:
    """便捷入口：一次性扫描完并返回文件列表。"""
    stats = ScanStats()
    scanner = Scanner(config, stats, cancel, on_progress)
    return list(scanner.iter_files(config.roots))


def group_by_size(
    entries: Iterable[FileEntry], min_group: int = 2
) -> list[list[FileEntry]]:
    """阶段 1：按文件字节数分组，只保留成员数 >= ``min_group`` 的组。

    这是**零成本**的最大过滤器：大小不同的文件绝不可能是重复文件。
    """
    buckets: dict[int, list[FileEntry]] = {}
    for entry in entries:
        buckets.setdefault(entry.size, []).append(entry)
    return [group for group in buckets.values() if len(group) >= min_group]
