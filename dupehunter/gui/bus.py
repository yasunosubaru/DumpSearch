"""线程通信：单槽进度广播 + 取消令牌。

两个关键设计（都是踩过坑之后定下来的）：

1. **进度用"加锁单槽、后来者胜"，不要用 ``queue.Queue``。**
   全盘扫描时哈希阶段每完成一个文件就回调一次，20 万文件/秒级别的
   ``put()`` 会让工作线程自己把自己堵死（消费者在 UI 线程，而 UI 线程
   此刻正在 ``wait_window`` 里）。单槽写法是"覆盖写"，无论生产者多快
   最多丢中间态，永远不会阻塞。

2. **只有两种事件。** ``progress``（可覆盖）和 ``finished``（只发一次，
   绝不丢）。这样不需要通用的丢事件队列，也就没有"扫描结束了但最后
   几条进度没收到"的竞态。
"""

from __future__ import annotations

import threading
from typing import Callable, Optional


class CancelToken:
    """可跨线程的取消信号。"""

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def event(self) -> threading.Event:
        return self._event

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._event.wait(timeout)

    def __bool__(self) -> bool:  # pragma: no cover
        return self._event.is_set()


class EventBus:
    """工作线程 -> UI 线程的进度广播。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._progress = None
        self._progress_seq = 0
        self._finished = False
        self._result = None
        self._error: Optional[str] = None
        self._progress_waiters: list[Callable[[object], None]] = []
        self._finish_waiters: list[Callable[[], None]] = []

    # -- 生产者侧（工作线程） ------------------------------------------
    def publish_progress(self, snapshot) -> None:
        """覆盖写。永不阻塞。"""
        with self._lock:
            self._progress = snapshot
            self._progress_seq += 1

    def publish_finished(self, result=None, error: Optional[str] = None) -> None:
        """只发一次。后续调用被忽略。"""
        with self._lock:
            if self._finished:
                return
            self._finished = True
            self._result = result
            self._error = error

    # -- 消费者侧（UI 线程） -------------------------------------------
    def take_progress(self):
        """取走当前进度槽；无新进度返回 ``None``。"""
        with self._lock:
            snapshot = self._progress
            self._progress = None
            return snapshot

    def is_finished(self) -> bool:
        with self._lock:
            return self._finished

    def finish_result(self):
        with self._lock:
            return self._result

    def finish_error(self) -> Optional[str]:
        with self._lock:
            return self._error

    @property
    def sequence(self) -> int:
        with self._lock:
            return self._progress_seq
