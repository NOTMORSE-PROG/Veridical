"""`citation_cache` reads/writes (V-028): checked BEFORE every provider
call, written after every real one. A cache hit older than
`settings.citation_cache_stale_days` is treated as a miss — retractions
land late, so an old "not retracted" can't be trusted forever (ticket
edge case) — but this only affects THIS record's freshness, not the
whole cache (a fresh DOI lookup for a different citation is unaffected).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.external.schemas import VerificationResult
from app.models.citation_cache import CitationCache


def _to_result(row: CitationCache) -> VerificationResult:
    return VerificationResult(**row.result)


async def get_cached(
    session: AsyncSession, *, key_kind: str, key_value: str, stale_days: int
) -> VerificationResult | None:
    row = await session.scalar(
        select(CitationCache).where(
            CitationCache.key_kind == key_kind, CitationCache.key_value == key_value
        )
    )
    if row is None:
        return None
    age = datetime.now(UTC) - row.verified_at
    if age > timedelta(days=stale_days):
        return None  # stale: caller re-verifies and overwrites via store_result
    return _to_result(row)


async def is_instructor_confirmed(session: AsyncSession, *, key_kind: str, key_value: str) -> bool:
    """BUG-078/FEATURES.md §9: has a human already confirmed this specific
    source is legitimate? Kept separate from `get_cached` (rather than
    folding into its return shape) so the common existence-confirmed/
    retracted/corrected paths pay no extra query -- only a `not_found`
    verdict needs to ask this."""
    row = await session.scalar(
        select(CitationCache.instructor_confirmed).where(
            CitationCache.key_kind == key_kind, CitationCache.key_value == key_value
        )
    )
    return bool(row)


async def citation_source_cached(session: AsyncSession, *, key_kind: str, key_value: str) -> bool:
    """Read-only existence check -- lets a caller validate a key BEFORE
    committing an irreversible local mutation, rather than discovering
    "no such row" only after (`backend-critic`, BUG-078 review: ordering
    matters here, see `confirm_citation_source`'s own docstring)."""
    row = await session.scalar(
        select(CitationCache.id).where(
            CitationCache.key_kind == key_kind, CitationCache.key_value == key_value
        )
    )
    return row is not None


async def confirm_citation_source(session: AsyncSession, *, key_kind: str, key_value: str) -> bool:
    """Marks a cached external-lookup result as manually confirmed
    legitimate (FEATURES.md §9: "cache instructor's manual confirmations
    so the same source isn't re-flagged") -- durable and cross-run/
    cross-manuscript, since `citation_cache` is keyed by source identity
    (DOI/ISBN/title), not by instructor or manuscript. Returns False if no
    cache row exists yet for this key -- callers that need to guard
    against this BEFORE mutating anything else should check
    `citation_source_cached` first (this function commits on success and
    is not meant to roll back a caller's other in-progress writes).

    Self-contained (commits its own write), same convention as every
    other mutator in this module (`store_result`) -- but the CALLER
    (`app.flags.service.confirm_citation_source`) deliberately invokes
    this LAST, after its own `Flag`/`AuditLog` commit, not first
    (`backend-critic`, BUG-078 review, live-reproduced): this is the
    GLOBAL, PERMANENT half of the action (no un-confirm exists, and it
    silences this source for every future manuscript, any instructor) --
    if a crash happens between the two commits, the safer failure mode is
    "the flag is honestly resolved with a real audit trail, but the
    cross-manuscript suppression didn't yet take effect" (recoverable —
    confirming again is idempotent), never "the source is silently
    silenced everywhere with zero local record of who did it or why."
    """
    row = await session.scalar(
        select(CitationCache).where(
            CitationCache.key_kind == key_kind, CitationCache.key_value == key_value
        )
    )
    if row is None:
        return False
    row.instructor_confirmed = True
    row.instructor_confirmed_at = datetime.now(UTC)
    await session.commit()
    return True


async def store_result(
    session: AsyncSession,
    *,
    key_kind: str,
    key_value: str,
    provider: str,
    result: VerificationResult,
) -> None:
    payload = {
        "found": result.found,
        "provider": result.provider,
        "title": result.title,
        "retracted": result.retracted,
        "retraction_detail": result.retraction_detail,
        "is_correction": result.is_correction,
        "content_checkable": result.content_checkable,
        "url": result.url,
        "raw": result.raw,
    }
    stmt = (
        pg_insert(CitationCache)
        .values(key_kind=key_kind, key_value=key_value, provider=provider, result=payload)
        .on_conflict_do_update(
            index_elements=[CitationCache.key_kind, CitationCache.key_value],
            set_={"provider": provider, "result": payload, "verified_at": datetime.now(UTC)},
        )
    )
    await session.execute(stmt)
    await session.commit()
