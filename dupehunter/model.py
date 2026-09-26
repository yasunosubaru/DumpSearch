"""数据模型：文件条目、重复组、扫描配置与统计。

本模块只依赖标准库，不含任何 I/O 逻辑，便于被 GUI / CLI / 测试共同复用。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import PureWindowsPath
from typing import Iterable, Iterator, Optional


class Phase(str, enum.Enum):
    """扫描流水线阶段。"""

    SCANNING = "扫描中"
    GROUPING = "按大小分组"
    QUICK_HASH = "快速哈希"
    FULL_HASH = "完整哈希"
    VERIFY = "逐字节校验"
    DONE = "完成"
    CANCELLED = "已取消"
    ERROR = "出错"


class KeepRule(str, enum.Enum):
    """每个重复组中保留哪一个文件。"""

    NEWEST = "newest"
    OLDEST = "oldest"
    SHORTEST_PATH = "shortest_path"
    LONGEST_PATH = "longest_path"
    SHALLOWEST = "shallowest"
    DEEPEST = "deepest"
    FIRST_SEEN = "first_seen"


@dataclass(slots=True, frozen=True)
class FileEntry:
    """一个参与比对的文件。

    ``path`` 始终是可直接打开的**显示路径**（不带 ``\\\\?\\`` 前缀），
    实际打开时由 :mod:`dupehunter.winpath` 负责补前缀。
    """

    path: str
    size: int
    mtime: float
    quick_hash: Optional[bytes] = None
    full_hash: Optional[bytes] = None

    @property
    def name(self) -> str:
        return PureWindowsPath(self.path).name

    @property
    def parent(self) -> str:
        return str(PureWindowsPath(self.path).parent)

    @property
    def suffix(self) -> str:
        return PureWindowsPath(self.path).suffix.lower()

    @property
    def depth(self) -> int:
        """路径层级深度，用于 SHALLOWEST / DEEPEST 保留策略。"""
        return self.path.count("\\") + self.path.count("/")

    def with_hashes(
        self, quick_hash: Optional[bytes] = None, full_hash: Optional[bytes] = None
    ) -> "FileEntry":
        return FileEntry(
            path=self.path,
            size=self.size,
            mtime=self.mtime,
            quick_hash=quick_hash if quick_hash is not None else self.quick_hash,
            full_hash=full_hash if full_hash is not None else self.full_hash,
        )


@dataclass(slots=True)
class DuplicateGroup:
    """内容完全相同的一组文件（至少 2 个成员）。"""

    digest: bytes
    size: int
    members: list[FileEntry] = field(default_factory=list)
    verified: bool = False

    @property
    def count(self) -> int:
        return len(self.members)

    @property
    def wasted_bytes(self) -> int:
        """若只保留一个，可释放的字节数。"""
        return self.size * (len(self.members) - 1)

    def pick_keeper(self, rule: KeepRule = KeepRule.NEWEST) -> FileEntry:
        """按规则选出应当保留的文件。"""
        if not self.members:
            raise ValueError("空分组")

        if rule is KeepRule.NEWEST:
            return max(self.members, key=lambda f: f.mtime)
        if rule is KeepRule.OLDEST:
            return min(self.members, key=lambda f: f.mtime)
        if rule is KeepRule.SHORTEST_PATH:
            return min(self.members, key=lambda f: (len(f.path), f.path.lower()))
        if rule is KeepRule.LONGEST_PATH:
            return max(self.members, key=lambda f: (len(f.path), f.path.lower()))
        if rule is KeepRule.SHALLOWEST:
            return min(self.members, key=lambda f: (f.depth, f.path.lower()))
        if rule is KeepRule.DEEPEST:
            return max(self.members, key=lambda f: (f.depth, f.path.lower()))
        return self.members[0]

    def split_by_keeper(
        self, rule: KeepRule = KeepRule.NEWEST
    ) -> tuple[FileEntry, list[FileEntry]]:
        """返回 ``(保留文件, 待删除文件列表)``。"""
        keeper = self.pick_keeper(rule)
        return keeper, [f for f in self.members if f.path != keeper.path]

    def iter_paths(self) -> Iterator[str]:
        for member in self.members:
            yield member.path


@dataclass(slots=True)
class ScanStats:
    """扫描过程中的累计统计，用于进度显示与结果摘要。"""

    drives: list[str] = field(default_factory=list)
    dirs_visited: int = 0
    files_seen: int = 0
    files_kept: int = 0
    bytes_seen: int = 0
    bytes_kept: int = 0
    bytes_hashed: int = 0
    files_hashed: int = 0
    files_quick_hashed: int = 0
    permission_errors: int = 0
    broken_entries: int = 0
    reparse_points_skipped: int = 0
    hardlinks_collapsed: int = 0
    unstable_skipped: int = 0
    cloud_placeholders_skipped: int = 0
    sparse_skipped: int = 0
    size_candidates: int = 0
    quick_candidates: int = 0

    def merge(self, other: "ScanStats") -> None:
        self.dirs_visited += other.dirs_visited
        self.files_seen += other.files_seen
        self.files_kept += other.files_kept
        self.bytes_seen += other.bytes_seen
        self.bytes_kept += other.bytes_kept
        self.permission_errors += other.permission_errors
        self.broken_entries += other.broken_entries
        self.reparse_points_skipped += other.reparse_points_skipped


@dataclass(slots=True)
class ScanConfig:
    """一次扫描任务的全部可调参数。"""

    roots: list[str] = field(default_factory=list)
    exclude_dirs: set[str] = field(default_factory=set)
    include_suffixes: set[str] = field(default_factory=set)
    exclude_suffixes: set[str] = field(default_factory=set)

    min_size: int = 1  # 1 字节 => 忽略 0 字节文件
    max_size: Optional[int] = None
    min_files_per_group: int = 2
    include_empty_files: bool = False
    include_hidden: bool = True
    follow_symlinks: bool = False
    verify_bytes: bool = True
    # NTFS 硬链接（st_nlink > 1）指向的是**同一个物理文件**。
    # 把它们算作"重复"是这类工具最常见的正确性坑：删掉一个副本
    # 释放 0 字节，还可能破坏用户程序里对该文件的引用。默认折叠为一份。
    treat_hardlinks_as_one: bool = True

    workers: int = 0  # 0 => 自动
    quick_chunk: int = 4096
    # 1 MiB 是实测的甜点：只有这个量级能让 hashlib 释放 GIL 拿到 ~5x
    # 线程扩展（4 KiB 约 0.7–1.8x，4 MiB 掉回 3.3x）。
    read_chunk: int = 1 * 1024 * 1024
    # 低于该体积的文件直接整读做快速哈希（避免 seek 往返）
    inline_quick_max: int = 128 * 1024
    # 超过该体积的稀疏/压缩文件默认跳过：读一个 16 GiB 稀疏文件要 ~6s，
    # 而它几乎不可能有"副本"（虚拟磁盘、pagefile 之类）。
    skip_sparse_over: int = 4 * 1024 * 1024 * 1024
    # 云端占位符（OneDrive 等）默认不读——读了会把文件下载下来。
    skip_cloud_placeholders: bool = True

    keep_rule: KeepRule = KeepRule.NEWEST

    def resolved_workers(self) -> int:
        if self.workers > 0:
            return self.workers
        import os

        return max(4, min(16, (os.cpu_count() or 4) * 2))

    def total_bytes_at_stake(self, groups: Iterable[DuplicateGroup]) -> int:
        return sum(g.wasted_bytes for g in groups)


@dataclass(slots=True)
class ScanResult:
    """扫描产物。"""

    groups: list[DuplicateGroup] = field(default_factory=list)
    stats: ScanStats = field(default_factory=ScanStats)
    config: ScanConfig = field(default_factory=ScanConfig)
    phase: Phase = Phase.DONE
    error: Optional[str] = None
    cancelled: bool = False

    @property
    def total_files(self) -> int:
        return sum(g.count for g in self.groups)

    @property
    def group_count(self) -> int:
        return len(self.groups)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(g.wasted_bytes for g in self.groups)

    def summary(self) -> str:
        stats = self.stats
        return (
            f"重复组 {self.group_count:,} 个 / 涉及文件 {self.total_files:,} 个 / "
            f"可释放 {format_bytes(self.reclaimable_bytes)}；"
            f"扫描 {stats.files_seen:,} 个文件（{format_bytes(stats.bytes_seen)}），"
            f"跳过权限受限 {stats.permission_errors:,}，"
            f"跳过重解析点 {stats.reparse_points_skipped:,}。"
        )


_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_bytes(num: float, precision: int = 2) -> str:
    """把字节数格式化为人类可读字符串。"""
    if num is None:
        return "-"
    negative = num < 0
    value = float(abs(num))
    idx = 0
    while value >= 1024.0 and idx < len(_UNITS) - 1:
        value /= 1024.0
        idx += 1
    text = f"{value:.{0 if idx == 0 else precision}f} {_UNITS[idx]}"
    return ("-" if negative else "") + text


_SIZE_UNITS = {"k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


def parse_size(text: str, default: int = 0) -> int:
    """``10M`` / ``1.5G`` / ``500K`` -> 字节。无法解析时返回 ``default``。"""
    raw = (text or "").strip().lower().rstrip("b")
    if not raw:
        return default
    try:
        if raw[-1] in _SIZE_UNITS:
            return int(float(raw[:-1]) * _SIZE_UNITS[raw[-1]])
        return int(float(raw))
    except ValueError:
        return default
