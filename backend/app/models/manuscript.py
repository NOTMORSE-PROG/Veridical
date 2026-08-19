from datetime import datetime
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.models.base import Base, PkCreatedMixin
from app.models.enums import IngestFailureReason, IngestStatus

if TYPE_CHECKING:
    from app.models.citation import Citation
    from app.models.instructor import Instructor


class Manuscript(Base, PkCreatedMixin):
    __tablename__ = "manuscript"

    instructor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("instructor.id"), index=True)
    # Reference to the stored upload (path/object key), never file content.
    file_ref: Mapped[str] = mapped_column(String(1024))
    # BUG-022: group_label defaults to a constant ("Ungrouped") for any
    # manuscript not explicitly grouped, so it alone can't distinguish two
    # uploads. The instructor's own filename usually can. NULL for rows
    # ingested before this column existed (no backfill source).
    original_filename: Mapped[str | None] = mapped_column(String(255))
    # V-062: `group_label` is now a write-through display cache of
    # `group.name` (kept in sync at ingest time by
    # `app/groups/service.py::resolve_or_create_group`), not the raw
    # instructor-typed text -- two differently-cased submissions of the
    # same team resolve to one Group and one canonical spelling here.
    # Column itself, and every reader of it, is unchanged; NULL is never
    # valid (every row has SOME label, even the default).
    group_label: Mapped[str] = mapped_column(String(200))
    # NULL only for rows ingested before this column existed (migration
    # 0025's backfill populates every pre-existing row; NEVER null for a
    # row created after this ships — `ingest_upload` always resolves one).
    group_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("manuscript_group.id"), index=True
    )
    ingest_status: Mapped[IngestStatus] = mapped_column(
        Enum(IngestStatus, native_enum=False), server_default=IngestStatus.pending
    )
    # BUG-016: a failed row must say why, not dead-end silently. NULL means
    # "failed before this field existed" (old rows), never fabricated.
    ingest_failure_reason: Mapped[IngestFailureReason | None] = mapped_column(
        Enum(IngestFailureReason, native_enum=False)
    )
    # Chapter/section hierarchy produced by ingestion (F1); shape owned by V-004.
    section_tree: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Reserved for V7 (D-005): schema ready, feature absent. submitted_by has
    # no FK on purpose — the student entity doesn't exist and may never.
    version: Mapped[int | None]
    submitted_by: Mapped[int | None] = mapped_column(BigInteger)
    # V-042: set when the instructor purges the F7 embedding archive + the
    # stored files. The Manuscript row, check-run history, and any decision
    # are deliberately kept -- NULL means "never purged", never fabricated.
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    instructor: Mapped["Instructor"] = relationship(back_populates="manuscripts")
    archive: Mapped["ManuscriptArchive | None"] = relationship(back_populates="manuscript")
    chapter_archives: Mapped[list["ManuscriptChapterArchive"]] = relationship(
        back_populates="manuscript", cascade="all, delete-orphan"
    )
    passage_archives: Mapped[list["ManuscriptPassageArchive"]] = relationship(
        back_populates="manuscript", cascade="all, delete-orphan"
    )
    citations: Mapped[list["Citation"]] = relationship(back_populates="manuscript")


