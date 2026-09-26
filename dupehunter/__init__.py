"""Dump Search —— 全盘重复文件查找与安全清理工具。

设计要点：

* **分层比对**：文件大小 → 分段采样哈希 → 全文 BLAKE2b-256 → 逐字节校验，
  把"读全文"这一最贵动作压到最少文件上。
* **默认安全**：删除一律走 Windows 回收站，且必须先预览、再确认；
  核心逻辑（扫描/哈希/选择）与 GUI 完全解耦，可独立测试。
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = [
    "DuplicateGroup",
    "FileEntry",
    "KeepRule",
    "Phase",
    "ScanConfig",
    "ScanEngine",
    "ScanResult",
    "ScanStats",
    "scan",
]


def __getattr__(name: str):
    # 惰性导出，避免 import 包时把 ctypes/Tkinter 一起拉进来
    if name in ("ScanConfig", "ScanResult", "ScanStats", "Phase", "KeepRule",
                "FileEntry", "DuplicateGroup"):
        from . import model

        return getattr(model, name)
    if name in ("ScanEngine", "scan"):
        from . import engine

        return getattr(engine, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
