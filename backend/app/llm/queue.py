"""Central LLM governor (V-009, ENGINEERING §3): every real Gemini call goes
through `LLMQueue.submit`. Serializes to respect req/min, gates a
DB-persisted daily quota, checks the response cache before spending quota
(D-011), retries transient failures with backoff, and writes every call
(success or exhausted retries) to `audit_log`.

Nothing outside `app/llm/` may call a transport directly (CODING.md §2).
"""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.errors import ApiDownError, InvalidApiKeyError, QuotaExhaustedError
from app.llm.pool import ModelSpec, pool_daily_capacity
from app.models.audit import AuditLog
from app.models.llm import LLMQuotaCounter, LLMResponseCache
from app.rate_governor import ClockFn, RateGovernor, SleepFn, _default_sleep

__all__ = [
    "ClockFn",
    "LLMQueue",
    "RateGovernor",
    "SleepFn",
    "Transport",
    "TransportDailyQuotaExhausted",
    "TransportInvalidKey",
    "TransportRateLimited",
    "TransportServerError",
]


class Transport(Protocol):
    """What a real (or test-fake) Gemini transport must implement."""

    async def generate(
        self, *, model: str, prompt: str, temperature: float, **context: Any
    ) -> dict[str, Any]: ...


class TransportRateLimited(Exception):
    """Transport signaled a per-MINUTE 429 — the governor's RPM budget was
    still too high. Retryable: the window reopens in seconds."""


class TransportDailyQuotaExhausted(Exception):
    """Transport signaled a per-DAY 429 for this model.

    Distinct from `TransportRateLimited` on purpose (V-049): retrying a
    per-day cap just burns the retry budget and then reports `api_down` —
    a dishonest code for "we spent the day's allowance" (charter rule 9).
    The queue treats it as "this model island is closed until reset" and
    fails over to the next model in the pool instead.
    """


class TransportServerError(Exception):
    """Transport signaled a 5xx — retry with backoff, then `api_down`."""


class TransportInvalidKey(Exception):
    """The key itself was rejected (401/403, or any APIError Google didn't
    already classify as a per-minute/per-day 429 or a 5xx) -- V-052
    (backend-critic finding, P1, live-reproduced): this used to propagate
    as a raw, unmapped `genai_errors.APIError` straight out of `submit()`,
    which `FallbackLLMClient` (app/llm/__init__.py) had no way to catch,
    so a revoked or mistyped instructor key was a hard crash of the whole
    check_run instead of the graceful "fall back to the shared key" the
    ticket's own edge case requires. Never retried (the key won't start
    working between attempts a second apart) and never tried against
    another model in the pool (an invalid key fails identically against
    every model, so trying the rest would just be four more guaranteed
    failures before reporting the same root cause)."""


def quota_day_for(tz_name: str) -> str:
    """Gemini's own reset boundary is midnight Pacific, not local time
    (ticket V-009 edge case) — callers pass `settings.llm_quota_reset_timezone`."""
    return datetime.now(ZoneInfo(tz_name)).date().isoformat()


