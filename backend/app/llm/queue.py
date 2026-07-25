"""Central LLM governor (V-009, ENGINEERING §3): every real Gemini call goes
through `LLMQueue.submit`. Serializes to respect req/min, gates a
DB-persisted daily quota, checks the response cache before spending quota
(D-011), retries transient failures with backoff, and writes every call
(success or exhausted retries) to `audit_log`.

Nothing outside `app/llm/` may call a transport directly (CODING.md §2).
"""

import asyncio
import hashlib
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.errors import ApiDownError, QuotaExhaustedError
from app.models.audit import AuditLog
from app.models.llm import LLMQuotaCounter, LLMResponseCache

SleepFn = Callable[[float], Awaitable[None]]
ClockFn = Callable[[], float]


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class Transport(Protocol):
    """What a real (or test-fake) Gemini transport must implement."""

    async def generate(
        self, *, model: str, prompt: str, temperature: float, **context: Any
    ) -> dict[str, Any]: ...


class TransportRateLimited(Exception):
    """Transport signaled 429 — the governor's RPM budget was still too high."""


class TransportServerError(Exception):
    """Transport signaled a 5xx — retry with backoff, then `api_down`."""


def quota_day_for(tz_name: str) -> str:
    """Gemini's own reset boundary is midnight Pacific, not local time
    (ticket V-009 edge case) — callers pass `settings.llm_quota_reset_timezone`."""
    return datetime.now(ZoneInfo(tz_name)).date().isoformat()


