"""Check-run creation and lookup (screens 4f/4g): validates the
manuscript/rubric pair, creates the `queued` row, and answers queue-
position/progress queries. Kicking the worker to advance a freshly
created run promptly (rather than waiting for its next poll tick) is the
router's job (`BackgroundTasks`), not this module's — this stays pure
DB logic (CODING.md §2: services never import FastAPI).
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.errors import ConflictError, NotFoundError
from app.models.audit import AuditLog
from app.models.enums import CheckRunStatus, IngestStatus, LLMMode
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun
from app.pipeline.schemas import RerunEstimateItem, RerunEstimateOut

_LLM_CALL_EVENT_TYPES = ("llm_call", "llm_cache_hit")

_ACTIVE_STATUSES = (
    CheckRunStatus.queued,
    CheckRunStatus.ingesting,
    CheckRunStatus.structural,
    CheckRunStatus.semantic,
    CheckRunStatus.integrity,
    CheckRunStatus.aggregating,
)


async def create_check_run(
    session: AsyncSession,
    instructor_id: int,
    manuscript_id: int,
    rubric_id: int,
    settings: Settings | None = None,
) -> CheckRun:
    manuscript = await session.get(Manuscript, manuscript_id)
    if manuscript is None or manuscript.instructor_id != instructor_id:
        raise NotFoundError(f"No manuscript with id {manuscript_id}.")
    if manuscript.ingest_status != IngestStatus.done:
        raise ConflictError(
            "This manuscript hasn't finished ingestion yet. Try again once it's ready."
        )
    # backend-critic finding (V-042, P1): a purged manuscript's stored
    # files are gone (archive/service.py::purge_manuscript), but nothing
    # here checked for that -- a re-check attempt (the natural next move
    # after a purge, or a V-041 re-run) would run, fail deep in the
    # pipeline with a raw FileNotFoundError, and the frontend's generic
    # failure copy ("Something went wrong... Try running it again")
    # would tell the instructor to retry an action that can never
    # succeed. Caught here instead, with an honest, specific reason.
    if manuscript.purged_at is not None:
        raise ConflictError(
            "This manuscript's stored content was purged and can no longer be checked. "
            "Upload it again to check it."
        )

    rubric = await session.get(Rubric, rubric_id)
    if rubric is None or rubric.instructor_id != instructor_id:
        raise NotFoundError(f"No rubric with id {rubric_id}.")
    if not rubric.is_active:
        raise ConflictError(
            "Only a confirmed, active rubric version can be used to start a check. "
            "Confirm it on the rubric review screen first."
        )

    settings = settings or get_settings()
    # BUG-049: persisted at creation, not inferred later -- without this,
    # a fake-LLM-mode run's report/PDF/public link could never be told
    # apart from a real one after the fact.
    llm_mode = LLMMode.fake if settings.veridical_fake_llm else LLMMode.real
    check_run = CheckRun(manuscript_id=manuscript_id, rubric_id=rubric_id, llm_mode=llm_mode)
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


async def estimate_rerun_calls(
    session: AsyncSession, instructor_id: int, manuscript_ids: list[int], rubric_id: int
) -> RerunEstimateOut:
    """V-041 (D-001 quota discipline): shown BEFORE confirming a bulk
    re-run against `rubric_id`. See `RerunEstimateOut`'s own docstring
    for why each manuscript's own most recent DONE run's real measured
    call count is the estimate, rather than a first-principles model of
    the pipeline's call structure.

    Scoped to `rubric_id`'s OWN rubric family, same fix and same reason
    as `report.service.get_report`'s version-comparison query
    (`backend-critic` finding, V-041): a manuscript's most recent run
    could be under a completely unrelated format with a very different
    criteria count and call cost. A rough cross-family number would be a
    quota estimate, not a fabricated comparison claim, but this endpoint
    feeds a quota DECISION (D-001) -- honestly `None` ("no prior run
    under this format") is safer than a number that could be off by much
    more than the ticket's own 20% bar with no way for the instructor to
    know why.
    """
    target_rubric = await session.get(Rubric, rubric_id)
    if target_rubric is None or target_rubric.instructor_id != instructor_id:
        raise NotFoundError(f"No rubric with id {rubric_id}.")

    owned_manuscript_ids = set(
        (
            await session.scalars(
                select(Manuscript.id).where(
                    Manuscript.id.in_(manuscript_ids), Manuscript.instructor_id == instructor_id
                )
            )
        ).all()
    )

    latest_done_by_manuscript: dict[int, int] = {}
    if owned_manuscript_ids:
        family_rubric_ids = select(Rubric.id).where(
            Rubric.rubric_family_id == target_rubric.rubric_family_id
        )
        runs = (
            await session.scalars(
                select(CheckRun)
                .where(
                    CheckRun.manuscript_id.in_(owned_manuscript_ids),
                    CheckRun.status == CheckRunStatus.done,
                    CheckRun.rubric_id.in_(family_rubric_ids),
                )
                .order_by(CheckRun.manuscript_id, CheckRun.created_at.desc())
            )
        ).all()
        for run in runs:
            latest_done_by_manuscript.setdefault(run.manuscript_id, run.id)

    call_counts_by_run: dict[int, int] = {}
    run_ids = list(latest_done_by_manuscript.values())
    if run_ids:
        rows = (
            await session.execute(
                select(AuditLog.check_run_id, func.count())
                .where(
                    AuditLog.check_run_id.in_(run_ids),
                    AuditLog.event_type.in_(_LLM_CALL_EVENT_TYPES),
                )
                .group_by(AuditLog.check_run_id)
            )
        ).all()
        call_counts_by_run = {run_id: count for run_id, count in rows}

    # Manuscript ids the caller doesn't own are silently excluded, never
    # echoed back with a null estimate -- same ownership-scoping contract
    # every other read endpoint in this codebase already follows (a
    # stranger's id existing at all is not this endpoint's to confirm).
    items: list[RerunEstimateItem] = []
    total = 0
    no_estimate = 0
    for manuscript_id in manuscript_ids:
        if manuscript_id not in owned_manuscript_ids:
            continue
        run_id = latest_done_by_manuscript.get(manuscript_id)
        calls = call_counts_by_run.get(run_id) if run_id is not None else None
        if calls is None:
            no_estimate += 1
        else:
            total += calls
        items.append(RerunEstimateItem(manuscript_id=manuscript_id, estimated_calls=calls))

    return RerunEstimateOut(
        items=items, total_estimated_calls=total, manuscripts_with_no_estimate=no_estimate
    )
