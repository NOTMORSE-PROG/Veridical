"""Durable authenticated browser-to-R2 manuscript upload lifecycle (F1/F9)."""

import asyncio
import math
import os
import threading
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated
from uuid import UUID, uuid4

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, Header, Request, UploadFile
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
from app.ingest.source_digest import HashingWriter, record_file_sha256
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.upload_attempt import UploadAttempt
from app.ratelimit import enforce_action_rate_limit
from app.storage import R2Storage, get_storage, namespaced_storage_key

router = APIRouter(tags=["ingestion"])
_relay_gate = threading.BoundedSemaphore(get_settings().direct_upload_relay_transfers)
# Transfers the browser declares it can perform; older clients send nothing
# and keep the original single-request contract.
TRANSPORTS_HEADER = "X-Veridical-Upload-Transports"
PARTS, STREAM = "parts", "stream"


def relay_local_path(settings, attempt_id: str) -> Path:
    """This instance's copy of a relayed upload, consumed by the worker."""
    return settings.data_dir / settings.direct_upload_staging_subdir / attempt_id


def _capabilities(header: str | None) -> set[str]:
    return {item.strip() for item in (header or "").split(",") if item.strip()}


def _part_layout(settings, size: int) -> list[tuple[int, int]]:
    """(number, length) for every part. R2 needs equal sizes except the last."""
    part_bytes = settings.direct_upload_part_mb * 1024 * 1024
    count = math.ceil(size / part_bytes)
    return [(n, min(part_bytes, size - (n - 1) * part_bytes)) for n in range(1, count + 1)]


def _signed_parts(storage, attempt, settings, ttl: int) -> list[dict]:
    try:
        stored = {
            part["PartNumber"]: part["Size"]
            for part in storage.list_parts(attempt.staging_key, attempt.multipart_upload_id)
        }
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "NoSuchUpload":
            raise ConflictError("This upload link expired. Start a new upload attempt.") from None
        raise
    parts = []
    for number, length in _part_layout(settings, attempt.source_bytes):
        item = {"number": number, "size": length, "done": stored.get(number) == length}
        if not item["done"]:
            item["url"] = storage.presign_part(
                attempt.staging_key, attempt.multipart_upload_id, number, length, ttl
            )
        parts.append(item)
    return parts


def _verified_parts(storage, attempt, settings) -> list[dict]:
    expected = _part_layout(settings, attempt.source_bytes)
    stored = storage.list_parts(attempt.staging_key, attempt.multipart_upload_id)
    if [(p["PartNumber"], p["Size"]) for p in stored] != expected:
        raise ConflictError(
            "Some parts of the file have not arrived yet. Retry the upload to send them."
        )
    return stored


async def _drain(task: asyncio.Task) -> None:
    # A cancelled request cannot stop a storage call already running in a
    # thread. Keep its buffers, file and client alive until it returns.
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break


async def _stream_relay(request: Request, key: str, size: int, settings):
    """Store the raw request body in R2 part by part while it is still
    arriving, and keep one local copy for the worker. The response can go out
    seconds after the last byte instead of after a second full transfer."""
    part_bytes = settings.storage_transfer_chunk_bytes
    storage = get_storage(settings)
    assert isinstance(storage, R2Storage)
    directory = settings.data_dir / settings.direct_upload_staging_subdir
    directory.mkdir(parents=True, exist_ok=True)
    handle = NamedTemporaryFile(dir=directory, delete=False)  # noqa: SIM115 -- closed on every path below
    partial = Path(handle.name)
    writer = HashingWriter(handle)
    queue: asyncio.Queue = asyncio.Queue(maxsize=settings.direct_upload_relay_queued_parts)
    parts: list[dict] = []
    upload_id = None
    consumer = None

    def store(number, data, upload):
        writer.write(data)
        parts.append({"PartNumber": number, "ETag": storage.upload_part(key, upload, number, data)})

    async def consume(upload):
        while (item := await queue.get()) is not None:
            await asyncio.to_thread(store, *item, upload)

    async def put(item):
        waiting = asyncio.ensure_future(queue.put(item))
        await asyncio.wait({waiting, consumer}, return_when=asyncio.FIRST_COMPLETED)
        queued = waiting.done() and not waiting.cancelled()
        if not queued:
            waiting.cancel()
        # The consumer only stops on its own when a part failed; the final
        # None is the one item it is allowed to finish on.
        if consumer.done() and (item is not None or not queued):
            consumer.result()
            raise OSError("Part storage stopped early.")

    try:
        upload_id = await asyncio.to_thread(storage.create_multipart, key)
        consumer = asyncio.create_task(consume(upload_id))
        buffer, number, received = bytearray(), 0, 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > size:
                raise ConflictError("The transferred file does not match the selected file size.")
            buffer += chunk
            while len(buffer) >= part_bytes:
                number += 1
                await put((number, bytes(buffer[:part_bytes])))
                del buffer[:part_bytes]
        if received != size:
            raise ConflictError("The transfer ended before the whole file arrived. Retry it.")
        if buffer:
            number += 1
            await put((number, bytes(buffer)))
        await put(None)
        await consumer
        handle.close()
        await asyncio.to_thread(storage.complete_multipart, key, upload_id, parts)
        metadata = await asyncio.to_thread(storage.object_metadata, key)
        return metadata, writer.hexdigest(), partial
    except BaseException:
        if consumer is not None:
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(None)
            await _drain(consumer)
        if upload_id is not None:
            # Storage also expires unfinished uploads; a failed abort is not fatal.
            with suppress(Exception):
                await asyncio.to_thread(storage.abort_multipart, key, upload_id)
        handle.close()
        partial.unlink(missing_ok=True)
        raise
    finally:
        storage.close()


