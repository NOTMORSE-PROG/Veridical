"""Check-run creation and lookup (screens 4f/4g): validates the
manuscript/rubric pair, creates the `queued` row, and answers queue-
position/progress queries. Kicking the worker to advance a freshly
created run promptly (rather than waiting for its next poll tick) is the
router's job (`BackgroundTasks`), not this module's — this stays pure
DB logic (CODING.md §2: services never import FastAPI).
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ConflictError, NotFoundError
from app.models.enums import CheckRunStatus, IngestStatus
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun

_ACTIVE_STATUSES = (
    CheckRunStatus.queued,
    CheckRunStatus.ingesting,
    CheckRunStatus.structural,
    CheckRunStatus.semantic,
    CheckRunStatus.integrity,
    CheckRunStatus.aggregating,
)


async def create_check_run(
    session: AsyncSession, instructor_id: int, manuscript_id: int, rubric_id: int
) -> CheckRun:
    manuscript = await session.get(Manuscript, manuscript_id)
    if manuscript is None or manuscript.instructor_id != instructor_id:
        raise NotFoundError(f"No manuscript with id {manuscript_id}.")
    if manuscript.ingest_status != IngestStatus.done:
        raise ConflictError(
            "This manuscript hasn't finished ingestion yet. Try again once it's ready."
        )

    rubric = await session.get(Rubric, rubric_id)
    if rubric is None or rubric.instructor_id != instructor_id:
        raise NotFoundError(f"No rubric with id {rubric_id}.")
    if not rubric.is_active:
        raise ConflictError(
            "Only a confirmed, active rubric version can be used to start a check. "
            "Confirm it on the rubric review screen first."
        )

    check_run = CheckRun(manuscript_id=manuscript_id, rubric_id=rubric_id)
    session.add(check_run)
    await session.commit()
    await session.refresh(check_run)
    return check_run


async def get_check_run(session: AsyncSession, check_run_id: int, instructor_id: int) -> CheckRun:
    check_run = await session.scalar(
        select(CheckRun)
        .join(Manuscript, Manuscript.id == CheckRun.manuscript_id)
        .where(CheckRun.id == check_run_id, Manuscript.instructor_id == instructor_id)
    )
    if check_run is None:
        raise NotFoundError(f"No check run with id {check_run_id}.")
    return check_run


async def list_check_runs(session: AsyncSession, instructor_id: int) -> list[CheckRun]:
    return list(
        (
            await session.scalars(
                select(CheckRun)
                .join(Manuscript, Manuscript.id == CheckRun.manuscript_id)
                .where(Manuscript.instructor_id == instructor_id)
                .order_by(CheckRun.created_at.desc())
            )
        ).all()
    )


async def queue_position(session: AsyncSession, check_run: CheckRun) -> int | None:
    """1-based position among runs still waiting/running, oldest first —
    `None` once a run is terminal (done/failed): "queue position" stops
    being a meaningful concept (ticket AC: position shown while queued)."""
    if check_run.status not in _ACTIVE_STATUSES:
        return None
    count = await session.scalar(
        select(func.count())
        .select_from(CheckRun)
        .where(
            CheckRun.status.in_(_ACTIVE_STATUSES),
            CheckRun.created_at < check_run.created_at,
        )
    )
    return (count or 0) + 1
