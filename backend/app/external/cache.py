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
