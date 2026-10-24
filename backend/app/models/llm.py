"""LLM governor persistence (V-009): daily quota counter + response cache.

Both survive process restarts by design — Render spins down on idle, so an
in-memory counter would silently reset the daily budget (ENGINEERING §3).
"""

from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkCreatedMixin


class LLMQuotaCounter(Base, PkCreatedMixin):
    """Local records per (configured budget day, requested model, key owner).

    Original requested identities and debits remain saved. A configured
    historical alias can contribute to another entry's conservative local cap;
    this does not establish independent or shared provider quota buckets.

    `instructor_id` NULL = the shared pool key; a real id = that
    instructor's own BYOK key (V-052), never mixed with the shared local
    counter for the same model. Different keys do not prove different provider
    projects or additional capacity. Two
    partial unique indexes (not one plain constraint) because Postgres
    unique constraints treat NULL as distinct from every other NULL, which
    would let unlimited shared-key rows exist for the same (day, model);
    see migration 0022 (same pattern as `share_link`'s migration 0020).

    `call_count` gates the local budget and may include a daily-limit closure
    hold. It is not a physical-attempt count. `cache_hit_count` records reuse
    separately and does not consume that local budget.
    """

    __tablename__ = "llm_quota_counter"
    __table_args__ = (
        Index(
            "uq_llm_quota_counter_shared",
            "quota_day",
            "model",
            unique=True,
            postgresql_where=text("instructor_id IS NULL"),
        ),
        Index(
            "uq_llm_quota_counter_own",
            "quota_day",
            "model",
            "instructor_id",
            unique=True,
            postgresql_where=text("instructor_id IS NOT NULL"),
        ),
    )

    quota_day: Mapped[str] = mapped_column(String(10))
    model: Mapped[str] = mapped_column(String(100))
    instructor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("instructor.id", ondelete="CASCADE")
    )
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
    # Optional provider metadata from the original generation. Legacy rows stay
    # NULL; a requested model string is not evidence of its serving version.
    model_version: Mapped[str | None] = mapped_column(Text, nullable=True)
