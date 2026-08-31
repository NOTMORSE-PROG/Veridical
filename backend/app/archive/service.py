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
from app.errors import ConflictError, NotFoundError
from app.ingest.service import raw_store_path
from app.models.manuscript import (
    Manuscript,
    ManuscriptArchive,
    ManuscriptChapterArchive,
    ManuscriptPassageArchive,
)
from app.models.run import CheckRun


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


async def purge_manuscript(
    session: AsyncSession, instructor_id: int, manuscript_id: int, settings: Settings
) -> PurgeOut:
    manuscript = await session.get(Manuscript, manuscript_id)
    if manuscript is None or manuscript.instructor_id != instructor_id:
        raise NotFoundError(f"Manuscript {manuscript_id} not found.")
    if manuscript.purged_at is not None:
        raise ConflictError("This manuscript's archive has already been purged.")

    await session.execute(
        delete(ManuscriptChapterArchive).where(
            ManuscriptChapterArchive.manuscript_id == manuscript_id
        )
    )
    await session.execute(
        delete(ManuscriptArchive).where(ManuscriptArchive.manuscript_id == manuscript_id)
    )
    # BUG-123: F7.4's passage-level archive (V-072) stores real body text
    # per passage (`text`/`context_text`), not just a vector -- this is
    # exactly the "comparison data used to check future manuscripts against
    # it" the purge confirmation modal promises is deleted. It was added
    # after this function shipped and purge was never updated to cover it,
    # so purged text kept matching and kept surfacing in other instructors'
    # flags. Same delete-by-manuscript_id pattern as the two tables above.
    await session.execute(
        delete(ManuscriptPassageArchive).where(
            ManuscriptPassageArchive.manuscript_id == manuscript_id
        )
    )

    # `file_ref` is already an absolute path (ingest/service.py writes it as
    # `str(data_dir / "uploads" / ...)`) -- used as-is, never re-joined.
    # Missing files (already gone, or an image-only manuscript that never
    # wrote a raw store) are a normal state, not an error.
    for path in (
        Path(manuscript.file_ref) if manuscript.file_ref.strip() else None,
        raw_store_path(settings, manuscript_id),
    ):
        if path is None:
            continue
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
