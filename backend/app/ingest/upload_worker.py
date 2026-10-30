"""Durable direct-upload processing. PostgreSQL owns claims and terminal receipts."""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select, update

from app.config import get_settings
from app.db import get_session_factory
from app.errors import NotFoundError, VeridicalError, failure_summary
from app.groups.service import DEFAULT_GROUP_LABEL
from app.ingest.admission import preparation_scope
from app.ingest.direct_upload import relay_local_path
from app.ingest.receipts import ingestion_receipt
from app.ingest.service import PreparationHandedOff, StagedSource, _ingest_upload
from app.loop_probe import LoopLagProbe
from app.memory import return_freed_memory
from app.models.enums import IngestFailureReason, IngestStatus
from app.models.manuscript import Manuscript
from app.models.run import CheckRun
from app.models.upload_attempt import UploadAttempt
from app.pipeline.service import create_check_run
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


def _lease_end(settings):
    # The database clock, so instances whose clocks differ by a few seconds
    # still agree on when a lease has run out.
    return func.clock_timestamp() + timedelta(seconds=settings.direct_upload_lease_seconds)


async def _hold_lease(session, attempt_id, lease_id, settings) -> bool:
    """Extend the lease if this worker still owns the attempt.

    The row stays locked until the caller's transaction ends, so another
    worker cannot retire the attempt in between; the caller commits."""
    held = await session.scalar(
        update(UploadAttempt)
        .where(
            UploadAttempt.id == attempt_id,
            UploadAttempt.lease_id == lease_id,
            UploadAttempt.status == "processing",
        )
        .values(lease_until=_lease_end(settings))
        .returning(UploadAttempt.id)
    )
    return held is not None


async def _heartbeat(attempt_id, lease_id, settings, on_lost):
    """Keep the lease alive while this process prepares the upload.

    Beats keep a fixed cadence and each renewal is bounded by one beat, so a
    slow renewal does not push the next one back. A failed renewal is retried
    at the next beat instead of ending the heartbeat: the lease is short, and
    one database hiccup must not let a live preparation be retired."""
    loop = asyncio.get_running_loop()
    beat = settings.direct_upload_heartbeat_seconds
    due = loop.time()
    while True:
        # After a stall, beat at once rather than bursting the missed beats.
        due = max(due + beat, loop.time())
        await asyncio.sleep(due - loop.time())
        try:
            async with asyncio.timeout(beat):
                async with get_session_factory()() as session:
                    held = await _hold_lease(session, attempt_id, lease_id, settings)
                    await session.commit()
        except Exception as exc:  # noqa: BLE001 -- retried at the next beat
            _logger.warning("Upload lease renewal deferred (%s)", failure_summary(exc))
            continue
        if not held:
            # Retired by another worker after missed beats: whatever this
            # process still does for the attempt would contradict the
            # interrupted answer the instructor was already given.
            _logger.warning("Upload lease for attempt %s is no longer held", attempt_id)
            on_lost()
            return


