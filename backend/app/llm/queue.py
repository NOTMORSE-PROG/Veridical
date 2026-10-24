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
from datetime import datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.errors import ApiDownError, QuotaExhaustedError, RuntimeApiKeyRejectedError
from app.llm.pool import ModelSpec, pool_daily_capacity
from app.llm.result import GenerationResult, generation_result
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
    "TransportModelUnavailable",
    "TransportRateLimited",
    "TransportServerError",
]


class Transport(Protocol):
    """What a real (or test-fake) Gemini transport must implement."""

    async def generate(
        self, *, model: str, prompt: str, temperature: float, **context: Any
    ) -> GenerationResult | dict[str, Any]: ...


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


class _LocalBudgetExhausted(TransportDailyQuotaExhausted):
    """Local records reached the configured cap; no provider closure occurred."""


class TransportModelUnavailable(Exception):
    """The requested model cannot serve this operation; try an eligible reserve."""


class TransportInvalidKey(Exception):
    """The key itself was rejected (401/403, or any APIError Google didn't
    already classify as a per-minute/per-day 429 or a 5xx) -- V-052
    (backend review finding, P1, live-reproduced): this used to propagate
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

    Requested models are tried in configured order. Local budgets include
    configured historical aliases; they do not prove independent provider quota
    buckets or current provider availability. A daily-limit or unavailable-model
    response moves routing to the next eligible configured entry.
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
        cache_bypass: bool = False,
        cache_contract_version: str | None = None,
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
        # BUG-162: off by default -- every existing caller keeps today's
        # cache-saving behavior unchanged. True only for a deliberate
        # `backend/scripts/stability_probe.py` run (`Settings.llm_cache_bypass`,
        # config.py's own docstring for why this exists).
        self._cache_bypass = cache_bypass
        self._cache_contract_version = (
            cache_contract_version or get_settings().llm_cache_contract_version
        )
        self._inflight: dict[str, tuple[asyncio.Lock, int]] = {}
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
    def _input_hash(
        prompt_version: str,
        model: str,
        prompt: str,
        context: dict[str, Any],
        *,
        prompt_type=None,
        temperature=None,
        cache_contract_version=None,
    ) -> str:
        contract = (
            {}
            if cache_contract_version is None
            else {
                "prompt_type": prompt_type,
                "temperature": temperature,
                "cache_contract_version": cache_contract_version,
            }
        )
        payload = json.dumps(
            {
                "prompt_version": prompt_version,
                "model": model,
                "prompt": prompt,
                "context": LLMQueue._json_safe(context) if contract else context,
                **contract,
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
        # Bound memory to currently waiting requests. The shared runtime queue
        # coalesces identical misses; separate processes still require DB locking.
        key = self._input_hash(
            prompt_version,
            self._model,
            prompt,
            context,
            prompt_type=prompt_type,
            temperature=self._temperature,
            cache_contract_version=self._cache_contract_version,
        )
        lock, users = self._inflight.get(key, (asyncio.Lock(), 0))
        self._inflight[key] = (lock, users + 1)
        try:
            async with lock:
                return await self._submit(
                    prompt_type=prompt_type,
                    prompt=prompt,
                    prompt_version=prompt_version,
                    check_run_id=check_run_id,
                    **context,
                )
        finally:
            _, users = self._inflight[key]
            if users == 1:
                del self._inflight[key]
            else:
                self._inflight[key] = (lock, users - 1)

    async def _submit(
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
            spec.model: self._input_hash(
                prompt_version,
                spec.model,
                prompt,
                context,
                prompt_type=prompt_type,
                temperature=self._temperature,
                cache_contract_version=self._cache_contract_version,
            )
            for spec in candidates
        }

        async with self._session_factory() as session:
            for spec in candidates:
                if self._cache_bypass:
                    # BUG-162: a deliberate stability probe -- skip the
                    # READ so this call always reaches the real transport,
                    # even though an identical prompt was already answered
                    # (that's the whole point: measuring the model's own
                    # run-to-run variance, not replaying a stored answer).
                    # `_write_cache` below still runs as normal.
                    continue
                cached = await self._get_cached(session, hashes[spec.model])
                if cached is None:
                    continue
                cached = generation_result(cached)
                await self._note_cache_hit(session, model=spec.model)
                await self._write_audit(
                    session,
                    event_type="llm_cache_hit",
                    prompt_type=prompt_type,
                    prompt_version=prompt_version,
                    input_hash=hashes[spec.model],
                    check_run_id=check_run_id,
                    response=cached.payload,
                    context=context,
                    prompt=prompt,
                    model=spec.model,
                    model_version=cached.model_version,
                )
                await session.commit()
                return cached.payload

        exhausted: list[str] = []
        unavailable: list[str] = []
        for spec in candidates:
            input_hash = hashes[spec.model]

            try:
                generated = await self._call_with_retry(
                    spec=spec,
                    prompt=prompt,
                    context=context,
                    prompt_type=prompt_type,
                    prompt_version=prompt_version,
                    input_hash=input_hash,
                    check_run_id=check_run_id,
                )
            except TransportModelUnavailable:
                # A model-specific 404 is neither a spent allowance nor a
                # transient failure. Keep the physical attempt reservation,
                # record a sanitized failure, and try the existing reserve.
                async with self._session_factory() as session:
                    await self._write_audit(
                        session,
                        event_type="llm_call_failed",
                        prompt_type=prompt_type,
                        prompt_version=prompt_version,
                        input_hash=input_hash,
                        check_run_id=check_run_id,
                        response={
                            "error_code": "model_unavailable",
                            "error": "The requested model cannot serve this operation.",
                        },
                        context=context,
                        prompt=prompt,
                        model=spec.model,
                    )
                    await session.commit()
                unavailable.append(spec.model)
                continue
            except TransportDailyQuotaExhausted as exc:
                # Reality disagreed with our counter (the real cap moved, or
                # something outside this app spent it). Believe the API, close
                # the island for the rest of the day, and try the next model —
                # this is the self-correction that the 20-vs-1400 drift needed.
                async with self._session_factory() as session:
                    if not isinstance(exc, _LocalBudgetExhausted):
                        await self._close_island(session, spec)
                    await self._write_audit(
                        session,
                        event_type="llm_model_exhausted",
                        prompt_type=prompt_type,
                        prompt_version=prompt_version,
                        input_hash=input_hash,
                        check_run_id=check_run_id,
                        response={
                            "error": str(exc),
                            "model": spec.model,
                            "cause": "local_budget"
                            if isinstance(exc, _LocalBudgetExhausted)
                            else "provider_daily_limit",
                        },
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
                # (V-052 ticket edge case, backend review finding P1).
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
                raise RuntimeApiKeyRejectedError(f"Gemini rejected this API key: {exc}") from exc
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
                response = generated.payload
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
                    model_version=generated.model_version,
                )
                await self._write_cache(
                    session,
                    input_hash=input_hash,
                    prompt_version=prompt_version,
                    response=response,
                    model=spec.model,
                    model_version=generated.model_version,
                )
                await session.commit()

            return response

        if unavailable:
            # Other candidates may have spent their allowance, but claiming
            # that every model exhausted quota would erase the availability
            # failure. Reuse the existing honest API-unavailable handling.
            raise ApiDownError("No eligible model could serve this call; a model was unavailable.")
        if not candidates:
            # BUG-035 follow-up (backend review): reachable only if every
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
        active = {spec.model for spec in self._pool}
        alias_targets = {alias: spec.model for spec in self._pool for alias in spec.budget_aliases}
        for spec in self._pool:
            own_calls, hits = used_by_model.get(spec.model, (0, 0))
            historical = sum(used_by_model.get(alias, (0, 0))[0] for alias in spec.budget_aliases)
            raw_debits = own_calls + historical
            per_model.append(
                {
                    "model": spec.model,
                    "calls_used": min(raw_debits, spec.daily_quota),
                    "recorded_budget_debits": raw_debits,
                    "historical_budget_debits": historical,
                    "budget_aliases": list(spec.budget_aliases),
                    "daily_limit": spec.daily_quota,
                    "calls_remaining": max(spec.daily_quota - raw_debits, 0),
                    # Requested-ID cache records appear once in the ledger.
                    "cache_hits_today": hits,
                    "rpm_limit": spec.rpm,
                    "vision": spec.vision,
                    "exhausted": raw_debits >= spec.daily_quota,
                }
            )

        legacy_models = [
            {
                "model": model,
                "recorded_budget_debits": calls,
                "cache_hits_today": hits,
                "budget_model": alias_targets.get(model),
            }
            for model, (calls, hits) in sorted(used_by_model.items())
            if model not in active
        ]
        return {
            "quota_day": day,
            "calls_used": sum(entry["calls_used"] for entry in per_model),
            "daily_limit": pool_daily_capacity(self._pool),
            "calls_remaining": sum(entry["calls_remaining"] for entry in per_model),
            "recorded_budget_debits": sum(calls for calls, _ in used_by_model.values()),
            "cache_hits_today": sum(hits for _, hits in used_by_model.values()),
            # A daily-limit closure can pin the budget counter to its cap.
            # It cannot supply a physical-request denominator for a cache rate.
            "cache_hit_rate": None,
            "reset_at": self._next_reset().isoformat(),
            "rpm_limit": self._pool[0].rpm,
            "models": per_model,
            "legacy_models": legacy_models,
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

    def _quota_owner_filter(self):
        return (
            LLMQuotaCounter.instructor_id.is_(None)
            if self._instructor_id is None
            else LLMQuotaCounter.instructor_id == self._instructor_id
        )

    async def _lock_budget(
        self, session: AsyncSession, spec: ModelSpec, day: str
    ) -> LLMQuotaCounter:
        """Serialize new-policy writers on the canonical owner/day row.

        Old writers must be drained before the alias policy is activated.
        Historical rows retain their original requested identities and values.
        """
        await session.execute(
            pg_insert(LLMQuotaCounter)
            .values(
                quota_day=day,
                model=spec.model,
                instructor_id=self._instructor_id,
                call_count=0,
                cache_hit_count=0,
            )
            .on_conflict_do_nothing(**self._quota_conflict_target())
        )
        return (
            await session.scalars(
                select(LLMQuotaCounter)
                .where(
                    LLMQuotaCounter.quota_day == day,
                    LLMQuotaCounter.model == spec.model,
                    self._quota_owner_filter(),
                )
                .with_for_update()
            )
        ).one()

    async def _try_reserve_quota(self, session: AsyncSession, spec: ModelSpec) -> bool:
        """Reserve against canonical plus alias records under one local cap.

        The transaction ends before any transport call or retry wait. Independent
        queue instances use the same database lock, rather than a process lock.
        """
        day = self._quota_day()
        counter = await self._lock_budget(session, spec, day)
        recorded = await session.scalar(
            select(func.coalesce(func.sum(LLMQuotaCounter.call_count), 0)).where(
                LLMQuotaCounter.quota_day == day,
                LLMQuotaCounter.model.in_((spec.model, *spec.budget_aliases)),
                self._quota_owner_filter(),
            )
        )
        if recorded >= spec.daily_quota:
            return False
        counter.call_count += 1
        await session.flush()
        return True

    async def _close_island(self, session: AsyncSession, spec: ModelSpec) -> None:
        """A provider daily-limit response closes the configured local budget.

        This conservative hold is not a count of physical attempts. Never lower
        previously saved debits, even when configuration now has a smaller cap.
        """
        counter = await self._lock_budget(session, spec, self._quota_day())
        counter.call_count = max(counter.call_count, spec.daily_quota)
        await session.flush()

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

    async def _get_cached(self, session: AsyncSession, input_hash: str) -> GenerationResult | None:
        row = await session.scalar(
            select(LLMResponseCache).where(LLMResponseCache.input_hash == input_hash)
        )
        if row is None or row.response is None:
            return None
        return GenerationResult(payload=row.response, model_version=row.model_version)

    async def _write_cache(
        self,
        session: AsyncSession,
        *,
        input_hash: str,
        prompt_version: str,
        response: Any,
        model: str,
        model_version: str | None = None,
    ) -> None:
        stmt = (
            pg_insert(LLMResponseCache)
            .values(
                input_hash=input_hash,
                prompt_version=prompt_version,
                model=model,
                response=response,
                model_version=model_version,
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
        response: Any,
        context: dict[str, Any] | None = None,
        prompt: str = "",
        model: str | None = None,
        model_version: str | None = None,
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
                    # Requested identity remains the cache/hash/replay input.
                    # A provider redirect can serve a different version.
                    "model": model or self._model,
                    "requested_model": model or self._model,
                    "model_version": model_version,
                    "model_version_source": (
                        "cached_response" if event_type == "llm_cache_hit" else "provider_response"
                    )
                    if model_version is not None
                    else "unrecorded",
                    "response_source": (
                        "cache"
                        if event_type == "llm_cache_hit"
                        else "generation"
                        if event_type == "llm_call"
                        else "not_recorded"
                    ),
                    # BUG-219: explicit, not just the absence of fake.py's
                    # own `"fake_llm": True` — a row written before this fix
                    # shipped has neither key, and the audit summary's
                    # execution-mode derivation (app/audit/service.py) must
                    # tell that "genuinely unknown" case apart from "known
                    # real" rather than defaulting an unmarked old row to
                    # either state by guessing.
                    "fake_llm": False,
                    "temperature": self._temperature,
                    "cache_contract_version": self._cache_contract_version,
                    "context": self._json_safe(context or {}),
                    "prompt": prompt,
                    "response": response,
                },
            )
        )

    async def _call_with_retry(
        self,
        *,
        spec: ModelSpec,
        prompt: str,
        context: dict[str, Any],
        prompt_type: str,
        prompt_version: str,
        input_hash: str,
        check_run_id: int | None,
    ) -> GenerationResult:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            await self._governors[spec.model].acquire()
            async with self._session_factory() as session:
                reserved = await self._try_reserve_quota(session, spec)
                await session.commit()
            if not reserved:
                raise _LocalBudgetExhausted("Configured local request budget reached.")
            # Each SDK invocation has its own reservation, RPM acquisition and
            # durable attempt record, including attempts returning no response.
            async with self._session_factory() as session:
                await self._write_audit(
                    session,
                    event_type="llm_transport_attempt",
                    prompt_type=prompt_type,
                    prompt_version=prompt_version,
                    input_hash=input_hash,
                    check_run_id=check_run_id,
                    response={"attempt": attempt + 1},
                    context=context,
                    prompt=prompt,
                    model=spec.model,
                )
                await session.commit()
            try:
                return generation_result(
                    await self._transport.generate(
                        model=spec.model, prompt=prompt, temperature=self._temperature, **context
                    )
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
