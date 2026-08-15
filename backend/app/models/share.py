from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.run import CheckRun


class ShareLink(Base):
    """A read-only, tokenized link to one report (F8.7, V-040) -- the
    lightest honest implementation of "shared down to adviser" (proposal
    §1.4): no adviser accounts, the token itself is the credential. Same
    "the token IS the primary key" shape as `Session` (one indexed read,
    no separate id column, no separate revocation list) -- not
    `PkCreatedMixin`, whose own `id` PK would conflict with this.

    Rows are immutable once created, same versioning discipline as
    `Rubric`/`CheckRun` history: "regenerate" never mutates an existing
    row, it revokes the current one and inserts a new one, so a link
    that was ever valid can always be audited as having existed. Only
    one row per `check_run_id` may be un-revoked at a time (enforced in
    the service layer, not the DB -- same pattern `raise_if_decided`
    already uses for a different single-active-state invariant).
    """

    __tablename__ = "share_link"

    # secrets.token_urlsafe(32) => 256 bits of entropy, comfortably over
    # the AC's >=128-bit floor; 43 chars, 64 leaves headroom (Session's
    # own precedent).
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    check_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("check_run.id", ondelete="CASCADE"), index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Optional per FEATURES' own "revocable, optional expiry" -- NULL
    # means no expiry was set, never a fabricated far-future date.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    check_run: Mapped["CheckRun"] = relationship()
