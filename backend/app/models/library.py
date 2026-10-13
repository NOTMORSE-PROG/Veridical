"""V-077 (D-028): the Library as its own entity.

A `library_entry` says "this manuscript is in the Library", with the
metadata an instructor gave it there. The stored file, extracted text and
F7 embeddings still hang off the `manuscript` row (one document pipeline,
not two); the entry is what makes that document a comparison source.

Invariant owned by `app.library.service`: the F7 embedding tables hold rows
for a manuscript exactly when its entry is `active`. Entering the Library
writes them, leaving it deletes them, so every similarity query searches the
Library and nothing else without needing a membership filter.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, PkCreatedMixin
from app.models.enums import LibraryEntrySource, LibraryEntryStatus, LibraryRemovalReason

if TYPE_CHECKING:
    from app.models.manuscript import Manuscript


class LibraryEntry(Base, PkCreatedMixin):
    __tablename__ = "library_entry"

    manuscript_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("manuscript.id", ondelete="CASCADE"), unique=True
    )
    source: Mapped[LibraryEntrySource] = mapped_column(
        Enum(LibraryEntrySource, native_enum=False, length=32)
    )
    status: Mapped[LibraryEntryStatus] = mapped_column(
        Enum(LibraryEntryStatus, native_enum=False, length=32), index=True
    )
    # Instructor-supplied (or title-page-proposed, then editable) metadata.
    # NULL means "not given", never guessed.
    title: Mapped[str | None] = mapped_column(String(500))
    team_name: Mapped[str | None] = mapped_column(String(200))
    authors: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    program_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("program.id"))
    school_year: Mapped[str | None] = mapped_column(String(20))
    # Fields VERIDICAL proposed from the title page (V-063 extraction) that
    # the instructor has not edited since, so the record can say "read from
    # the title page, check it" instead of presenting a guess as given.
    proposed_fields: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    # Set while `needs_decision`: the requester's own active entry this one
    # may duplicate. Kept after the decision as a record of what was shown.
    possible_duplicate_of_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("library_entry.id", ondelete="SET NULL")
    )
    # Vetted `messages.py` text only (same rule as `ingest_failure_detail`).
    failure_detail: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removal_reason: Mapped[LibraryRemovalReason | None] = mapped_column(
        Enum(LibraryRemovalReason, native_enum=False, length=32)
    )

    manuscript: Mapped["Manuscript"] = relationship(back_populates="library_entry")
