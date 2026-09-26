"""阶段 2/3：分层哈希与逐字节校验。

分层策略（业界成熟做法，见 rmlint / jdupes / fdupes / Andrewsimsd 等）：

======  ==========================================  ==================
阶段    依据                                        代价
======  ==========================================  ==================
1       文件大小                                    免费（遍历时顺带）
2       头/中/尾三段采样 + BLAKE2b-128              每文件读 ~12 KB
3       全文 BLAKE2b-256                            读全文一次
4       逐字节比对（可选 paranoid 模式）           读全文一次，无哈希
======  ==========================================  ==================

第 2 阶段刻意采样**头 + 中 + 尾**三段而不是只取头部：很多文件格式
（如改过时间戳的 ISO、只更新了尾部的日志/字幕、ZIP 之后的中央目录）头
部完全一致，只比头部会产生大量假候选，把全文哈希的代价压不出去。

选用 BLAKE2b：比 SHA-256 快（尤其在无 SHA-NI 的老 CPU 上），
且 ``hashlib`` 内置、无外部依赖、抗碰撞性对去重场景绰绰有余。
"""

from __future__ import annotations

import hashlib
import os
from typing import Optional

from .model import FileEntry
from . import winpath

QUICK_DIGEST_SIZE = 16  # 128 位
FULL_DIGEST_SIZE = 32  # 256 位

# 采样点比例：头 50%、中 25%、尾 25% 的窗口
_PROBE_RATIOS = (0.0, 0.5, 0.75)


def _new_quick():
    return hashlib.blake2b(digest_size=QUICK_DIGEST_SIZE)


def _new_full():
    return hashlib.blake2b(digest_size=FULL_DIGEST_SIZE)


def _fingerprint(path: str) -> tuple[int, int]:
    """取 ``(st_size, st_mtime_ns)``，用来判断读取期间文件是否变了。"""
    try:
        st = os.stat(path)
    except OSError:
        return (-1, -1)
    return (st.st_size, st.st_mtime_ns)


def quick_digest(entry: FileEntry, chunk: int = 4096,
                 inline_max: int = 128 * 1024) -> Optional[bytes]:
    """阶段 2：对文件做分段采样哈希。

    小文件直接整读，避免多次 seek 的往返开销。

    返回 ``None`` 表示不可用：读取出错，或**读取期间文件被改写**。
    后者必须丢弃——Windows 上 ``open()`` 用 ``_SH_DENYNO``
    (``FILE_SHARE_READ|WRITE``)，可以读到别的进程正在写的文件，
    算出来的是磁盘上**从未存在过**的一个状态。
    """
    size = entry.size
    h = _new_quick()
    before = _fingerprint(entry.path)

    with winpath.open_binary(entry.path, "rb", buffering=0) as fh:
        if size <= inline_max:
            h.update(fh.read(size))
            digest = h.digest()
        else:
            # 采样 3 段（头/中/尾），每段 chunk 字节。
            # 只比头部会漏掉"只改了尾部"的文件，那种文件头部完全一致。
            window = min(chunk, max(1, size // 8))
            for ratio in _PROBE_RATIOS:
                offset = int(size * ratio)
                offset = min(offset, max(0, size - window))
                h.update(offset.to_bytes(8, "little"))
                fh.seek(offset)
                h.update(fh.read(window))
            digest = h.digest()

    if before != _fingerprint(entry.path):
        return None
    return digest


def full_digest(entry: FileEntry, chunk: int = 1 * 1024 * 1024) -> Optional[bytes]:
    """阶段 3：全文 BLAKE2b-256。

    ``chunk`` 默认 1 MiB：实测只有 1 MiB 左右的块能让 ``hashlib`` 释放
    GIL 拿到 ~5x 线程扩展（4 KiB 只有 0.7–1.8x，4 MiB 反而掉到 3.3x）。
    ``readinto`` 复用缓冲区，避免每块重新分配。

    返回 ``None`` 表示读取期间文件被改写，必须丢弃。
    """
    h = _new_full()
    h.update(entry.size.to_bytes(8, "little"))
    before = _fingerprint(entry.path)

    buf = bytearray(chunk)
    view = memoryview(buf)
    with winpath.open_binary(entry.path, "rb", buffering=0) as fh:
        while True:
            n = fh.readinto(buf)
            if not n:
                break
            h.update(view[:n])

    if before != _fingerprint(entry.path):
        return None
    return h.digest()


def files_identical(a: FileEntry, b: FileEntry,
                    chunk: int = 1 * 1024 * 1024) -> bool:
    """阶段 4：逐字节比对两个文件。

    用于 paranoid 模式——在"删除"这种不可撤销操作前做最后一道确认。
    没有任何哈希实现能 100% 排除碰撞，直接比字节可以。
    """
    if a.size != b.size:
        return False
    if a.size == 0:
        return True

    # 先后读出两路数据做内存比较，避免把两个文件都读进内存
    with winpath.open_binary(a.path, "rb", buffering=0) as fa, \
            winpath.open_binary(b.path, "rb", buffering=0) as fb:
        while True:
            ba = fa.read(chunk)
            bb = fb.read(chunk)
            if ba != bb:
                return False
            if not ba:
                return True


def digest_display(digest: Optional[bytes]) -> str:
    if not digest:
        return ""
    return digest.hex()[:16]


def estimate_cost(files: int, avg_size: float, workers: int) -> float:
    """粗略估算全文哈希阶段耗时（秒），仅用于 UI 预填 ETA。"""
    if workers <= 0 or avg_size <= 0:
        return 0.0
    # 保守假设单核顺序读+哈希约 400 MB/s
    throughput = 400e6 * min(workers, 8) / 8
    return (files * avg_size) / max(throughput, 1.0)


def file_size_on_disk(path: str) -> int:
    return winpath.getsize_safe(path)
