"""Generic sliding-window rate limiter. Originally built for the Gemini
queue (V-009) but the algorithm has nothing Gemini-specific about it — V-028
reuses it for the external citation-verification APIs (CrossRef/S2/
OpenLibrary/GBooks) rather than a second implementation (PLAYBOOK §2:
search for an existing implementation before writing a new one).

Distinct from `app/ratelimit.py` (per-instructor request throttling,
BUG-004/D-020) and `app/auth/rate_limit.py` (login attempt throttling) —
those gate WHO can call an endpoint and how often; this gates how fast
THIS process may call an outbound API, a different axis entirely.
"""

import asyncio
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable

SleepFn = Callable[[float], Awaitable[None]]
ClockFn = Callable[[], float]


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class RateGovernor:
    """Serializes calls to a sliding 60s window of at most `rpm` calls.

    `clock`/`sleep` are injectable so tests can prove a burst never exceeds
    the window without waiting real wall-clock minutes (V-009 AC).
    """

    def __init__(
        self,
        rpm: int,
        *,
        min_interval_seconds: float = 0.0,
        clock: ClockFn | None = None,
        sleep: SleepFn | None = None,
    ) -> None:
        self.rpm = rpm
        self._clock = clock or time.monotonic
        self._sleep = sleep or _default_sleep
        self._lock = asyncio.Lock()
        self._timestamps: deque[float] = deque()
        self._min_interval_seconds = 0.0
        self._last_admitted_at: float | None = None
        self.tighten_min_interval(min_interval_seconds)

    def tighten_min_interval(self, seconds: float) -> None:
        """Retain admission history when a provider profile becomes stricter.

        Limits may tighten for this process lifetime; changing configuration
        must not create a fresh burst allowance between concurrent lookups.
        The default zero interval preserves callers' existing RPM behavior.
        """
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Minimum request interval must be finite and nonnegative.")
        self._min_interval_seconds = max(self._min_interval_seconds, seconds)

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                window_start = now - 60.0
                while self._timestamps and self._timestamps[0] < window_start:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.rpm:
                    if self._last_admitted_at is not None:
                        remaining = self._last_admitted_at + self._min_interval_seconds - now
                        if remaining > 0:
                            await self._sleep(remaining)
                            continue
                    self._timestamps.append(now)
                    self._last_admitted_at = now
                    return
                wait = self._timestamps[0] + 60.0 - now
                await self._sleep(max(wait, 0.01))
