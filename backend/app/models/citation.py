from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, PkCreatedMixin
from app.models.enums import CitationParseStatus

if TYPE_CHECKING:
    from app.models.manuscript import Manuscript


class Citation(Base, PkCreatedMixin):
    """One reference-list entry, structured (F1.5) — the work queue for the
    citation-integrity checks (F5). `raw_text` is always kept: a failed
    parse preserves the entry instead of dropping it (honest-boundary rule),
    and every structured field must be re-derivable from it."""

    __tablename__ = "citation"

    manuscript_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("manuscript.id", ondelete="CASCADE"), index=True
    )
    # Position in the reference list (0-based) — stable citation anchor.
    order_index: Mapped[int]
    raw_text: Mapped[str] = mapped_column(Text)
    authors: Mapped[list[str] | None] = mapped_column(JSONB)
    year: Mapped[int | None]
    title: Mapped[str | None] = mapped_column(Text)
    venue: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(String(255))
    isbn: Mapped[str | None] = mapped_column(String(32))
    url: Mapped[str | None] = mapped_column(String(2048))
    parse_status: Mapped[CitationParseStatus] = mapped_column(
        Enum(CitationParseStatus, native_enum=False)
    )

    manuscript: Mapped["Manuscript"] = relationship(back_populates="citations")
