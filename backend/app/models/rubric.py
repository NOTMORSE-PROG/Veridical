import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, PkCreatedMixin
from app.models.enums import CriterionType, RubricParseStatus

if TYPE_CHECKING:
    from app.models.instructor import Instructor


class Rubric(Base, PkCreatedMixin):
    """One VERSION of an uploaded required format (F2.4, D-010).

    Re-uploading a format creates a new row sharing rubric_family_id with
    version+1 — never an UPDATE, so old reports keep their exact rubric.
    """

    __tablename__ = "rubric"
    __table_args__ = (UniqueConstraint("rubric_family_id", "version"),)

    instructor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("instructor.id"), index=True)
    rubric_family_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=func.gen_random_uuid())
    version: Mapped[int] = mapped_column(server_default="1")
    title: Mapped[str] = mapped_column(String(300))
    # Reference to the stored source document (path/object key), not content.
    source_file: Mapped[str | None] = mapped_column(String(1024))
    # F2.2 validation gate outcome (V-011): "needs_review" means the parse
    # exhausted its retries — `criteria` may be a partial set, never a
    # failed upload. `parse_issues` carries the last attempt's reasons
    # (coverage ratio, duplicates) so the review screen can explain why.
    parse_status: Mapped[RubricParseStatus] = mapped_column(
        Enum(RubricParseStatus, native_enum=False), server_default=RubricParseStatus.parsed.value
    )
    parse_issues: Mapped[list[Any] | None] = mapped_column(JSONB)
    # F2.3: nothing runs against a rubric until the instructor confirms
    # (V-012). V-013 generalizes this to "exactly one active version per
    # family" when re-uploads exist; V-012 only ever has one version to
    # activate.
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # V-064 (AC1): a FAMILY-level attribute (denormalized onto every
    # version row, same as `title`) -- NULL = "not set", eligible for
    # everything (AC3), never guessed. Kept in sync across a family's
    # versions by `app/rubric/service.py::set_rubric_family_program`.
    program_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("program.id"))

    instructor: Mapped["Instructor"] = relationship(back_populates="rubrics")
    # cascade="all, delete-orphan" + passive_deletes=True: deleting a
    # Rubric deletes its criteria too, via Postgres's own ON DELETE
    # CASCADE (the FK below) rather than SQLAlchemy first UPDATE-ing
    # each child's FK to NULL in Python — which fails outright since
    # rubric_id is NOT NULL. `passive_deletes` alone (no delete cascade)
    # still tried the NULL-out for any already-loaded children; found
    # live deleting a rubric via delete_rubric (V-013).
    criteria: Mapped[list["Criterion"]] = relationship(
        back_populates="rubric",
        order_by="Criterion.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Criterion(Base, PkCreatedMixin):
    """One checkable requirement decomposed from a rubric (F2.1)."""

    __tablename__ = "criterion"

    # Criteria are part of their rubric version; they go when it goes.
    rubric_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rubric.id", ondelete="CASCADE"), index=True
    )
    # native_enum=False → plain VARCHAR: no Postgres enum type to migrate
    # when members change (same for every enum column in the schema).
    type: Mapped[CriterionType] = mapped_column(Enum(CriterionType, native_enum=False))
    text: Mapped[str] = mapped_column(Text)
    # What evidence satisfies the criterion, as parsed/edited (F2.3).
    evidence: Mapped[str | None] = mapped_column(Text)
    weight: Mapped[Decimal] = mapped_column(Numeric(8, 3))
    position: Mapped[int]
    # V-069 AC1/AC3: a graded performance scale's own named levels, as
    # STRUCTURED data -- `[{"level": 1, "name": "Beginner", "descriptor":
    # "...", "points": 1.0}, ...]`, ascending. NULL/empty = an ordinary
    # pass/fail criterion (the common case, and every criterion before
    # this column existed) -- nothing downstream (grading, scoring,
    # export) branches on a global flag, only on whether THIS criterion
    # carries levels, so a rubric mixing levelled and pass/fail criteria
    # (ticket edge case) grades each one against its own vocabulary.
    levels: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    rubric: Mapped["Rubric"] = relationship(back_populates="criteria")