async def _retire_interrupted(session):
    message = (
        "Document processing was interrupted. "
        "The stored original and any saved record are retained."
    )
    rows = await session.execute(
        update(UploadAttempt)
        .where(
            UploadAttempt.status == "processing",
            UploadAttempt.lease_until < func.clock_timestamp(),
        )
        .values(status="failed", error=message, lease_until=None)
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


CHECK_NOT_STARTED = "The check could not be started. Start it from the Review Desk."
# Used when the server could not even look: the check may exist anyway.
CHECK_MAY_NOT_HAVE_STARTED = (
    "The check may not have started. Look for it on the Review Desk before starting it."
)
RUBRIC_GONE = (
    "The required format chosen for this check was removed, so the check did not start. "
    "Start it from the Review Desk."
)


async def _started_check_id(session, manuscript_id, rubric_id):
    return await session.scalar(
        select(CheckRun.id)
        .where(CheckRun.manuscript_id == manuscript_id, CheckRun.rubric_id == rubric_id)
        .order_by(CheckRun.id.desc())
        .limit(1)
    )


async def _start_requested_check(session, instructor_id, manuscript_id, rubric_id):
    """V-090: start the check the instructor asked for with the same rules as
    a manual start. Nothing here can undo the finished upload: a refusal is
    kept for the tray, and after any other error a check that did commit is
    found and linked instead of being reported as not started."""
    try:
        check_run = await create_check_run(session, instructor_id, manuscript_id, rubric_id)
    except NotFoundError:
        await session.rollback()
        return None, RUBRIC_GONE
    except VeridicalError as exc:
        await session.rollback()
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 -- any other failure keeps the upload done
        await session.rollback()
        _logger.warning("Requested check start did not finish (%s)", failure_summary(exc))
        try:
            started = await _started_check_id(session, manuscript_id, rubric_id)
        except Exception:  # noqa: BLE001 -- the link is best effort
            await session.rollback()
            return None, CHECK_MAY_NOT_HAVE_STARTED
        return started, None if started is not None else CHECK_NOT_STARTED
    return check_run.id, None


async def _finish_done(session, attempt_id, lease_id, receipt, check_run_id, check_start_error):
    await session.execute(
        update(UploadAttempt)
        .where(
            UploadAttempt.id == attempt_id,
            UploadAttempt.lease_id == lease_id,
            UploadAttempt.status == "processing",
        )
        .values(
            status="done",
            response=receipt,
            error=None,
            lease_until=None,
            check_run_id=check_run_id,
            check_start_error=check_start_error,
        )
    )
    await session.commit()


async def process_next_upload(settings, wake_pipeline=None):
    with preparation_scope(settings) as lease:
        processed = await _process_next_upload(settings, lease, wake_pipeline)
    if processed:
        # A preparation's records are all freed by now; hand the pages back so
        # the next job starts from the idle footprint, not this one's peak.
        # Off the event loop, and without a full collection (reference
        # counting has freed them; a collection would hold the interpreter
        # lock for most of a second on a 0.1-CPU host).
        await asyncio.to_thread(return_freed_memory, collect=False)
    return processed


async def _process_next_upload(settings, lease, wake_pipeline=None):
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
        attempt.lease_until = _lease_end(settings)
        await session.commit()
        attempt_id, lease_id = attempt.id, attempt.lease_id
        instructor_id, start_rubric_id = attempt.instructor_id, attempt.start_rubric_id
        preparing = asyncio.current_task()
        handed_off = asyncio.Event()
        publishing = False

        def stop_preparing():
            # The attempt was retired while this process still worked on it.
            # Stop, unless only the fenced final write is left.
            handed_off.set()
            if not publishing:
                preparing.cancel()

        async def still_owned(owner_session):
            # Runs inside the transaction that marks the manuscript ready, so
            # a retired attempt can never end with a ready manuscript.
            if not await _hold_lease(owner_session, attempt_id, lease_id, settings):
                raise PreparationHandedOff

        heartbeat = asyncio.create_task(_heartbeat(attempt_id, lease_id, settings, stop_preparing))
        probe = LoopLagProbe(settings.event_loop_probe_seconds).start()
        started = asyncio.get_running_loop().time()
        receipt = manuscript_id = outcome = None
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
                before_finalize=still_owned,
            )
            receipt = ingestion_receipt(*values).model_dump(mode="json")
            manuscript_id = values[0].id
            check_run_id = check_start_error = None
            if start_rubric_id is not None:
                # The attempt row stays locked until the check start commits,
                # so the check never starts for an attempt already retired.
                await still_owned(session)
                check_run_id, check_start_error = await _start_requested_check(
                    session, instructor_id, manuscript_id, start_rubric_id
                )
                outcome = (check_run_id, check_start_error)
                if check_run_id is not None and wake_pipeline is not None:
                    # Same effect as a manual start: the scheduler looks now
                    # instead of at its next poll (or never, without polling).
                    wake_pipeline()
            publishing = True
            await _finish_done(
                session, attempt_id, lease_id, receipt, check_run_id, check_start_error
            )
        except PreparationHandedOff:
            await session.rollback()
            _logger.warning("Upload attempt %s was retired before it finished", attempt_id)
        except asyncio.CancelledError:
            # Only the heartbeat's own stop is absorbed; a shutdown is not.
            if not handed_off.is_set() or preparing.uncancel() > 0:
                raise
            with suppress(Exception):
                await session.rollback()
            _logger.warning("Upload attempt %s was retired before it finished", attempt_id)
        except Exception as exc:
            await session.rollback()
            if receipt is not None:
                publishing = True
                # The manuscript is ready; a later failure (recording the
                # outcome) must not report a finished upload as failed.
                _logger.warning("Upload finish was retried (%s)", failure_summary(exc))
                check_run_id = check_start_error = None
                if outcome is not None:
                    # Keep the outcome already known instead of guessing again.
                    check_run_id, check_start_error = outcome
                elif start_rubric_id is not None:
                    try:
                        check_run_id = await _started_check_id(
                            session, manuscript_id, start_rubric_id
                        )
                        if check_run_id is None:
                            check_start_error = CHECK_NOT_STARTED
                    except Exception:  # noqa: BLE001 -- the link is best effort
                        await session.rollback()
                        check_start_error = CHECK_MAY_NOT_HAVE_STARTED
                await _finish_done(
                    session, attempt_id, lease_id, receipt, check_run_id, check_start_error
                )
                return True
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
            # Wait without absorbing a cancellation aimed at this task.
            await asyncio.wait([heartbeat])
            worst = await probe.stop()
            _logger.info(
                "Upload preparation %s ended after %.1f s; worst event-loop delay %.2f s",
                attempt_id,
                asyncio.get_running_loop().time() - started,
                worst,
            )
            # A relay copy that was not consumed would only fill the disk.
            relay_local_path(settings, attempt_id).unlink(missing_ok=True)
        return True


async def upload_worker_loop(wake_pipeline=None):
    while True:
        settings = get_settings()
        if settings.direct_manuscript_upload_enabled:
            try:
                await process_next_upload(settings, wake_pipeline)
            except Exception as exc:
                _logger.warning("Direct upload scheduler unavailable (%s)", failure_summary(exc))
        await asyncio.sleep(settings.direct_upload_poll_seconds)
