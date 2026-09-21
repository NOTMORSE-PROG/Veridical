"""Bounded recovery of exact-file hashes for retrievable legacy manuscripts.

BUG-235 deliberately keeps this work outside Alembic and HTTP requests.  A
legacy source may require a full durable-object read, so an operator chooses a
fixed id ceiling and this service processes one configured batch at a time.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import advisory_lock
from app.ingest.service import hash_file_sha256
from app.models.manuscript import Manuscript
from app.storage import Storage, get_storage


class ContentHashBackfillOutcome(StrEnum):
    would_update = "would_update"
    updated = "updated"
    already_filled = "already_filled"
    purged = "purged"
    missing_object = "missing_object"
    storage_failure = "storage_failure"
    database_failure = "database_failure"
    hash_conflict = "hash_conflict"
    invalid_source_ref = "invalid_source_ref"
    row_missing = "row_missing"
    update_conflict = "update_conflict"


@dataclass(frozen=True)
class ContentHashBackfillItem:
    manuscript_id: int
    outcome: ContentHashBackfillOutcome


@dataclass(frozen=True)
class ContentHashBackfillBatch:
    after_id: int
    through_id: int
    batch_size: int
    apply: bool
    items: tuple[ContentHashBackfillItem, ...]
    next_after_id: int
    retry_after_id: int | None
    more_eligible: bool

    @property
    def counts(self) -> dict[str, int]:
        counted = Counter(item.outcome.value for item in self.items)
        return {
            outcome.value: counted.get(outcome.value, 0) for outcome in ContentHashBackfillOutcome
        }

    @property
    def has_unrecovered(self) -> bool:
        incomplete = {
            ContentHashBackfillOutcome.missing_object,
            ContentHashBackfillOutcome.storage_failure,
            ContentHashBackfillOutcome.database_failure,
            ContentHashBackfillOutcome.hash_conflict,
            ContentHashBackfillOutcome.invalid_source_ref,
            ContentHashBackfillOutcome.row_missing,
            ContentHashBackfillOutcome.update_conflict,
        }
        return any(item.outcome in incomplete for item in self.items)


class InvalidContentHashSource(ValueError):
    """A legacy file reference resolves outside the configured data root."""


class ContentHashStorageInitializationError(RuntimeError):
    """Durable storage could not initialize; underlying detail stays private."""


def _source_path_and_key(settings: Settings, file_ref: str) -> tuple[Path, str]:
    root = settings.data_dir.resolve()
    candidate = Path(file_ref).resolve()
    if not candidate.is_relative_to(root):
        raise InvalidContentHashSource
    return candidate, candidate.relative_to(root).as_posix()


def _recover_and_hash(
    settings: Settings,
    storage: Storage,
    file_ref: str,
) -> tuple[Path | None, str]:
    """Hash a local source or one durable object without populating cache.

    The shared storage seam currently returns a complete object as bytes. A
    cold recovery therefore materializes one object in memory, updates the
    digest in configured slices, and discards it; it never writes a second
    worker-visible cache path or accumulates recovered manuscripts on disk.
    """
    local_path, storage_key = _source_path_and_key(settings, file_ref)
    if local_path.exists():
        return local_path, hash_file_sha256(local_path, settings.content_hash_read_chunk_bytes)

    payload = storage.get_bytes(storage_key)
    digest = hashlib.sha256()
    view = memoryview(payload)
    for offset in range(0, len(view), settings.content_hash_read_chunk_bytes):
        digest.update(view[offset : offset + settings.content_hash_read_chunk_bytes])
    return None, digest.hexdigest()


def _remove_recovered_cache(settings: Settings, path: Path | None) -> None:
    """Remove only a cache path proven to be under the configured data root.

    A purge can race between durable recovery and the conditional DB update.
    In that case the database correctly rejects the hash; this cleanup keeps
    the recovery process from recreating a purged local cache copy afterward.
    """
    if path is None:
        return
    root = settings.data_dir.resolve()
    candidate = path.resolve()
    if candidate.is_relative_to(root):
        candidate.unlink(missing_ok=True)


async def _store_hash_if_still_eligible(
    session: AsyncSession,
    manuscript_id: int,
    digest: str,
) -> ContentHashBackfillOutcome:
    result = await session.execute(
        update(Manuscript)
        .where(
            Manuscript.id == manuscript_id,
            Manuscript.content_hash.is_(None),
            Manuscript.purged_at.is_(None),
        )
        .values(content_hash=digest)
        .returning(Manuscript.id)
    )
    if result.scalar_one_or_none() is not None:
        await session.commit()
        return ContentHashBackfillOutcome.updated

    await session.rollback()
    return await _classify_current_state(
        session,
        manuscript_id,
        digest=digest,
        eligible_outcome=ContentHashBackfillOutcome.update_conflict,
    )


async def _classify_current_state(
    session: AsyncSession,
    manuscript_id: int,
    *,
    digest: str | None,
    eligible_outcome: ContentHashBackfillOutcome,
) -> ContentHashBackfillOutcome:
    state = (
        await session.execute(
            select(Manuscript.content_hash, Manuscript.purged_at).where(
                Manuscript.id == manuscript_id
            )
        )
    ).one_or_none()
    await session.rollback()
    if state is None:
        return ContentHashBackfillOutcome.row_missing
    if state.purged_at is not None:
        return ContentHashBackfillOutcome.purged
    if state.content_hash is not None:
        if digest is not None and state.content_hash != digest:
            return ContentHashBackfillOutcome.hash_conflict
        return ContentHashBackfillOutcome.already_filled
    return eligible_outcome


async def _recover_one_under_lock(
    session: AsyncSession,
    settings: Settings,
    storage: Storage,
    *,
    manuscript_id: int,
    file_ref: str,
    apply: bool,
) -> ContentHashBackfillOutcome:
    """Recover one identity while serialized with purge and peer workers."""
    async with advisory_lock(session, manuscript_id):
        # Selection happened before this potentially blocking lock. Re-read
        # authoritative state before touching either local or durable bytes.
        initial = await _classify_current_state(
            session,
            manuscript_id,
            digest=None,
            eligible_outcome=ContentHashBackfillOutcome.would_update,
        )
        if initial is not ContentHashBackfillOutcome.would_update:
            if initial in {
                ContentHashBackfillOutcome.purged,
                ContentHashBackfillOutcome.row_missing,
            }:
                await asyncio.to_thread(_remove_recovered_cache, settings, Path(file_ref))
            return initial

        recovery_failure: ContentHashBackfillOutcome | None = None
        try:
            local_path, digest = await asyncio.to_thread(
                _recover_and_hash, settings, storage, file_ref
            )
        except InvalidContentHashSource:
            recovery_failure = ContentHashBackfillOutcome.invalid_source_ref
        except FileNotFoundError:
            recovery_failure = ContentHashBackfillOutcome.missing_object
        except Exception:
            # SDK/filesystem exception text can contain paths, endpoints, or
            # credentials. The CLI reports a fixed outcome only.
            recovery_failure = ContentHashBackfillOutcome.storage_failure

        if recovery_failure is not None:
            outcome = await _classify_current_state(
                session,
                manuscript_id,
                digest=None,
                eligible_outcome=recovery_failure,
            )
            if outcome in {
                ContentHashBackfillOutcome.purged,
                ContentHashBackfillOutcome.row_missing,
            }:
                await asyncio.to_thread(_remove_recovered_cache, settings, Path(file_ref))
            return outcome

        if apply:
            outcome = await _store_hash_if_still_eligible(session, manuscript_id, digest)
        else:
            outcome = await _classify_current_state(
                session,
                manuscript_id,
                digest=digest,
                eligible_outcome=ContentHashBackfillOutcome.would_update,
            )
        if outcome in {
            ContentHashBackfillOutcome.purged,
            ContentHashBackfillOutcome.row_missing,
        }:
            await asyncio.to_thread(_remove_recovered_cache, settings, local_path)
        return outcome


async def backfill_content_hash_batch(
    session: AsyncSession,
    settings: Settings,
    *,
    through_id: int,
    after_id: int = 0,
    batch_size: int | None = None,
    apply: bool = False,
    storage: Storage | None = None,
) -> ContentHashBackfillBatch:
    """Inspect or recover one stable primary-key batch.

    `through_id` freezes the recovery population. `after_id` is an exclusive
    continuation cursor. No manuscript contents, filenames, paths, or hashes
    are returned to the caller.
    """
    size = settings.content_hash_backfill_batch_size if batch_size is None else batch_size
    if through_id < 1:
        raise ValueError("through_id must be at least 1")
    if after_id < 0:
        raise ValueError("after_id cannot be negative")
    if after_id >= through_id:
        raise ValueError("after_id must be lower than through_id")
    if size < 1:
        raise ValueError("batch_size must be at least 1")
    if size > settings.content_hash_backfill_max_batch_size:
        raise ValueError("batch_size exceeds content_hash_backfill_max_batch_size")

    selected = (
        await session.execute(
            select(Manuscript.id, Manuscript.file_ref)
            .where(
                Manuscript.id > after_id,
                Manuscript.id <= through_id,
                Manuscript.content_hash.is_(None),
                Manuscript.purged_at.is_(None),
            )
            .order_by(Manuscript.id)
            .limit(size + 1)
        )
    ).all()
    # Never hold a transaction open during local/R2 reads.
    await session.rollback()

    candidates = selected[:size]
    more_eligible = len(selected) > size
    resolved_storage = storage
    if candidates and resolved_storage is None:
        try:
            resolved_storage = get_storage(settings)
        except Exception as exc:
            raise ContentHashStorageInitializationError from exc
    items: list[ContentHashBackfillItem] = []
    next_after_id = after_id

    for manuscript_id, file_ref in candidates:
        assert resolved_storage is not None
        try:
            outcome = await _recover_one_under_lock(
                session,
                settings,
                resolved_storage,
                manuscript_id=manuscript_id,
                file_ref=file_ref,
                apply=apply,
            )
        except Exception:
            # Earlier rows commit independently. Preserve those outcomes and
            # leave the continuation cursor before this ambiguous row so a
            # retry re-reads its authoritative state instead of skipping it.
            # Never expose a driver error: it may carry credentials or SQL.
            with suppress(Exception):
                await session.rollback()
            items.append(
                ContentHashBackfillItem(
                    manuscript_id=manuscript_id,
                    outcome=ContentHashBackfillOutcome.database_failure,
                )
            )
            more_eligible = True
            break
        items.append(ContentHashBackfillItem(manuscript_id=manuscript_id, outcome=outcome))
        next_after_id = manuscript_id

    retry_after_id = (
        after_id
        if any(
            item.outcome
            in {
                ContentHashBackfillOutcome.missing_object,
                ContentHashBackfillOutcome.storage_failure,
                ContentHashBackfillOutcome.database_failure,
                ContentHashBackfillOutcome.hash_conflict,
                ContentHashBackfillOutcome.invalid_source_ref,
                ContentHashBackfillOutcome.row_missing,
                ContentHashBackfillOutcome.update_conflict,
            }
            for item in items
        )
        else None
    )
    return ContentHashBackfillBatch(
        after_id=after_id,
        through_id=through_id,
        batch_size=size,
        apply=apply,
        items=tuple(items),
        next_after_id=next_after_id,
        retry_after_id=retry_after_id,
        more_eligible=more_eligible,
    )
