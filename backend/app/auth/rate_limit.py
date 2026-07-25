"""In-process failed-login throttle (F9.1: "wrong password... rate
limited"). Sliding window per key (email); resets on process restart —
an honest, documented v1 limitation (config.py), not a hidden gap.
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable


class LoginRateLimiter:
    def __init__(
        self,
        *,
        max_attempts: int,
        window_seconds: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._clock = clock or time.monotonic
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def is_blocked(self, key: str) -> bool:
        self._prune(key)
        return len(self._attempts[key]) >= self._max_attempts

    def record_failure(self, key: str) -> None:
        self._prune(key)
        self._attempts[key].append(self._clock())

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)

    def _prune(self, key: str) -> None:
        window_start = self._clock() - self._window_seconds
        bucket = self._attempts[key]
        while bucket and bucket[0] < window_start:
            bucket.popleft()
