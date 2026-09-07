"""Originality/reuse (F7) archive: list processed manuscripts' embedding
state and purge one on instructor request (V-042, screen 4t).

Purge deletes the F7 embedding rows (`ManuscriptArchive`,
`ManuscriptChapterArchive`) and the stored raw/upload files -- it does NOT
touch the `Manuscript` row, its `CheckRun` history, or any `ReadinessReport`
decision (charter: a decision, once made, is never silently unmade or
erased -- only explicitly reopened, V-038). No auto-deletion exists or is
proposed anywhere in this module: FEATURES.md §10 Q4 (retention policy) is
still an open adviser question, so purge is instructor-triggered only.
"""

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.archive.schemas import ArchiveItemOut, PaginatedArchive, PurgeOut
from app.audit.service import write_audit_event
from app.config import Settings
from app.db import advisory_lock
from app.errors import ConflictError, NotFoundError
from app.ingest.service import raw_store_path
from app.models.manuscript import (
    Manuscript,
    ManuscriptArchive,
    ManuscriptChapterArchive,
    ManuscriptPassageArchive,
)
from app.models.run import CheckRun
from app.storage import get_storage, storage_key_for


async def list_archive(
    session: AsyncSession, instructor_id: int, *, page: int = 1, page_size: int = 50
) -> PaginatedArchive:
    total = await session.scalar(
        select(func.count())
        .select_from(Manuscript)
        .where(Manuscript.instructor_id == instructor_id)
    )
    manuscripts = (
        await session.scalars(
            select(Manuscript)
            .where(Manuscript.instructor_id == instructor_id)
            .order_by(Manuscript.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    manuscript_ids = [m.id for m in manuscripts]
    archived_ids: set[int] = set()
    latest_status_by_manuscript: dict[int, str] = {}
    if manuscript_ids:
        archived_ids = set(
            (
                await session.scalars(
                    select(ManuscriptArchive.manuscript_id).where(
                        ManuscriptArchive.manuscript_id.in_(manuscript_ids)
                    )
                )
            ).all()
        )
        runs = (
            await session.scalars(
                select(CheckRun)
                .where(CheckRun.manuscript_id.in_(manuscript_ids))
                .order_by(CheckRun.manuscript_id, CheckRun.created_at.desc())
            )
        ).all()
        for run in runs:
            latest_status_by_manuscript.setdefault(run.manuscript_id, run.status.value)

    return PaginatedArchive(
        items=[
            ArchiveItemOut(
                manuscript_id=m.id,
                group_label=m.group_label,
                original_filename=m.original_filename,
                created_at=m.created_at,
                ingest_status=m.ingest_status.value,
                dismissed_at=m.dismissed_at,
                latest_check_run_status=latest_status_by_manuscript.get(m.id),
                has_archive=m.id in archived_ids,
                purged_at=m.purged_at,
            )
            for m in manuscripts
        ],
        total=total or 0,
        page=page,
        page_size=page_size,
    )


async def delete_archive_rows(session: AsyncSession, manuscript_id: int) -> bool:
    """The F7 embedding rows only (`ManuscriptArchive`,
    `ManuscriptChapterArchive`, `ManuscriptPassageArchive`) -- withdraws a
    manuscript from the shared cross-instructor originality corpus without
    touching its stored file, its `Manuscript` row, or `purged_at` (that
    full, irreversible, instructor-triggered action is `purge_manuscript`
    below, which calls this as its first step). Split out (BUG-178) so a
    SYSTEM-initiated withdrawal -- a cancelled check whose reuse write-back
    already landed -- can remove the same three rows without also deleting
    the instructor's own upload out from under them, which `purge_manuscript`
    would do and which cancelling a check never asks for.

    Returns whether anything was actually removed (`backend-critic`
    finding, BUG-178 review): a caller that logs an audit event only on a
    REAL removal must not claim one happened when the tables were already
    empty for this manuscript -- an audit row asserting something that
    didn't happen is exactly the class of dishonesty this codebase treats
    as a defect (`reuse/service.py`'s own BUG-151 precedent, inverted)."""
    chapter_result = await session.execute(
        delete(ManuscriptChapterArchive).where(
            ManuscriptChapterArchive.manuscript_id == manuscript_id
        )
    )
    archive_result = await session.execute(
        delete(ManuscriptArchive).where(ManuscriptArchive.manuscript_id == manuscript_id)
    )
    # BUG-123: F7.4's passage-level archive (V-072) stores real body text
    # per passage (`text`/`context_text`), not just a vector -- this is
    # exactly the "comparison data used to check future manuscripts against
    # it" the purge confirmation modal promises is deleted. It was added
    # after this function shipped and purge was never updated to cover it,
    # so purged text kept matching and kept surfacing in other instructors'
    # flags. Same delete-by-manuscript_id pattern as the two tables above.
    passage_result = await session.execute(
        delete(ManuscriptPassageArchive).where(
            ManuscriptPassageArchive.manuscript_id == manuscript_id
        )
    )
    return bool(chapter_result.rowcount or archive_result.rowcount or passage_result.rowcount)


async def withdraw_manuscript_if_orphaned(
    session: AsyncSession,
    *,
    manuscript_id: int,
    check_run_id: int,
    reached_integrity: bool,
) -> bool:
    """BUG-178: called from BOTH cancellation-finalization paths
    (`pipeline.machine._finish_cancel_if_requested`, for a run stopped
    mid-flight by its own worker; `pipeline.service.cancel_check_run`'s
    `stops_without_worker` branch, for a queued/blocked run finalized
    inline) when a check_run is finalized as cancelled and MAY already
    have written this manuscript into the shared cross-instructor
    originality corpus -- F7's reuse write-back happens INSIDE the
    integrity stage, before that stage's own boundary check. One shared
    function, not two divergent copies: `backend-critic` found the
    ORIGINAL version of this fix only guarded the worker path, leaving the
    queued/blocked path unprotected -- currently safe only because reuse
    is the last integrity sub-check and neither it nor aggregation makes
    an LLM call today (so nothing between the write-back and `done` can
    currently block), an invariant a future change could silently break.

    `reached_integrity` is a cheap pre-filter (skip the lock/query
    entirely when the run provably never reached a stage where the
    write-back could happen) -- callers pass whether their own notion of
    "current stage" is `integrity` or later.

    Provably safe, not just probably safe (`backend-critic` finding: the
    original version could destroy a DIFFERENT, concurrently-running
    check_run's legitimate write-back for the same manuscript -- READ
    COMMITTED gives no protection here, and nothing ties an archive row
    back to the check_run that wrote it). Withdraws ONLY when this
    manuscript has never had any OTHER check_run at all -- if a second
    check_run has ever existed, whatever is currently archived might be
    ITS legitimate data (`ManuscriptArchive.manuscript_id` is unique and
    every write-back REPLACES the row, `reuse/store.py`'s own "idempotent
    upsert" docstring), and there is no way to attribute existing rows to
    a specific check_run after the fact. Skipping in that case trades a
    small, disclosed under-cleaning risk (a genuinely orphaned entry can
    occasionally survive until a later re-check's own write overwrites
    it) for a guarantee this never destroys another run's real data --
    ground rule 1's precision-over-recall bias, applied to data retention
    instead of a flag.

    Serialized via `app.db.advisory_lock` (see its own docstring for why
    this is a DEDICATED connection, not a lock taken directly on `session`
    -- `backend-critic` found the first version of this fix broke under
    real connection-pool contention) keyed on `manuscript_id`.
    `run_originality_reuse_check`'s own write-back holds the SAME lock
    around its write (`app/checks/reuse/service.py`), and `purge_manuscript`
    below holds it too, so none of the three can ever interleave with
    another: whichever gets there first completes fully before the next
    proceeds, so a withdrawal here always re-evaluates against the
    now-current sibling count, never stale info."""
    if not reached_integrity:
        return False
    async with advisory_lock(session, manuscript_id):
        other_run_count = await session.scalar(
            select(func.count())
            .select_from(CheckRun)
            .where(CheckRun.manuscript_id == manuscript_id, CheckRun.id != check_run_id)
        )
        if other_run_count:
            return False
        return await delete_archive_rows(session, manuscript_id)


async def purge_manuscript(
    session: AsyncSession, instructor_id: int, manuscript_id: int, settings: Settings
) -> PurgeOut:
    manuscript = await session.get(Manuscript, manuscript_id)
    if manuscript is None or manuscript.instructor_id != instructor_id:
        raise NotFoundError(f"Manuscript {manuscript_id} not found.")
    if manuscript.purged_at is not None:
        raise ConflictError("This manuscript's archive has already been purged.")

    # BUG-178 (`backend-critic` finding): this call used to race a
    # concurrent check_run's own reuse write-back for the SAME manuscript
    # (a real, reachable case -- no unique constraint stops a second
    # check_run existing for one manuscript, and it's often the SAME
    # instructor's own re-run) with no protection at all -- an in-flight
    # write could land AFTER this delete, resurrecting archive rows the
    # instructor had just explicitly, "irreversibly" purged, with the next
    # visit to the archive screen showing it as archived again and no
    # explanation. Same lock the withdrawal path and the write-back itself
    # both hold (`app.db.advisory_lock`), so none of the three can ever
    # interleave with another. Unlike the automatic withdrawal path, purge
    # has no "other check_run" guard -- an instructor's explicit purge
    # request is unconditional by design; the lock only ensures it can't
    # be raced by a write in flight at this exact moment.
    async with advisory_lock(session, manuscript_id):
        await delete_archive_rows(session, manuscript_id)

    # `file_ref` is already an absolute path (ingest/service.py writes it as
    # `str(data_dir / "uploads" / ...)`) -- used as-is, never re-joined.
    # Missing files (already gone, or an image-only manuscript that never
    # wrote a raw store) are a normal state, not an error.
    #
    # BUG-138: purge must also remove the DURABLE copy, or "purged" is a lie
    # once R2 exists -- the bytes would outlive their own deletion forever
    # instead of just until the next ephemeral-disk wipe.
    storage = get_storage(settings)
    for path in (
        Path(manuscript.file_ref) if manuscript.file_ref.strip() else None,
        raw_store_path(settings, manuscript_id),
    ):
        if path is None:
            continue
        storage.delete(storage_key_for(settings, str(path)))
        path.unlink(missing_ok=True)

    manuscript.purged_at = datetime.now(UTC)
    await write_audit_event(
        session,
        event_type="manuscript_purged",
        check_run_id=None,
        payload={"manuscript_id": manuscript_id, "group_label": manuscript.group_label},
    )
    await session.commit()
    return PurgeOut(manuscript_id=manuscript_id, purged_at=manuscript.purged_at)
