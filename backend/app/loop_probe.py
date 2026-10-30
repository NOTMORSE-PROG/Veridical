"""How late the event loop ran while a long job was in progress.

Leases on this host (upload preparation, check claims) are renewed by tasks on
the event loop, so they are only safe while the loop is never blocked for
longer than the lease. This probe wakes on a fixed tick and keeps the worst
lateness it saw, so each job can log the number its lease has to beat. A stall
of at least one tick always delays some wake-up; shorter ones are partly seen.
"""

import asyncio


class LoopLagProbe:
    def __init__(self, tick_seconds: float):
        self.tick = tick_seconds
        self.worst = 0.0
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            due = loop.time() + self.tick
            await asyncio.sleep(self.tick)
            self.worst = max(self.worst, loop.time() - due)

    def start(self) -> "LoopLagProbe":
        if self.tick > 0:
            self._task = asyncio.create_task(self._run(), name="event-loop-probe")
        return self

    def stop(self) -> float:
        """Stop sampling and return the worst delay seen. Nothing is awaited,
        so a caller's cleanup cannot be cut short here."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        return self.worst
