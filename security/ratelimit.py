"""In-process sliding-window limiter for login attempts.

Single-process only (fine for a single-node deployment); use a shared store such as Redis when
running several API workers.
"""
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException


class AttemptLimiter:
    def __init__(self, max_attempts: int, window_seconds: int):
        self.max = max_attempts
        self.window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque:
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def check(self, key: str) -> None:
        now = time.time()
        with self._lock:
            q = self._prune(key, now)
            if len(q) >= self.max:
                retry = max(1, int(self.window - (now - q[0])))
                raise HTTPException(429, "Too many attempts. Try again later.", headers={"Retry-After": str(retry)})

    def fail(self, key: str) -> None:
        now = time.time()
        with self._lock:
            self._prune(key, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def clear_all(self) -> None:
        with self._lock:
            self._hits.clear()
