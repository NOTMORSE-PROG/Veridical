"""External citation-verification cache (V-028, F5.6): re-runs of the same
manuscript MUST hit this instead of re-querying CrossRef/S2/OpenLibrary/
GBooks (rate-limit + defense-season survival, ticket AC).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkCreatedMixin


class CitationCache(Base, PkCreatedMixin):
    """Keyed by whichever identifier was actually used to look the citation
    up — DOI, ISBN, or a normalized title (V-029 picks the key per citation;
    this table doesn't care which). `verified_at` gates staleness: a
    retraction status re-check is forced once a cached row is older than
    `settings.citation_cache_stale_days` (ticket edge case — retractions
    land late, so an old "not retracted" can't be trusted forever) even
    though the rest of the cached metadata doesn't need re-fetching.

    `instructor_confirmed` is a durable, cross-run "a human already looked
    at this and it's fine" mark (FEATURES §9 mitigation named in the
    ticket) — the schema carries it now so the confirmation UI (a later
    ticket) has somewhere to write without another migration.
    """

    __tablename__ = "citation_cache"
    __table_args__ = (UniqueConstraint("key_kind", "key_value", name="uq_citation_cache_key"),)

    key_kind: Mapped[str] = mapped_column(String(16))  # "doi" | "isbn" | "title"
    key_value: Mapped[str] = mapped_column(String(512))
    provider: Mapped[str] = mapped_column(
        String(32)
    )  # "crossref" | "s2" | "openlibrary" | "gbooks"
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    instructor_confirmed: Mapped[bool] = mapped_column(Boolean, server_default="false")
    instructor_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