def next_reset_for(tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    midnight = datetime(today.year, today.month, today.day, tzinfo=tz)
    return midnight + timedelta(days=1)


class LLMQueue:
    """Spends a POOL of models, not one (V-049).

    Gemini meters requests-per-day per model, so the pool is a list of
    independent quota islands tried in order: the queue only moves to the
    next model once the current one's own day is spent. A single pinned
    model would cap the product at that model's RPD.
    """

    def __init__(
        self,
        *,
        transport: Transport,
        session_factory: async_sessionmaker[AsyncSession],
        temperature: float,
        max_retries: int,
        retry_base_seconds: float,
        reset_timezone: str,
        pool: tuple[ModelSpec, ...] | None = None,
        model: str | None = None,
        rpm: int | None = None,
        daily_quota: int | None = None,
        governor: RateGovernor | None = None,
        clock: ClockFn | None = None,
        sleep: SleepFn | None = None,
        instructor_id: int | None = None,
    ) -> None:
        if pool is None:
            # Single-model form: still supported so a caller (or a test) can
            # pin exactly one model, but it is now explicitly a pool of one
            # rather than a hidden assumption baked through the class.
            if model is None or rpm is None or daily_quota is None:
                raise ValueError("LLMQueue needs either pool= or model=/rpm=/daily_quota=.")
            pool = (ModelSpec(model=model, rpm=rpm, daily_quota=daily_quota, vision=True),)
        if not pool:
            raise ValueError("LLMQueue pool is empty, there is nothing to spend.")

        self._transport = transport
        self._session_factory = session_factory
        self._pool = pool
        # V-052 (BYOK): None = this queue spends the shared pool key; a real
        # id = an instructor's own key, a genuinely separate Gemini quota
        # island (see LLMQuotaCounter's own docstring for the partial-index
        # reasoning). Threaded into every quota-table read/write below.
        self._instructor_id = instructor_id
        self._temperature = temperature
        self._max_retries = max_retries
        self._retry_base_seconds = retry_base_seconds
        self._tz = ZoneInfo(reset_timezone)
        self._sleep = sleep or _default_sleep
        # One governor per model: RPM is metered per model too, so sharing a
        # single window would throttle the whole pool to the slowest member.
        self._governors: dict[str, RateGovernor] = {
            spec.model: RateGovernor(spec.rpm, clock=clock, sleep=self._sleep) for spec in pool
        }
        if governor is not None:  # explicit injection (tests) applies to the primary
            self._governors[pool[0].model] = governor

    @property
    def _model(self) -> str:
        """The primary model — the one a call is served by unless its day is
        spent. Kept as a property so audit/cache callers read the pool head
        rather than a duplicated field that could drift out of sync."""
        return self._pool[0].model

    def _quota_day(self) -> str:
        return datetime.now(self._tz).date().isoformat()

    def _next_reset(self) -> datetime:
        today = datetime.now(self._tz).date()
        midnight = datetime(today.year, today.month, today.day, tzinfo=self._tz)
        return midnight + timedelta(days=1)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        """Make a context value storable in the audit payload's JSONB column.

        The vision pass (V-007) passes raw PNG bytes as `images=[...]`, and
        `json.dumps` cannot encode bytes — writing the audit row would raise
        mid-call. Bytes are replaced by their length + SHA-256 so the row
        still identifies exactly which image was sent (replay, V-024) without
        inlining megabytes of base64 into every audit row.
        """
        if isinstance(value, bytes | bytearray):
            return {
                "__bytes__": len(value),
                "sha256": hashlib.sha256(bytes(value)).hexdigest(),
            }
        if isinstance(value, dict):
            return {key: LLMQueue._json_safe(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [LLMQueue._json_safe(item) for item in value]
        return value

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
        # The vision pass must stay on a multimodal model: failing over to a
        # text-only model would silently drop the images and return a
        # confident answer about nothing (V-007 / charter rule 9).
        candidates = self._candidates_for(context)

        # One hash per candidate (model is part of the cache key by design —
        # D-011). A hit on ANY candidate is a real answer already paid for,
        # so all of them are checked before spending a call.
        hashes = {
            spec.model: self._input_hash(prompt_version, spec.model, prompt, context)
            for spec in candidates
        }

        async with self._session_factory() as session:
            for spec in candidates:
                cached = await self._get_cached(session, hashes[spec.model])
                if cached is None:
                    continue
                await self._note_cache_hit(session, model=spec.model)
                await self._write_audit(
                    session,
                    event_type="llm_cache_hit",
                    prompt_type=prompt_type,
                    prompt_version=prompt_version,
                    input_hash=hashes[spec.model],
                    check_run_id=check_run_id,
                    response=cached,
                    context=context,
                    prompt=prompt,
                    model=spec.model,
                )
                await session.commit()
                return cached

        exhausted: list[str] = []
        for spec in candidates:
            async with self._session_factory() as session:
                reserved = await self._try_reserve_quota(session, spec)
                await session.commit()
            if not reserved:
                exhausted.append(spec.model)
                continue

            await self._governors[spec.model].acquire()
            input_hash = hashes[spec.model]

            try:
                response = await self._call_with_retry(
                    model=spec.model, prompt=prompt, context=context
                )
            except TransportDailyQuotaExhausted as exc:
                # Reality disagreed with our counter (the real cap moved, or
                # something outside this app spent it). Believe the API, close
                # the island for the rest of the day, and try the next model —
                # this is the self-correction that the 20-vs-1400 drift needed.
                async with self._session_factory() as session:
                    await self._close_island(session, spec)
                    await self._write_audit(
                        session,
                        event_type="llm_model_exhausted",
                        prompt_type=prompt_type,
                        prompt_version=prompt_version,
                        input_hash=input_hash,
                        check_run_id=check_run_id,
                        response={"error": str(exc), "model": spec.model},
                        context=context,
                        prompt=prompt,
                        model=spec.model,
                    )
                    await session.commit()
                exhausted.append(spec.model)
                continue
            except TransportInvalidKey as exc:
                # Unlike quota exhaustion, an invalid key fails identically
                # against every model in the pool -- raise immediately
                # rather than burning through the rest of `candidates` for
                # guaranteed-identical failures. `InvalidApiKeyError` (not
                # `ApiDownError`): `FallbackLLMClient` (app/llm/__init__.py)
                # catches it and reroutes to the shared key, the same as it
                # already does for `QuotaExhaustedError` -- a revoked/bad
                # BYOK key must degrade gracefully, never hard-fail the run
                # (V-052 ticket edge case, backend-critic finding P1).
                async with self._session_factory() as session:
                    await self._write_audit(
                        session,
                        event_type="llm_call_failed",
                        prompt_type=prompt_type,
                        prompt_version=prompt_version,
                        input_hash=input_hash,
                        check_run_id=check_run_id,
                        response={"error": str(exc)},
                        context=context,
                        prompt=prompt,
                        model=spec.model,
                    )
                    await session.commit()
                raise InvalidApiKeyError(f"Gemini rejected this API key: {exc}") from exc
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
                        context=context,
                        prompt=prompt,
                        model=spec.model,
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
                    context=context,
                    prompt=prompt,
                    model=spec.model,
                )
                await self._write_cache(
                    session,
                    input_hash=input_hash,
                    prompt_version=prompt_version,
                    response=response,
                    model=spec.model,
                )
                await session.commit()

            return response

        if not candidates:
            # BUG-035 follow-up (backend-critic): reachable only if every
            # candidate model has daily_quota=0 (or, for the image path,
            # none is vision-capable — that case already raises earlier).
            # Distinct from the message below: these models were never
            # tried at all, so "has spent its allowance" would be false —
            # there was no allowance to spend in the first place.
            raise QuotaExhaustedError(
                f"No model in the pool has any {self._quota_day()} (Pacific) allowance "
                f"configured; resumes {self._next_reset().isoformat()}."
            )
        raise QuotaExhaustedError(
            f"Every model in the pool has spent its {self._quota_day()} (Pacific) allowance "
            f"({', '.join(exhausted)}); resumes {self._next_reset().isoformat()}."
        )

    def _candidates_for(self, context: dict[str, Any]) -> tuple[ModelSpec, ...]:
        # BUG-035: `_try_reserve_quota`'s atomic UPSERT only gates the
        # ON CONFLICT UPDATE branch — the plain INSERT (the day's first
        # call for a model) has no quota check and always succeeds, so a
        # `daily_quota=0` model (a way to operationally disable a model
        # without removing it from the pool) would let exactly one real
        # call through per day instead of zero. Filtered out here, not
        # just left to `_try_reserve_quota`'s SQL, so a zero-quota model
        # is never a candidate at all — the intent behind the docstring's
        # `self._pool` (the full DECLARED pool, still used as-is for
        # status reporting/capacity totals below) shouldn't leak into
        # which models are actually eligible to serve a call.
        eligible = tuple(spec for spec in self._pool if spec.daily_quota > 0)
        if not context.get("images"):
            return eligible
        vision = tuple(spec for spec in eligible if spec.vision)
        if not vision:
            raise QuotaExhaustedError(
                "No multimodal model in the pool can serve this image call; "
                "refusing to send it to a text-only model."
            )
        return vision

    async def get_quota_status(self) -> dict[str, Any]:
        """Aggregate across the pool, plus a per-model breakdown.

        The aggregate is what capacity claims must be priced against; the
        breakdown is what makes a "we ran out" report checkable (which island
        closed, and how much of the pool is still open).
        """
        day = self._quota_day()
        instructor_filter = (
            LLMQuotaCounter.instructor_id.is_(None)
            if self._instructor_id is None
            else LLMQuotaCounter.instructor_id == self._instructor_id
        )
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        LLMQuotaCounter.model,
                        LLMQuotaCounter.call_count,
                        LLMQuotaCounter.cache_hit_count,
                    ).where(LLMQuotaCounter.quota_day == day, instructor_filter)
                )
            ).all()
        used_by_model = {model: (calls, hits) for model, calls, hits in rows}

        per_model: list[dict[str, Any]] = []
        for spec in self._pool:
            calls, hits = used_by_model.get(spec.model, (0, 0))
            per_model.append(
                {
                    "model": spec.model,
                    "calls_used": calls,
                    "daily_limit": spec.daily_quota,
                    "calls_remaining": max(spec.daily_quota - calls, 0),
                    "cache_hits_today": hits,
                    "rpm_limit": spec.rpm,
                    "vision": spec.vision,
                    "exhausted": calls >= spec.daily_quota,
                }
            )

        calls_used = sum(entry["calls_used"] for entry in per_model)
        cache_hits = sum(entry["cache_hits_today"] for entry in per_model)
        daily_limit = pool_daily_capacity(self._pool)
        total = calls_used + cache_hits
        return {
            "quota_day": day,
            "calls_used": calls_used,
            "daily_limit": daily_limit,
            "calls_remaining": max(daily_limit - calls_used, 0),
            "cache_hits_today": cache_hits,
            "cache_hit_rate": round(cache_hits / total, 3) if total else 0.0,
            "reset_at": self._next_reset().isoformat(),
            "rpm_limit": self._pool[0].rpm,
            "models": per_model,
        }

    def _quota_conflict_target(self) -> dict[str, Any]:
        """Postgres ON CONFLICT must name a real unique index, and a partial
        index's target must match its own WHERE predicate exactly -- there
        are two (shared vs. per-instructor, see LLMQuotaCounter's own
        docstring), so which one applies depends on `self._instructor_id`."""
        if self._instructor_id is None:
            return {
                "index_elements": [LLMQuotaCounter.quota_day, LLMQuotaCounter.model],
                "index_where": LLMQuotaCounter.instructor_id.is_(None),
            }
        return {
            "index_elements": [
                LLMQuotaCounter.quota_day,
                LLMQuotaCounter.model,
                LLMQuotaCounter.instructor_id,
            ],
            "index_where": LLMQuotaCounter.instructor_id.isnot(None),
        }

    async def _try_reserve_quota(self, session: AsyncSession, spec: ModelSpec) -> bool:
        """Atomic check-and-increment for ONE model's island (INSERT ... ON
        CONFLICT ... WHERE): safe under concurrent workers even though Render
        runs one dyno today (ticket V-009 edge case — don't bake in the
        single-dyno assumption).

        Returns False (rather than raising) when this island is spent: the
        caller decides whether another model can serve the call.
        """
        day = self._quota_day()
        stmt = (
            pg_insert(LLMQuotaCounter)
            .values(
                quota_day=day,
                model=spec.model,
                instructor_id=self._instructor_id,
                call_count=1,
                cache_hit_count=0,
            )
            .on_conflict_do_update(
                **self._quota_conflict_target(),
                set_={"call_count": LLMQuotaCounter.call_count + 1},
                where=LLMQuotaCounter.call_count < spec.daily_quota,
            )
            .returning(LLMQuotaCounter.call_count)
        )
        return (await session.execute(stmt)).first() is not None

    async def _close_island(self, session: AsyncSession, spec: ModelSpec) -> None:
        """Pin a model's counter to its configured cap because the API itself
        said the day is over — so the rest of the day's calls skip it without
        re-asking (and `/quota` shows the truth, not our stale guess)."""
        day = self._quota_day()
        stmt = (
            pg_insert(LLMQuotaCounter)
            .values(
                quota_day=day,
                model=spec.model,
                instructor_id=self._instructor_id,
                call_count=spec.daily_quota,
                cache_hit_count=0,
            )
            .on_conflict_do_update(
                **self._quota_conflict_target(),
                set_={"call_count": spec.daily_quota},
            )
        )
        await session.execute(stmt)

    async def _note_cache_hit(self, session: AsyncSession, *, model: str) -> None:
        day = self._quota_day()
        stmt = (
            pg_insert(LLMQuotaCounter)
            .values(
                quota_day=day,
                model=model,
                instructor_id=self._instructor_id,
                call_count=0,
                cache_hit_count=1,
            )
            .on_conflict_do_update(
                **self._quota_conflict_target(),
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
        model: str,
    ) -> None:
        stmt = (
            pg_insert(LLMResponseCache)
            .values(
                input_hash=input_hash,
                prompt_version=prompt_version,
                model=model,
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
        context: dict[str, Any] | None = None,
        prompt: str = "",
        model: str | None = None,
    ) -> None:
        # `prompt`/`context` (e.g. V-022's `consistency_pass`) are stored
        # alongside the response so `tools/replay_call.py` (V-024) can
        # reconstruct the EXACT call byte-for-byte — together with
        # model/temperature they're exactly the inputs `_input_hash` folded
        # in, so a replay can re-hash them and prove nothing drifted.
        # Compression: JSONB columns this large are TOASTed by Postgres
        # automatically (pglz, transparent, no app code) — "store
        # compressed" (V-024 edge case) without hand-rolling it; only the
        # UI's PREVIEW is capped (AuditLog.tsx), never the stored value.
        session.add(
            AuditLog(
                event_type=event_type,
                check_run_id=check_run_id,
                prompt_version=prompt_version,
                input_hash=input_hash,
                payload={
                    "prompt_type": prompt_type,
                    # The model that ACTUALLY served the call, not the pool
                    # head — a replay (V-024) must reconstruct the real call,
                    # and a verdict's model is part of its provenance.
                    "model": model or self._model,
                    "temperature": self._temperature,
                    "context": self._json_safe(context or {}),
                    "prompt": prompt,
                    "response": response,
                },
            )
        )

    async def _call_with_retry(
        self, *, model: str, prompt: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                return await self._transport.generate(
                    model=model, prompt=prompt, temperature=self._temperature, **context
                )
            except TransportDailyQuotaExhausted:
                # Never retried: the window is a DAY, not seconds. Propagate
                # so the caller fails over to the next model immediately.
                raise
            except (TransportRateLimited, TransportServerError) as exc:
                last_exc = exc
                if attempt < self._max_retries - 1:
                    await self._sleep(self._retry_base_seconds * (2**attempt))
        raise ApiDownError(
            f"Gemini did not respond after {self._max_retries} attempts."
        ) from last_exc
