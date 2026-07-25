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

    instructor: Mapped["Instructor"] = relationship(back_populates="rubrics")
    criteria: Mapped[list["Criterion"]] = relationship(
        back_populates="rubric", order_by="Criterion.position"
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

    rubric: Mapped["Rubric"] = relationship(back_populates="criteria")
