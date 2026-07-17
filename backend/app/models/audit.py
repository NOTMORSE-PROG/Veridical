from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkCreatedMixin


class AuditLog(Base, PkCreatedMixin):
    """Append-only trail: every AI call, override, and decision (F8.10).

    Immutability is enforced IN THE DATABASE by a BEFORE UPDATE/DELETE
    trigger (see migration 0001) — a REVOKE alone is a no-op for the
    superuser/owner roles this app runs as (verified 2026-07-17, V-003).
    This is the one sanctioned place for raw AI payloads (CODING.md §1:
    logs get ids/hashes, this table gets the content).
    """

    __tablename__ = "audit_log"

    event_type: Mapped[str] = mapped_column(String(100))
    # Plain id, deliberately NOT a ForeignKey: audit rows must outlive and
    # never block whatever they reference.
    check_run_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    # Queryable AI-call metadata (TESTING.md §3); NULL for non-AI events.
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    input_hash: Mapped[str | None] = mapped_column(String(128))
    agreement_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    # Full event body (raw AI response, override before/after, decision…).
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