class _Tee:
    """Read-through view of the upload that also writes every byte to this
    instance's copy, so the worker does not download the file again."""

    def __init__(self, source, sink):
        self._source, self._sink = source, sink

    def read(self, size=-1):
        chunk = self._source.read(size)
        if chunk:
            self._sink.write(chunk)
        return chunk

    def readable(self):
        return True

    def seekable(self):
        # Storage then reads each part exactly once, in order.
        return False


def _store_relay(storage, source, key, directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=directory, delete=False) as handle:
        partial = Path(handle.name)
        try:
            writer = HashingWriter(handle)
            storage.upload_stream(_Tee(source, writer), key)
            handle.close()
            return storage.object_metadata(key), writer.hexdigest(), partial
        except BaseException:
            handle.close()
            partial.unlink(missing_ok=True)
            raise


async def _relay_transfer(storage, source, key, directory: Path):
    work = asyncio.create_task(asyncio.to_thread(_store_relay, storage, source, key, directory))
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
        if work.done() and not work.cancelled() and work.exception() is None:
            work.result()[2].unlink(missing_ok=True)
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
    transports: Annotated[str | None, Header(alias=TRANSPORTS_HEADER)] = None,
):
    settings = enabled_settings()
    capabilities = _capabilities(transports)
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
            staging_key=namespaced_storage_key(
                settings, f"{settings.direct_upload_staging_subdir}/{uuid4()}"
            ),
            status="pending",
            expires_at=now + timedelta(seconds=settings.direct_upload_url_ttl_seconds),
        )
        if attempt.upload_transport == "r2" and PARTS in capabilities:
            # Created under the admission lock, so a repeated begin for this
            # attempt finds the same upload instead of starting another.
            storage = get_storage(settings)
            try:
                assert isinstance(storage, R2Storage)
                attempt.multipart_upload_id = await asyncio.to_thread(
                    storage.create_multipart, attempt.staging_key
                )
            finally:
                storage.close()
        session.add(attempt)
    await session.commit()
    response = status_response(attempt)
    if attempt.status == "pending":
        ttl = int((attempt.expires_at - now).total_seconds())
        if ttl <= 0:
            raise ConflictError("This upload link expired. Start a new upload attempt.")
        if attempt.upload_transport == "r2" and attempt.multipart_upload_id:
            storage = get_storage(settings)
            try:
                assert isinstance(storage, R2Storage)
                response["parts"] = await asyncio.to_thread(
                    _signed_parts, storage, attempt, settings, ttl
                )
            finally:
                storage.close()
            response["part_concurrency"] = settings.direct_upload_part_concurrency
            response["part_retries"] = settings.direct_upload_part_retries
        elif attempt.upload_transport == "r2":
            storage = get_storage(settings)
            try:
                assert isinstance(storage, R2Storage)
                response["upload_url"] = await asyncio.to_thread(
                    storage.presign_upload, attempt.staging_key, attempt.source_bytes, ttl
                )
                response["upload_headers"] = {"Content-Type": "application/octet-stream"}
            finally:
                storage.close()
        elif STREAM in capabilities:
            response["relay_mode"] = STREAM
    return response


