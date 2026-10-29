"""Durable private upload receipts; idempotency is scoped to the authenticated owner."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UploadAttempt(Base):
    __tablename__ = "upload_attempt"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    instructor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("instructor.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    source_bytes: Mapped[int] = mapped_column(BigInteger)
    staging_key: Mapped[str] = mapped_column(String(1024), unique=True)
    source_etag: Mapped[str | None] = mapped_column(String(255))
    upload_transport: Mapped[str] = mapped_column(String(8), server_default="r2")
    # Set when the browser sends the file to storage in parallel parts. The
    # part size is fixed when the attempt begins, so a configuration change
    # can never re-cut parts that are already stored.
    multipart_upload_id: Mapped[str | None] = mapped_column(String(1024))
    multipart_part_bytes: Mapped[int | None] = mapped_column(BigInteger)
    staging_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    manuscript_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("manuscript.id"))
    lease_id: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_upload_attempt_owner_status", "instructor_id", "status"),)
