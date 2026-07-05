from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """进程内滑动窗口限流器（按 key 计数），用于匿名 QA 的按 IP 限流。

    没有 Redis，多实例部署下各进程独立计数；对"防脚本刷"这一目标足够，
    真要跨实例精确限流再换 Redis。线程安全：FastAPI 默认线程池执行同步路由。
    """

    def __init__(self, *, max_requests: int, window_seconds: float) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """记录一次请求；未超限返回 True，超限返回 False（不记录本次）。"""
        current = time.monotonic() if now is None else now
        threshold = current - self._window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= threshold:
                hits.popleft()
            if len(hits) >= self._max_requests:
                return False
            hits.append(current)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
