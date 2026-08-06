from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, PkCreatedMixin

if TYPE_CHECKING:
    from app.models.manuscript import Manuscript
    from app.models.rubric import Rubric


class Instructor(Base, PkCreatedMixin):
    __tablename__ = "instructor"

    # 320 = RFC 5321 max address length.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    # Nullable until auth lands (V-014): the dev seed account exists first,
    # and V-014 owns the hashing scheme decision.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    # NULL = hasn't dismissed the first-run welcome banner yet (V-055,
    # Flow A). Persisted server-side, not session-only, so it survives
    # logout and a different browser.
    onboarding_dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    rubrics: Mapped[list["Rubric"]] = relationship(back_populates="instructor")
    manuscripts: Mapped[list["Manuscript"]] = relationship(back_populates="instructor")
