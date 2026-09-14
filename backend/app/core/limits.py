from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        timestamps = self._requests[key]
        while timestamps and now - timestamps[0] >= self.window_seconds:
            timestamps.popleft()
        if len(timestamps) >= self.limit:
            return False
        timestamps.append(now)
        return True

    def cleanup(self) -> None:
        now = time.monotonic()
        expired = [
            key for key, timestamps in self._requests.items()
            if not timestamps or now - timestamps[-1] >= self.window_seconds
        ]
        for key in expired:
            self._requests.pop(key, None)
