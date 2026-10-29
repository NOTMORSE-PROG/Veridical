"""Durable direct-upload processing. PostgreSQL owns claims and terminal receipts."""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update

from app.config import get_settings
from app.db import get_session_factory
from app.errors import VeridicalError, failure_summary
from app.groups.service import DEFAULT_GROUP_LABEL
from app.ingest.admission import preparation_scope
from app.ingest.direct_upload import relay_local_path
from app.ingest.receipts import ingestion_receipt
from app.ingest.service import StagedSource, _ingest_upload
from app.models.enums import IngestFailureReason, IngestStatus
from app.models.manuscript import Manuscript
from app.models.upload_attempt import UploadAttempt
from app.storage import get_storage

_logger = logging.getLogger(__name__)


async def _cleanup_staging(session, settings):
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.direct_upload_staging_retention_seconds)
    rows = list(
        (
            await session.scalars(
                select(UploadAttempt)
                .where(
                    UploadAttempt.expires_at < cutoff,
                    UploadAttempt.staging_deleted_at.is_(None),
                    UploadAttempt.status.in_(("pending", "expired", "done")),
                )
                .order_by(UploadAttempt.expires_at)
                .with_for_update(skip_locked=True)
                .limit(settings.direct_upload_cleanup_batch_size)
            )
        ).all()
    )
    if not rows:
        return
    storage = get_storage(settings)
    try:
        for attempt in rows:
            if attempt.multipart_upload_id and attempt.status in ("pending", "expired"):
                await asyncio.to_thread(
                    storage.abort_multipart, attempt.staging_key, attempt.multipart_upload_id
                )
            await asyncio.to_thread(storage.delete, attempt.staging_key)
            relay_local_path(settings, attempt.id).unlink(missing_ok=True)
            attempt.staging_deleted_at = datetime.now(UTC)
            if attempt.status == "pending":
                attempt.status, attempt.error = (
                    "expired",
                    "This upload link expired. Start a new upload attempt.",
                )
        await session.commit()
    finally:
        storage.close()


async def _heartbeat(attempt_id, lease_id, settings):
    while True:
        await asyncio.sleep(settings.direct_upload_lease_seconds / 4)
        async with get_session_factory()() as session:
            await session.execute(
                update(UploadAttempt)
                .where(
                    UploadAttempt.id == attempt_id,
                    UploadAttempt.lease_id == lease_id,
                    UploadAttempt.status == "processing",
                )
                .values(
                    lease_until=datetime.now(UTC)
                    + timedelta(seconds=settings.direct_upload_lease_seconds)
                )
            )
            await session.commit()


async def _retire_interrupted(session):
    message = (
        "Document processing was interrupted. "
        "The stored original and any saved record are retained."
    )
    rows = await session.execute(
        update(UploadAttempt)
        .where(UploadAttempt.status == "processing", UploadAttempt.lease_until < datetime.now(UTC))
        .values(status="failed", error=message)
        .returning(UploadAttempt.manuscript_id)
    )
    ids = [value for value in rows.scalars() if value is not None]
    if ids:
        await session.execute(
            update(Manuscript)
            .where(
                Manuscript.id.in_(ids),
                Manuscript.ingest_status.in_((IngestStatus.pending, IngestStatus.processing)),
            )
            .values(
                ingest_status=IngestStatus.failed,
                ingest_failure_reason=IngestFailureReason.extraction_failed,
                ingest_failure_detail=message,
            )
        )
    await session.commit()


async def process_next_upload(settings):
    with preparation_scope(settings) as lease:
        return await _process_next_upload(settings, lease)


async def _process_next_upload(settings, lease):
    async with get_session_factory()() as session:
        await _cleanup_staging(session, settings)
        await _retire_interrupted(session)
        attempt = await session.scalar(
            select(UploadAttempt)
            .where(UploadAttempt.status == "queued")
            .order_by(UploadAttempt.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if attempt is None:
            return False
        attempt.status, attempt.lease_id = "processing", str(uuid4())
        attempt.lease_until = datetime.now(UTC) + timedelta(
            seconds=settings.direct_upload_lease_seconds
        )
        await session.commit()
        attempt_id, lease_id = attempt.id, attempt.lease_id
        heartbeat = asyncio.create_task(_heartbeat(attempt_id, lease_id, settings))
        try:
            # The staged object is copied inside storage for the durable
            # original; the bytes reach this host once at most (none when the
            # relay's own copy is still here), hashed as they are written.
            staged = StagedSource(
                key=attempt.staging_key,
                etag=attempt.source_etag,
                size=attempt.source_bytes,
                local=relay_local_path(settings, attempt_id),
            )
            large_settings = settings.model_copy(
                update={"max_upload_mb": settings.direct_manuscript_upload_max_mb}
            )
            values = await _ingest_upload(
                session,
                None,
                attempt.filename,
                DEFAULT_GROUP_LABEL,
                large_settings,
                instructor_id=attempt.instructor_id,
                preparation_lease=lease,
                upload_attempt=attempt,
                staged=staged,
            )
            receipt = ingestion_receipt(*values).model_dump(mode="json")
            await session.execute(
                update(UploadAttempt)
                .where(
                    UploadAttempt.id == attempt_id,
                    UploadAttempt.lease_id == lease_id,
                    UploadAttempt.status == "processing",
                )
                .values(status="done", response=receipt, error=None, lease_until=None)
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            # Domain messages are vetted product templates. Never persist raw
            # SDK/database exceptions, source keys, credentials or document text.
            error = (
                str(exc)
                if isinstance(exc, VeridicalError)
                else (
                    "Document processing could not finish. Check Review Desk for any saved record."
                )
            )
            await session.execute(
                update(UploadAttempt)
                .where(
                    UploadAttempt.id == attempt_id,
                    UploadAttempt.lease_id == lease_id,
                    UploadAttempt.status == "processing",
                )
                .values(status="failed", error=error, lease_until=None)
            )
            linked_id = await session.scalar(
                select(UploadAttempt.manuscript_id).where(
                    UploadAttempt.id == attempt_id, UploadAttempt.lease_id == lease_id
                )
            )
            if linked_id is not None:
                await session.execute(
                    update(Manuscript)
                    .where(
                        Manuscript.id == linked_id,
                        Manuscript.ingest_status.in_(
                            (IngestStatus.pending, IngestStatus.processing)
                        ),
                        Manuscript.purged_at.is_(None),
                    )
                    .values(
                        ingest_status=IngestStatus.failed,
                        ingest_failure_reason=IngestFailureReason.extraction_failed,
                        ingest_failure_detail=error,
                    )
                )
            await session.commit()
            _logger.warning("Direct upload processing did not complete (%s)", failure_summary(exc))
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await heartbeat
            # A relay copy that was not consumed would only fill the disk.
            relay_local_path(settings, attempt_id).unlink(missing_ok=True)
        return True


async def upload_worker_loop():
    while True:
        settings = get_settings()
        if settings.direct_manuscript_upload_enabled:
            try:
                await process_next_upload(settings)
            except Exception as exc:
                _logger.warning("Direct upload scheduler unavailable (%s)", failure_summary(exc))
        await asyncio.sleep(settings.direct_upload_poll_seconds)