@router.put("/manuscripts/uploads/{attempt_id}/body")
async def relay_upload_body(
    attempt_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_upload_instructor)],
):
    """Raw-body relay. Nothing is read before sign-in, ownership and the
    declared size are checked, and no transaction stays open while it streams."""
    settings = enabled_settings()
    if settings.direct_upload_transport != "relay":
        raise ConflictError("This upload uses direct storage transfer.")
    attempt = await owned_attempt(session, instructor.id, attempt_id, lock=True)
    if attempt.upload_transport != "relay":
        raise ConflictError("This attempt uses direct storage transfer.")
    if attempt.status != "pending":
        response = status_response(attempt)
        await session.commit()
        return response
    if attempt.expires_at <= datetime.now(UTC):
        raise ConflictError("This upload link expired. Start a new upload attempt.")
    declared = request.headers.get("content-length", "")
    if not declared.isdigit() or int(declared) != attempt.source_bytes:
        raise ConflictError("The transferred file does not match the selected file size.")
    key, size = attempt.staging_key, attempt.source_bytes
    await session.commit()
    if not _relay_gate.acquire(blocking=False):
        raise DocumentPreparationBusyError(
            "Another file transfer is running. Retry this upload shortly."
        )
    try:
        metadata, digest, partial = await _stream_relay(request, key, size, settings)
        try:
            attempt = await owned_attempt(session, instructor.id, attempt_id, lock=True)
            if attempt.status != "pending":
                response = status_response(attempt)
                await session.commit()
                return response
            if (
                type(metadata.get("ContentLength")) is not int
                or metadata["ContentLength"] != size
                or not isinstance(metadata.get("ETag"), str)
                or not metadata["ETag"]
            ):
                raise ConflictError("The stored upload could not be confirmed.")
            local = relay_local_path(settings, attempt_id)
            os.replace(partial, local)
            record_file_sha256(local, digest, settings)
            attempt.source_etag, attempt.status = metadata["ETag"], "queued"
            await session.commit()
            return status_response(attempt)
        finally:
            partial.unlink(missing_ok=True)
    finally:
        _relay_gate.release()


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
    key, size = attempt.staging_key, attempt.source_bytes
    # The database closes sessions left idle in a transaction; never hold one
    # open while storage works.
    await session.commit()
    if not _relay_gate.acquire(blocking=False):
        raise DocumentPreparationBusyError(
            "Another file transfer is running. Retry this upload shortly."
        )
    storage = None
    try:
        storage = get_storage(settings)
        assert isinstance(storage, R2Storage)
        await file.seek(0)
        local = relay_local_path(settings, attempt_id)
        metadata, digest, partial = await _relay_transfer(storage, file.file, key, local.parent)
        try:
            attempt = await owned_attempt(session, instructor.id, attempt_id, lock=True)
            if attempt.status != "pending":
                response = status_response(attempt)
                await session.commit()
                return response
            if (
                type(metadata.get("ContentLength")) is not int
                or metadata["ContentLength"] != size
                or not isinstance(metadata.get("ETag"), str)
                or not metadata["ETag"]
            ):
                raise ConflictError("The stored upload could not be confirmed.")
            os.replace(partial, local)
            record_file_sha256(local, digest, settings)
            attempt.source_etag, attempt.status = metadata["ETag"], "queued"
            await session.commit()
            return status_response(attempt)
        finally:
            partial.unlink(missing_ok=True)
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
            if attempt.multipart_upload_id:
                await asyncio.to_thread(_complete_parts, storage, attempt, settings)
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


def _complete_parts(storage, attempt, settings) -> None:
    # Only parts storage confirms are used; the client sends no part list.
    try:
        parts = _verified_parts(storage, attempt, settings)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "NoSuchUpload":
            # Already completed by an earlier request whose reply was lost;
            # the object metadata check that follows decides.
            return
        raise
    storage.complete_multipart(attempt.staging_key, attempt.multipart_upload_id, parts)


@router.get("/manuscripts/uploads/{attempt_id}")
async def upload_status(
    attempt_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_upload_instructor)],
):
    return status_response(await owned_attempt(session, instructor.id, attempt_id))
