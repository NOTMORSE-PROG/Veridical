"""Durable authenticated browser-to-R2 manuscript upload lifecycle (F1/F9)."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_upload_instructor
from app.config import get_settings
from app.db import get_session
from app.errors import (
    ConflictError,
    DocumentPreparationBusyError,
    FileTooLargeError,
    NotFoundError,
    RateLimitedError,
)
from app.ingest.service import clean_original_filename
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.upload_attempt import UploadAttempt
from app.ratelimit import enforce_action_rate_limit
from app.storage import R2Storage, get_storage

router = APIRouter(tags=["ingestion"])
_relay_gate = threading.BoundedSemaphore(get_settings().direct_upload_relay_transfers)


def _store_relay(storage, source, key):
    storage.upload_stream(source, key)
    return storage.object_metadata(key)


async def _relay_transfer(storage, source, key):
    work = asyncio.create_task(asyncio.to_thread(_store_relay, storage, source, key))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        # A cancelled request cannot terminate its SDK thread. Keep its source,
        # storage client, DB fence and transfer slot alive until that thread ends.
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if work.done() and not work.cancelled():
            work.exception()
        raise


class BeginUpload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    attempt_id: str
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)

    @field_validator("attempt_id")
    @classmethod
    def canonical_uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("Invalid upload identity.")
        return value


def enabled_settings():
    settings = get_settings()
    if (
        not settings.direct_manuscript_upload_enabled
        or settings.storage_backend != "r2"
        or not settings.native_compute_url
        or not settings.native_compute_secret
    ):
        raise ConflictError("Direct manuscript uploads are not available.")
    return settings


async def owned_attempt(session, instructor_id, attempt_id, *, lock=False):
    query = select(UploadAttempt).where(
        UploadAttempt.id == attempt_id, UploadAttempt.instructor_id == instructor_id
    )
    if lock:
        query = query.with_for_update()
    attempt = await session.scalar(query)
    if attempt is None:
        raise NotFoundError("Upload attempt not found.")
    return attempt


def status_response(attempt):
    settings = get_settings()
    return {
        "attempt_id": attempt.id,
        "status": attempt.status,
        "manuscript_id": attempt.manuscript_id,
        "result": attempt.response,
        "error": attempt.error,
        "transport": attempt.upload_transport,
        "poll_after_ms": settings.direct_upload_poll_seconds * 1000,
        "poll_timeout_ms": settings.direct_upload_lease_seconds * 1000,
    }


@router.post("/manuscripts/uploads")
async def begin_upload(
    body: BeginUpload,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_upload_instructor)],
):
    settings = enabled_settings()
    enforce_action_rate_limit(settings, "manuscript_ingest", instructor.id)
    if body.size > settings.direct_manuscript_upload_max_mb * 1024 * 1024:
        raise FileTooLargeError(
            f"Choose a manuscript no larger than {settings.direct_manuscript_upload_max_mb} MB."
        )
    filename = clean_original_filename(body.filename)
    if not filename or not filename.lower().endswith((".pdf", ".docx")):
        raise ConflictError("Choose a PDF or DOCX manuscript.")
    # Serialize admission including the initially empty table, so simultaneous
    # requests cannot bypass per-owner/global pending-object ceilings.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
        {"scope": "upload_attempt:admission"},
    )
    existing = await session.get(UploadAttempt, body.attempt_id)
    now = datetime.now(UTC)
    if existing:
        if existing.instructor_id != instructor.id:
            raise NotFoundError("Upload attempt not found.")
        if existing.filename != filename or existing.source_bytes != body.size:
            raise ConflictError("This upload identity belongs to another selected file.")
        attempt = existing
    else:
        staging_reservation = case(
            (UploadAttempt.staging_deleted_at.is_(None), UploadAttempt.source_bytes), else_=0
        )
        original_reservation = case(
            (
                and_(UploadAttempt.manuscript_id.is_not(None), Manuscript.purged_at.is_(None)),
                UploadAttempt.source_bytes,
            ),
            (
                and_(
                    UploadAttempt.manuscript_id.is_(None),
                    UploadAttempt.status.in_(("pending", "queued", "processing")),
                ),
                UploadAttempt.source_bytes,
            ),
            else_=0,
        )
        reserved = await session.scalar(
            select(func.coalesce(func.sum(staging_reservation + original_reservation), 0))
            .select_from(UploadAttempt)
            .outerjoin(Manuscript, Manuscript.id == UploadAttempt.manuscript_id)
        )
        if reserved + body.size * 2 > settings.direct_upload_storage_budget_mb * 1024 * 1024:
            raise RateLimitedError(
                "The free storage budget is full. "
                "Remove unneeded files in Stored Files before uploading."
            )
        active = select(UploadAttempt).where(
            UploadAttempt.status.in_(("pending", "queued", "processing")),
            (UploadAttempt.status != "pending") | (UploadAttempt.expires_at > now),
        )
        total = await session.scalar(select(func.count()).select_from(active.subquery()))
        owned = await session.scalar(
            select(func.count()).select_from(
                active.where(UploadAttempt.instructor_id == instructor.id).subquery()
            )
        )
        if (
            total >= settings.direct_upload_pending_global
            or owned >= settings.direct_upload_pending_per_instructor
        ):
            raise RateLimitedError("Finish an existing upload before starting another.")
        attempt = UploadAttempt(
            id=body.attempt_id,
            instructor_id=instructor.id,
            filename=filename,
            source_bytes=body.size,
            upload_transport=settings.direct_upload_transport,
            staging_key=f"{settings.direct_upload_staging_subdir}/{uuid4()}",
            status="pending",
            expires_at=now + timedelta(seconds=settings.direct_upload_url_ttl_seconds),
        )
        session.add(attempt)
    await session.commit()
    response = status_response(attempt)
    if attempt.status == "pending":
        ttl = int((attempt.expires_at - now).total_seconds())
        if ttl <= 0:
            raise ConflictError("This upload link expired. Start a new upload attempt.")
        if attempt.upload_transport == "r2":
            storage = get_storage(settings)
            try:
                assert isinstance(storage, R2Storage)
                response["upload_url"] = await asyncio.to_thread(
                    storage.presign_upload, attempt.staging_key, attempt.source_bytes, ttl
                )
                response["upload_headers"] = {"Content-Type": "application/octet-stream"}
            finally:
                storage.close()
    return response


@router.post("/manuscripts/uploads/{attempt_id}/file")
async def relay_upload(
    attempt_id: str,
    file: UploadFile,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_upload_instructor)],
):
    settings = enabled_settings()
    if settings.direct_upload_transport != "relay":
        raise ConflictError("This upload uses direct storage transfer.")
    attempt = await owned_attempt(session, instructor.id, attempt_id, lock=True)
    if attempt.upload_transport != "relay":
        raise ConflictError("This attempt uses direct storage transfer.")
    if attempt.status != "pending":
        return status_response(attempt)
    if attempt.expires_at <= datetime.now(UTC):
        raise ConflictError("This upload link expired. Start a new upload attempt.")
    if type(file.size) is not int or file.size != attempt.source_bytes:
        raise ConflictError("The transferred file does not match the selected file size.")
    if not _relay_gate.acquire(blocking=False):
        raise DocumentPreparationBusyError(
            "Another file transfer is running. Retry this upload shortly."
        )
    storage = None
    try:
        storage = get_storage(settings)
        assert isinstance(storage, R2Storage)
        await file.seek(0)
        metadata = await _relay_transfer(storage, file.file, attempt.staging_key)
        if (
            type(metadata.get("ContentLength")) is not int
            or metadata["ContentLength"] != attempt.source_bytes
            or not isinstance(metadata.get("ETag"), str)
            or not metadata["ETag"]
        ):
            raise ConflictError("The stored upload could not be confirmed.")
        attempt.source_etag, attempt.status = metadata["ETag"], "queued"
        await session.commit()
        return status_response(attempt)
    finally:
        try:
            if storage is not None:
                storage.close()
        finally:
            _relay_gate.release()


@router.post("/manuscripts/uploads/{attempt_id}/complete")
async def complete_upload(
    attempt_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_upload_instructor)],
):
    settings = enabled_settings()
    attempt = await owned_attempt(session, instructor.id, attempt_id, lock=True)
    if attempt.status == "pending":
        if attempt.expires_at <= datetime.now(UTC):
            raise ConflictError("This upload link expired. Start a new upload attempt.")
        storage = get_storage(settings)
        try:
            assert isinstance(storage, R2Storage)
            metadata = await asyncio.to_thread(storage.object_metadata, attempt.staging_key)
        finally:
            storage.close()
        if (
            type(metadata.get("ContentLength")) is not int
            or metadata.get("ContentLength") != attempt.source_bytes
            or not isinstance(metadata.get("ETag"), str)
            or not metadata["ETag"]
        ):
            raise ConflictError("The stored upload does not match the selected file size.")
        attempt.source_etag = metadata["ETag"]
        attempt.status = "queued"
        await session.commit()
    return status_response(attempt)


@router.get("/manuscripts/uploads/{attempt_id}")
async def upload_status(
    attempt_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_upload_instructor)],
):
    return status_response(await owned_attempt(session, instructor.id, attempt_id))
