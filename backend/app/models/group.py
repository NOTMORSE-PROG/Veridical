"""V-062: a real `Group` entity replacing the free-text `Manuscript.group_label`
as the identity mechanism, plus a `program` reference table.

`program` is deliberately a table, not a Python enum (ground rule 7 /
DECIDED 2026-08-16 on the ticket): CS and IT are TIP's programs today, and
another school (or TIP next year) may have different ones — adding one
must be a data change, never a redeploy.
"""

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, PkCreatedMixin


class Program(Base, PkCreatedMixin):
    """CS / IT today (seeded once by migration 0025); an instructor or a
    future admin surface can add rows without any code change."""

    __tablename__ = "program"

    name: Mapped[str] = mapped_column(String(50), unique=True)


class Group(Base, PkCreatedMixin):
    """A capstone team, scoped to the instructor who created it.

    Table name is `manuscript_group`, not `group` (a reserved SQL keyword
    that would otherwise need quoting on every reference) — matches the
    existing `manuscript_archive` / `manuscript_chapter_archive` naming.

    `name_normalized` (trim + collapse whitespace + lowercase, see
    `app/groups/service.py::normalize_group_name`) is what two differently
    -cased spellings of the same team ("Group 4" / "group 4") resolve
    against; `name` keeps whichever casing was submitted first, since that
    is what the instructor actually typed.
    """

    __tablename__ = "manuscript_group"

    instructor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("instructor.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    name_normalized: Mapped[str] = mapped_column(String(200))
    # NULL = "not set", never guessed (same convention as
    # `ingest_failure_reason`) — the backfill (migration 0025) always
    # leaves this NULL; V-063's title-page inference is the only path
    # that ever sets it for a NEW group.
    program_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("program.id"))

    __table_args__ = (
        UniqueConstraint(
            "instructor_id", "name_normalized", name="uq_manuscript_group_instructor_name"
        ),
    )

    members: Mapped[list["GroupMember"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class GroupMember(Base, PkCreatedMixin):
    """A name observed on a manuscript's own title page (V-063's `By:`
    block extraction) -- the durable record the matching rule needs:
    "short name matches AND at least one member overlaps" is how two
    different teams that happen to share a short acronym stay distinct
    groups instead of silently merging (see V-063's own DECIDED section
    for the real-document proof this rule is built on)."""

    __tablename__ = "group_member"

    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("manuscript_group.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))

    group: Mapped["Group"] = relationship(back_populates="members")
