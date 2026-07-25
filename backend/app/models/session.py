from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.instructor import Instructor


class Session(Base):
    """A logged-in session (F9.1). The token itself IS the primary key —
    the cookie value — so a lookup is one indexed read, and `DELETE ...
    WHERE token = ...` kills a session server-side (logout, F9.1 AC)
    without any separate revocation list.
    """

    __tablename__ = "session"

    # secrets.token_urlsafe(32) => 43 chars; 64 leaves headroom.
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    instructor_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("instructor.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    instructor: Mapped["Instructor"] = relationship()