class ManuscriptArchive(Base, PkCreatedMixin):
    """Whole-document embedding archive for the originality/reuse check
    (F7.1, V-036) — one row per manuscript. `model_id` is recorded on every
    row (ticket edge case: a future model change makes old vectors
    incomparable — V-037's query must filter by model_id, never mix)."""

    __tablename__ = "manuscript_archive"

    manuscript_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("manuscript.id", ondelete="CASCADE"), unique=True
    )
    # Dim comes from settings (EMBEDDING_DIM) and is baked in at migration
    # time — potion-base-8M finalized at V-036 pickup (see config.py).
    embedding: Mapped[list[float]] = mapped_column(Vector(get_settings().embedding_dim))
    model_id: Mapped[str] = mapped_column(String(200))

    manuscript: Mapped["Manuscript"] = relationship(back_populates="archive")

    __table_args__ = (
        # HNSW (not ivfflat): buildable on an empty table, and the archive
        # grows slowly (~20 groups/term). Cosine matches F7's similarity use.
        Index(
            "ix_manuscript_archive_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class ManuscriptChapterArchive(Base, PkCreatedMixin):
    """Per-chapter embeddings (F7.1, V-036) — enables V-037's "a chapter
    transplanted into a new doc" AC and later F7.4 section similarity.
    Many rows per manuscript, unlike the whole-document archive above."""

    __tablename__ = "manuscript_chapter_archive"

    manuscript_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("manuscript.id", ondelete="CASCADE"), index=True
    )
    chapter_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    # Anchor: page (PDF) or paragraph (DOCX) — same "one or the other"
    # convention as every other anchor in this schema (Citation, Flag).
    page: Mapped[int | None] = mapped_column(Integer)
    paragraph: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(get_settings().embedding_dim))
    model_id: Mapped[str] = mapped_column(String(200))

    manuscript: Mapped["Manuscript"] = relationship(back_populates="chapter_archives")

    __table_args__ = (
        UniqueConstraint(
            "manuscript_id",
            "chapter_index",
            name="uq_manuscript_chapter_archive_manuscript_chapter",
        ),
        Index(
            "ix_manuscript_chapter_archive_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class ManuscriptPassageArchive(Base, PkCreatedMixin):
    """Passage-level embeddings (F7.4, V-072) — one row per ~150-word
    passage, many more rows per manuscript than the chapter archive above.
    Includes reference-list and block-quote passages (tagged via
    `is_reference_list`/`is_block_quote`, never dropped at this layer) —
    F7.1-3's chapter/whole-doc vectors already exclude that text entirely
    (`app/checks/reuse/embed.py`'s own docstring); F7.4 needs it STORED so
    the instructor's exclusion toggle (ticket AC7) has something real to
    reveal when switched off, not silently unavailable.

    `char_start`/`char_end` are offsets within the OWNING CHAPTER's own
    assembled text (`"\\n".join` of its content blocks), not the whole
    document and not the original file's byte offsets — see
    `PassageEmbedding`'s own docstring for what that basis is.

    `text`/`context_text` are the bounded-excerpt mechanism itself (owner
    ruling, carried from V-058/BUG-050 Branch B): both are computed and
    truncated ONCE here, at embed time, from this manuscript's own
    extraction — never a live read of a (potentially another account's)
    file at match-view time. This is what lets the F7.4 passage-pair
    viewer show a matched passage's actual text without ever opening the
    matched manuscript's stored file.
    """

    __tablename__ = "manuscript_passage_archive"

    manuscript_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("manuscript.id", ondelete="CASCADE"), index=True
    )
    passage_index: Mapped[int] = mapped_column(Integer)
    chapter_index: Mapped[int] = mapped_column(Integer)
    # Same "page (PDF) or paragraph (DOCX), one or the other" convention as
    # every other anchor in this schema (Citation, Flag, ManuscriptChapterArchive).
    page: Mapped[int | None] = mapped_column(Integer)
    paragraph: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    context_text: Mapped[str] = mapped_column(Text)
    is_reference_list: Mapped[bool] = mapped_column(default=False)
    is_block_quote: Mapped[bool] = mapped_column(default=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(get_settings().embedding_dim))
    model_id: Mapped[str] = mapped_column(String(200))

    manuscript: Mapped["Manuscript"] = relationship(back_populates="passage_archives")

    __table_args__ = (
        UniqueConstraint(
            "manuscript_id",
            "passage_index",
            name="uq_manuscript_passage_archive_manuscript_passage",
        ),
        Index(
            "ix_manuscript_passage_archive_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
