import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, PkCreatedMixin
from app.models.enums import CriterionType

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