def next_reset_for(tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    midnight = datetime(today.year, today.month, today.day, tzinfo=tz)
    return midnight + timedelta(days=1)


class RateGovernor:
    """Serializes calls to a sliding 60s window of at most `rpm` calls.

    `clock`/`sleep` are injectable so tests can prove a 50-call burst never
    exceeds the window without waiting real wall-clock minutes (V-009 AC).
    """

    def __init__(
        self, rpm: int, *, clock: ClockFn | None = None, sleep: SleepFn | None = None
    ) -> None:
        self.rpm = rpm
        self._clock = clock or time.monotonic
        self._sleep = sleep or _default_sleep
        self._lock = asyncio.Lock()
        self._timestamps: deque[float] = deque()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                window_start = now - 60.0
                while self._timestamps and self._timestamps[0] < window_start:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.rpm:
                    self._timestamps.append(now)
                    return
                wait = self._timestamps[0] + 60.0 - now
                await self._sleep(max(wait, 0.01))


class LLMQueue:
    def __init__(
        self,
        *,
        transport: Transport,
        session_factory: async_sessionmaker[AsyncSession],
        model: str,
        temperature: float,
        rpm: int,
        daily_quota: int,
        max_retries: int,
        retry_base_seconds: float,
        reset_timezone: str,
        governor: RateGovernor | None = None,
        clock: ClockFn | None = None,
        sleep: SleepFn | None = None,
    ) -> None:
        self._transport = transport
        self._session_factory = session_factory
        self._model = model
        self._temperature = temperature
        self._daily_quota = daily_quota
        self._max_retries = max_retries
        self._retry_base_seconds = retry_base_seconds
        self._tz = ZoneInfo(reset_timezone)
        self._sleep = sleep or _default_sleep
        self._governor = governor or RateGovernor(rpm, clock=clock, sleep=self._sleep)

    def _quota_day(self) -> str:
        return datetime.now(self._tz).date().isoformat()

    def _next_reset(self) -> datetime:
        today = datetime.now(self._tz).date()
        midnight = datetime(today.year, today.month, today.day, tzinfo=self._tz)
        return midnight + timedelta(days=1)

    @staticmethod
    def _input_hash(prompt_version: str, model: str, prompt: str, context: dict[str, Any]) -> str:
        payload = json.dumps(
            {
                "prompt_version": prompt_version,
                "model": model,
                "prompt": prompt,
                "context": context,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def submit(
        self,
        *,
        prompt_type: str,
        prompt: str,
        prompt_version: str,
        check_run_id: int | None = None,
        **context: Any,
    ) -> dict[str, Any]:
        input_hash = self._input_hash(prompt_version, self._model, prompt, context)

        async with self._session_factory() as session:
            cached = await self._get_cached(session, input_hash)
            if cached is not None:
                await self._note_cache_hit(session, input_hash=input_hash)
                await self._write_audit(
                    session,
                    event_type="llm_cache_hit",
                    prompt_type=prompt_type,
                    prompt_version=prompt_version,
                    input_hash=input_hash,
                    check_run_id=check_run_id,
                    response=cached,
                )
                await session.commit()
                return cached

            await self._reserve_quota(session)
            await session.commit()

        await self._governor.acquire()

        try:
            response = await self._call_with_retry(prompt=prompt, context=context)
        except ApiDownError as exc:
            async with self._session_factory() as session:
                await self._write_audit(
                    session,
                    event_type="llm_call_failed",
                    prompt_type=prompt_type,
                    prompt_version=prompt_version,
                    input_hash=input_hash,
                    check_run_id=check_run_id,
                    response={"error": str(exc)},
                )
                await session.commit()
            raise

        async with self._session_factory() as session:
            await self._write_audit(
                session,
                event_type="llm_call",
                prompt_type=prompt_type,
                prompt_version=prompt_version,
                input_hash=input_hash,
                check_run_id=check_run_id,
                response=response,
            )
            await self._write_cache(
                session, input_hash=input_hash, prompt_version=prompt_version, response=response
            )
            await session.commit()

        return response

    async def get_quota_status(self) -> dict[str, Any]:
        day = self._quota_day()
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(LLMQuotaCounter.call_count, LLMQuotaCounter.cache_hit_count).where(
                        LLMQuotaCounter.quota_day == day
                    )
                )
            ).first()
        calls_used = row[0] if row else 0
        cache_hits = row[1] if row else 0
        total = calls_used + cache_hits
        return {
            "quota_day": day,
            "calls_used": calls_used,
            "daily_limit": self._daily_quota,
            "calls_remaining": max(self._daily_quota - calls_used, 0),
            "cache_hits_today": cache_hits,
            "cache_hit_rate": round(cache_hits / total, 3) if total else 0.0,
            "reset_at": self._next_reset().isoformat(),
            "rpm_limit": self._governor.rpm,
        }

    async def _reserve_quota(self, session: AsyncSession) -> None:
        """Atomic check-and-increment (INSERT ... ON CONFLICT ... WHERE):
        safe under concurrent workers even though Render runs one dyno
        today (ticket V-009 edge case — don't bake in the single-dyno
        assumption)."""
        day = self._quota_day()
        stmt = (
            pg_insert(LLMQuotaCounter)
            .values(quota_day=day, call_count=1, cache_hit_count=0)
            .on_conflict_do_update(
                index_elements=[LLMQuotaCounter.quota_day],
                set_={"call_count": LLMQuotaCounter.call_count + 1},
                where=LLMQuotaCounter.call_count < self._daily_quota,
            )
            .returning(LLMQuotaCounter.call_count)
        )
        result = await session.execute(stmt)
        if result.first() is None:
            raise QuotaExhaustedError(
                f"Daily Gemini quota ({self._daily_quota}) reached for {day} (Pacific); "
                f"resumes {self._next_reset().isoformat()}."
            )

    async def _note_cache_hit(self, session: AsyncSession, *, input_hash: str) -> None:
        day = self._quota_day()
        stmt = (
            pg_insert(LLMQuotaCounter)
            .values(quota_day=day, call_count=0, cache_hit_count=1)
            .on_conflict_do_update(
                index_elements=[LLMQuotaCounter.quota_day],
                set_={"cache_hit_count": LLMQuotaCounter.cache_hit_count + 1},
            )
        )
        await session.execute(stmt)

    async def _get_cached(self, session: AsyncSession, input_hash: str) -> dict[str, Any] | None:
        return await session.scalar(
            select(LLMResponseCache.response).where(LLMResponseCache.input_hash == input_hash)
        )

    async def _write_cache(
        self,
        session: AsyncSession,
        *,
        input_hash: str,
        prompt_version: str,
        response: dict[str, Any],
    ) -> None:
        stmt = (
            pg_insert(LLMResponseCache)
            .values(
                input_hash=input_hash,
                prompt_version=prompt_version,
                model=self._model,
                response=response,
            )
            .on_conflict_do_nothing(index_elements=[LLMResponseCache.input_hash])
        )
        await session.execute(stmt)

    async def _write_audit(
        self,
        session: AsyncSession,
        *,
        event_type: str,
        prompt_type: str,
        prompt_version: str,
        input_hash: str,
        check_run_id: int | None,
        response: dict[str, Any],
    ) -> None:
        session.add(
            AuditLog(
                event_type=event_type,
                check_run_id=check_run_id,
                prompt_version=prompt_version,
                input_hash=input_hash,
                payload={
                    "prompt_type": prompt_type,
                    "model": self._model,
                    "temperature": self._temperature,
                    "response": response,
                },
            )
        )

    async def _call_with_retry(self, *, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                return await self._transport.generate(
                    model=self._model, prompt=prompt, temperature=self._temperature, **context
                )
            except (TransportRateLimited, TransportServerError) as exc:
                last_exc = exc
                if attempt < self._max_retries - 1:
                    await self._sleep(self._retry_base_seconds * (2**attempt))
        raise ApiDownError(
            f"Gemini did not respond after {self._max_retries} attempts."
        ) from last_exc
