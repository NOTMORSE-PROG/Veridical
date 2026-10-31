"""One shared daily budget of Gemini transport attempts for every accuracy lane.

Several harness runs can be live at once, so the count lives in one JSON file
that is only changed under an exclusive lock file. A lane reserves an attempt
before each request goes out, retries included; a reservation that would pass
the lane's share or the day's cap is refused before any call is made.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Free-tier quotas reset on Pacific time, so the budget day does too.
QUOTA_TIMEZONE = ZoneInfo("America/Los_Angeles")
LOCK_WAIT_SECONDS = 30.0
STALE_LOCK_SECONDS = 120.0


class BudgetExceeded(RuntimeError):
    """The attempt would pass the lane's share or the day's cap."""


def pacific_day(now: datetime | None = None) -> str:
    return (now or datetime.now(QUOTA_TIMEZONE)).astimezone(QUOTA_TIMEZONE).date().isoformat()


@contextmanager
def _locked(path: Path):
    lock = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(handle)
            break
        # Windows reports a lock file that another process is deleting as a
        # permission error rather than as existing; both mean "busy".
        except (FileExistsError, PermissionError):
            try:
                stale = time.time() - lock.stat().st_mtime > STALE_LOCK_SECONDS
            except FileNotFoundError:
                continue
            if stale:
                # Taking over a stale lock races with another process doing the
                # same, and a lost update would undercount spent calls, so a
                # person removes it after checking no run is still going.
                raise TimeoutError(
                    f"Budget lock {lock} is older than {STALE_LOCK_SECONDS:.0f}s: a run "
                    "probably died holding it. Check no harness run is going, then delete it."
                ) from None
            if time.monotonic() > deadline:
                raise TimeoutError(f"Budget lock {lock} stayed held") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


class Budget:
    def __init__(self, path: Path, *, cap: int, shares: dict[str, int]):
        self.path, self.cap, self.shares = path, cap, shares

    @classmethod
    def from_golden(cls, golden: Path) -> Budget:
        """The shared budget and its settings, from `<golden>/budget_config.json`
        ({"cap": ..., "shares": {lane: ...}}), so no lane hardcodes the numbers."""
        config = json.loads((golden / "budget_config.json").read_text(encoding="utf-8"))
        return cls(golden / "_gemini_budget.json", cap=config["cap"], shares=config["shares"])

    def _read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def _write(self, data: dict) -> None:
        staged = self.path.with_suffix(".tmp")
        staged.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(staged, self.path)

    def reserve(self, lane: str, n: int = 1, *, now: datetime | None = None) -> int:
        """Reserve `n` attempts for `lane` today; returns the lane's new total."""
        day = pacific_day(now)
        with _locked(self.path):
            data = self._read()
            today = data.setdefault(
                day, {"cap": self.cap, "attempts": 0, "by": {}, "shares": dict(self.shares)}
            )
            used = today["by"].get(lane, 0)
            share = today["shares"].get(lane, 0)
            if used + n > share:
                raise BudgetExceeded(f"{lane} would use {used + n} of its {share} today ({day})")
            if today["attempts"] + n > today["cap"]:
                raise BudgetExceeded(f"Day cap {today['cap']} reached ({day})")
            today["by"][lane] = used + n
            today["attempts"] += n
            self._write(data)
            return today["by"][lane]

    def spent(self, day: str | None = None) -> dict:
        with _locked(self.path):
            return self._read().get(day or pacific_day(), {})
