from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Enum, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.models.base import Base, PkCreatedMixin
from app.models.enums import IngestStatus

if TYPE_CHECKING:
    from app.models.instructor import Instructor


class Manuscript(Base, PkCreatedMixin):
    __tablename__ = "manuscript"

    instructor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("instructor.id"), index=True)
    group_label: Mapped[str] = mapped_column(String(200))
    # Reference to the stored upload (path/object key), never file content.
    file_ref: Mapped[str] = mapped_column(String(1024))
    ingest_status: Mapped[IngestStatus] = mapped_column(
        Enum(IngestStatus, native_enum=False), server_default=IngestStatus.pending
    )
    # Chapter/section hierarchy produced by ingestion (F1); shape owned by V-004.
    section_tree: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Reserved for V7 (D-005): schema ready, feature absent. submitted_by has
    # no FK on purpose — the student entity doesn't exist and may never.
    version: Mapped[int | None]
    submitted_by: Mapped[int | None] = mapped_column(BigInteger)

    instructor: Mapped["Instructor"] = relationship(back_populates="manuscripts")
    archive: Mapped["ManuscriptArchive | None"] = relationship(back_populates="manuscript")


class ManuscriptArchive(Base, PkCreatedMixin):
    """Embedding archive for the originality/reuse check (F7.1)."""

    __tablename__ = "manuscript_archive"

    manuscript_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("manuscript.id", ondelete="CASCADE"), unique=True
    )
    # Dim comes from settings (EMBEDDING_DIM) and is baked in at migration
    # time; V-036 finalizes the embedding model (see config.py).
    embedding: Mapped[list[float]] = mapped_column(Vector(get_settings().embedding_dim))

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
