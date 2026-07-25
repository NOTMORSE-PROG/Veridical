"""LLM governor persistence (V-009): daily quota counter + response cache.

Both survive process restarts by design — Render spins down on idle, so an
in-memory counter would silently reset the daily budget (ENGINEERING §3).
"""

from typing import Any

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkCreatedMixin


class LLMQuotaCounter(Base, PkCreatedMixin):
    """One row per Pacific calendar day (Gemini's own reset boundary).

    `call_count` gates the daily budget; `cache_hit_count` is tracked
    separately so the quota meter can show cache-hit rate (D-011) without
    it counting against the real quota.
    """

    __tablename__ = "llm_quota_counter"

    quota_day: Mapped[str] = mapped_column(String(10), unique=True)
    call_count: Mapped[int] = mapped_column(Integer, server_default="0")
    cache_hit_count: Mapped[int] = mapped_column(Integer, server_default="0")
    updated_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LLMResponseCache(Base, PkCreatedMixin):
    """Keyed by (input_hash, prompt_version, model) — checked before every
    real Gemini call (D-011); re-runs and Flow E re-parses cost zero quota.
    """

    __tablename__ = "llm_response_cache"

    input_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prompt_version: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
