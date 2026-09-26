"""扫描流水线编排：遍历 → 按大小分组 → 快速哈希 → 全文哈希 → 逐字节校验。

所有阶段都是**可取消**的（``threading.Event``），并通过 ``on_progress``
回调把不可变快照推送给调用方（GUI）。回调在工作线程触发，由调用方负责
转回 UI 线程。
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from . import hasher
from .model import (
    DuplicateGroup,
    FileEntry,
    Phase,
    ScanConfig,
    ScanResult,
    ScanStats,
)
from .scanner import Scanner

# 进度回调节流间隔（秒）
_PROGRESS_INTERVAL = 0.15


@dataclass(slots=True)
class Progress:
    """一次进度快照（不可变语义，字段均为标量）。"""

    phase: Phase = Phase.SCANNING
    detail: str = ""
    done: int = 0
    total: int = 0
    bytes_processed: int = 0
    speed_bps: float = 0.0
    eta_seconds: float = 0.0
    stats: ScanStats = field(default_factory=ScanStats)

    @property
    def fraction(self) -> float:
        if self.total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.done / self.total))

    @property
    def percent(self) -> int:
        return int(self.fraction * 100)


ProgressFn = Callable[[Progress], None]


class _Speed:
    """简易速率/ETA 估算（滑动窗口，避免瞬时抖动）。"""

    def __init__(self, window: float = 3.0) -> None:
        self._window = window
        self._start = time.monotonic()
        self._last_bytes = 0
        self._last_time = self._start

    def update(self, total_bytes: int) -> tuple[float, float]:
        now = time.monotonic()
        d_bytes = total_bytes - self._last_bytes
        d_time = now - self._last_time
        if d_time >= 0.25:
            rate = d_bytes / d_time if d_time > 0 else 0.0
            # 指数平滑
            prev = getattr(self, "_rate", 0.0)
            self._rate = rate if prev == 0.0 else (prev * 0.4 + rate * 0.6)
            self._last_bytes = total_bytes
            self._last_time = now
        rate = getattr(self, "_rate", 0.0)
        return rate, self.elapsed

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start


def _bounded_parallel(
    func: Callable[[FileEntry], object],
    items: Sequence[FileEntry],
    workers: int,
    cancel: threading.Event,
    on_one_done: Optional[Callable[[int, int], None]] = None,
) -> list:
    """带在途上限的 ``map``，支持中途取消。

    不使用 ``executor.map``：它会一次性提交全部任务，几万条以上会
    堆积大量 Future。这里保证在途任务不超过 ``workers * 4``。
    """
    results: list = [None] * len(items)
    if not items:
        return results

    if workers <= 1:
        for idx, item in enumerate(items):
            if cancel.is_set():
                break
            results[idx] = _safe_call(func, item)
            if on_one_done:
                on_one_done(1, item.size)
        return results

    max_inflight = max(2, workers * 4)
    next_submit = 0
    in_flight: list[tuple[int, Future]] = []
    completed = 0

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dh-hash") as pool:
        while True:
            while next_submit < len(items) and len(in_flight) < max_inflight:
                if cancel.is_set():
                    break
                fut = pool.submit(_safe_call, func, items[next_submit])
                in_flight.append((next_submit, fut))
                next_submit += 1

            if not in_flight:
                break

            idx, fut = in_flight.pop(0)
            results[idx] = fut.result()
            completed += 1
            if on_one_done:
                on_one_done(completed, items[idx].size)

            if cancel.is_set():
                break

    return results


def _safe_call(func: Callable[[FileEntry], object], item: FileEntry):
    try:
        return func(item)
    except (PermissionError, FileNotFoundError, IsADirectoryError, OSError):
        return None


class ScanEngine:
    """把 :class:`ScanConfig` 跑成 :class:`ScanResult`。"""

    def __init__(
        self,
        config: ScanConfig,
        cancel: Optional[threading.Event] = None,
        on_progress: Optional[ProgressFn] = None,
    ) -> None:
        self.config = config
        self.cancel = cancel if cancel is not None else threading.Event()
        self.on_progress = on_progress
        self.stats = ScanStats()
        self._last_emit = 0.0
        self._speed = _Speed()

    # -- 进度 ------------------------------------------------------------
    def _emit(self, progress: Progress, force: bool = False) -> None:
        if self.on_progress is None:
            return
        now = time.monotonic()
        if not force and now - self._last_emit < _PROGRESS_INTERVAL:
            return
        self._last_emit = now
        self.on_progress(progress)

    def _emit_now(self, progress: Progress) -> None:
        if self.on_progress is not None:
            self._last_emit = time.monotonic()
            self.on_progress(progress)

    def _progress(
        self, phase: Phase, detail: str = "", done: int = 0, total: int = 0
    ) -> Progress:
        rate, elapsed = self._speed.update(self.stats.bytes_hashed)
        eta = 0.0
        if rate > 1 and total > done:
            remaining_bytes = (total - done) * max(1, self._avg_size)
            eta = remaining_bytes / rate
        return Progress(
            phase=phase,
            detail=detail,
            done=done,
            total=total,
            bytes_processed=self.stats.bytes_hashed,
            speed_bps=rate,
            eta_seconds=eta,
            stats=self.stats,
        )

    _avg_size: int = 1

    # -- 主流程 ----------------------------------------------------------
    def run(self) -> ScanResult:
        cfg = self.config
        result = ScanResult(config=cfg, stats=self.stats)
        workers = cfg.resolved_workers()

        try:
            # ---- 阶段 0：遍历 + 阶段 1：按大小分组 ----
            buckets: dict[int, list[FileEntry]] = {}
            scanner = Scanner(cfg, self.stats, self.cancel, on_progress=None)

            self._emit_now(Progress(phase=Phase.SCANNING, detail="开始遍历", stats=self.stats))

            for entry in scanner.iter_files(cfg.roots):
                buckets.setdefault(entry.size, []).append(entry)
                if self.stats.files_kept % 2000 == 0:
                    self._emit(
                        Progress(
                            phase=Phase.SCANNING,
                            detail=f"已遍历 {self.stats.dirs_visited:,} 个目录",
                            done=self.stats.files_kept,
                            total=0,
                            stats=self.stats,
                        )
                    )

            if self.cancel.is_set():
                result.cancelled = True
                result.phase = Phase.CANCELLED
                return result

            size_groups = [g for g in buckets.values() if len(g) >= cfg.min_files_per_group]
            buckets.clear()

            # 硬链接折叠：指向同一物理文件的多个路径不是"重复副本"，
            # 删掉不省任何空间。放在大小分组之后，额外 stat 的量很小。
            if cfg.treat_hardlinks_as_one and size_groups:
                self._emit_now(
                    Progress(
                        phase=Phase.GROUPING,
                        detail=f"检查 {len(size_groups):,} 组候选中的硬链接",
                        stats=self.stats,
                    )
                )
                size_groups = Scanner.collapse_hardlinks(size_groups, self.stats)

            candidates: list[FileEntry] = [f for g in size_groups for f in g]
            self.stats.size_candidates = len(candidates)

            if not candidates:
                result.phase = Phase.DONE
                self._emit_now(Progress(phase=Phase.DONE, detail="未发现重复文件", stats=self.stats))
                return result

            self._avg_size = max(1, self.stats.bytes_kept // max(1, self.stats.files_kept))

            # ---- 阶段 2：快速哈希 ----
            self._emit_now(
                Progress(
                    phase=Phase.QUICK_HASH,
                    detail=f"{len(candidates):,} 个候选文件（大小相同）",
                    total=len(candidates),
                    stats=self.stats,
                )
            )

            done = [0]

            def on_quick(count: int, size: int) -> None:
                done[0] = count
                self.stats.files_quick_hashed += 1
                self.stats.bytes_hashed += min(size, cfg.quick_chunk * 3)
                self._emit(
                    Progress(
                        phase=Phase.QUICK_HASH,
                        done=count,
                        total=len(candidates),
                        stats=self.stats,
                    )
                )

            quick_results = _bounded_parallel(
                lambda f: hasher.quick_digest(
                    f, chunk=cfg.quick_chunk, inline_max=cfg.inline_quick_max
                ),
                candidates,
                workers,
                self.cancel,
                on_quick,
            )

            survivors: list[FileEntry] = []
            for entry, digest in zip(candidates, quick_results):
                if digest is None:
                    # None = 读取出错，或读取期间被改写（磁盘上从未存在过的状态）
                    self.stats.unstable_skipped += 1
                    continue
                survivors.append(entry.with_hashes(quick_hash=digest))
            candidates.clear()

            if self.cancel.is_set():
                result.cancelled = True
                result.phase = Phase.CANCELLED
                return result

            quick_groups = [
                g for g in _bucket_map_by(survivors, lambda f: f.quick_hash).values()
                if len(g) >= cfg.min_files_per_group
            ]
            self.stats.quick_candidates = sum(len(g) for g in quick_groups)
            survivors.clear()

            if not quick_groups:
                result.phase = Phase.DONE
                self._emit_now(
                    Progress(phase=Phase.DONE, detail="快速哈希后已无候选", stats=self.stats)
                )
                return result

            # ---- 阶段 3：全文哈希 ----
            full_candidates: list[FileEntry] = [f for g in quick_groups for f in g]
            self._emit_now(
                Progress(
                    phase=Phase.FULL_HASH,
                    detail=f"{len(full_candidates):,} 个文件需要读全文",
                    total=len(full_candidates),
                    stats=self.stats,
                )
            )

            done[0] = 0
            total_bytes = sum(f.size for f in full_candidates)

            def on_full(count: int, size: int) -> None:
                done[0] = count
                self.stats.files_hashed += 1
                self.stats.bytes_hashed += size
                self._emit(
                    Progress(
                        phase=Phase.FULL_HASH,
                        detail="",
                        done=count,
                        total=len(full_candidates),
                        stats=self.stats,
                    )
                )

            full_results = _bounded_parallel(
                lambda f: hasher.full_digest(f, chunk=cfg.read_chunk),
                full_candidates,
                workers,
                self.cancel,
                on_full,
            )

            hashed: list[FileEntry] = []
            for entry, digest in zip(full_candidates, full_results):
                if digest is None:
                    self.stats.unstable_skipped += 1
                    continue
                hashed.append(entry.with_hashes(full_hash=digest))
            full_candidates.clear()

            if self.cancel.is_set():
                result.cancelled = True
                result.phase = Phase.CANCELLED
                return result

            groups = [
                DuplicateGroup(digest=key, size=members[0].size, members=members)
                for key, members in _bucket_map_by(hashed, lambda f: f.full_hash).items()
                if len(members) >= cfg.min_files_per_group
            ]

            # ---- 阶段 4：逐字节校验 ----
            if cfg.verify_bytes and not self.cancel.is_set():
                self._emit_now(
                    Progress(
                        phase=Phase.VERIFY,
                        detail=f"校验 {len(groups):,} 个重复组",
                        total=len(groups),
                        stats=self.stats,
                    )
                )
                groups = self._verify(groups, workers, total_bytes)

            # ---- 输出 ----
            groups.sort(key=lambda g: g.wasted_bytes, reverse=True)
            result.groups = groups
            result.phase = Phase.CANCELLED if self.cancel.is_set() else Phase.DONE
            self._emit_now(
                Progress(
                    phase=result.phase,
                    detail=result.summary(),
                    done=1,
                    total=1,
                    stats=self.stats,
                )
            )
            return result

        except Exception as exc:  # pragma: no cover - 兜底，避免线程静默死掉
            result.phase = Phase.ERROR
            result.error = f"{type(exc).__name__}: {exc}"
            self._emit_now(
                Progress(phase=Phase.ERROR, detail=result.error, stats=self.stats)
            )
            return result

    # -- 校验阶段 --------------------------------------------------------
    def _verify(
        self, groups: list[DuplicateGroup], workers: int, total_bytes: int
    ) -> list[DuplicateGroup]:
        """对每组做逐字节比对，剔除哈希碰撞导致的误判。"""
        jobs: list[tuple[DuplicateGroup, list[FileEntry]]] = []
        for group in groups:
            jobs.append((group, group.members[1:]))

        def check(job):
            group, others = job
            base = group.members[0]
            keep = [base]
            for other in others:
                if self.cancel.is_set():
                    return group, keep
                try:
                    if hasher.files_identical(base, other):
                        keep.append(other)
                except OSError:
                    continue
            return group, keep

        processed = 0
        verified: list[DuplicateGroup] = []
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dh-verify") as pool:
            futures = [pool.submit(check, job) for job in jobs]
            for fut in futures:
                if self.cancel.is_set():
                    break
                try:
                    group, keep = fut.result()
                except Exception:
                    continue
                processed += 1
                if len(keep) >= self.config.min_files_per_group:
                    group.members = keep
                    group.verified = True
                    verified.append(group)
                self._emit(
                    Progress(
                        phase=Phase.VERIFY,
                        done=processed,
                        total=len(jobs),
                        stats=self.stats,
                    )
                )
        return verified


def _bucket_map_by(
    items: Iterable[FileEntry], keyfn: Callable[[FileEntry], object]
) -> dict[object, list[FileEntry]]:
    buckets: dict[object, list[FileEntry]] = {}
    for item in items:
        buckets.setdefault(keyfn(item), []).append(item)
    return buckets


def scan(
    config: ScanConfig,
    cancel: Optional[threading.Event] = None,
    on_progress: Optional[ProgressFn] = None,
) -> ScanResult:
    """同步跑一次完整扫描。"""
    return ScanEngine(config, cancel, on_progress).run()


def default_workers() -> int:
    return max(4, min(16, (os.cpu_count() or 4) * 2))
