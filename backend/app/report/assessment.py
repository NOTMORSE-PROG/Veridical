"""Shared transaction boundary for instructor changes to a completed assessment."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ConflictError, NotFoundError
from app.models.enums import CheckRunStatus
from app.models.manuscript import Manuscript
from app.models.run import CheckRun, ReadinessReport


async def lock_assessment(
    session: AsyncSession, check_run_id: int, *, write: bool = False
) -> ReadinessReport | None:
    with session.no_autoflush:
        return await session.scalar(
            select(ReadinessReport)
            .where(ReadinessReport.check_run_id == check_run_id)
            .with_for_update(read=not write)
            .execution_options(populate_existing=True)
        )


@asynccontextmanager
async def assessment_write(
    session: AsyncSession,
    check_run_id: int,
    instructor_id: int,
    expected_revision: int,
    *,
    allow_decided: bool = False,
) -> AsyncIterator[ReadinessReport]:
    """Validate the displayed revision and commit one complete action or nothing.

    Callers assemble their acknowledgment before leaving this context. The
    parent lock stays held through scoring, audit and response construction.
    """
    try:
        with session.no_autoflush:
            run = await session.scalar(
                select(CheckRun)
                .join(Manuscript, Manuscript.id == CheckRun.manuscript_id)
                .where(CheckRun.id == check_run_id, Manuscript.instructor_id == instructor_id)
                .execution_options(populate_existing=True)
            )
        if run is None:
            raise NotFoundError(f"No check run with id {check_run_id}.")
        report = await lock_assessment(session, check_run_id, write=True)
        await session.refresh(run)
        if run.status != CheckRunStatus.done:
            raise ConflictError("This check hasn't finished yet. Its report isn't ready.")
        if report is None:
            raise NotFoundError(f"No report for check run {check_run_id}.")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ConflictError("Reload and review the current assessment before saving.")
        if report.assessment_revision != expected_revision:
            raise ConflictError(
                "This assessment changed after you reviewed it. Your entry was not saved. "
                "Reload the current assessment and review your entry before confirming again."
            )
        if not allow_decided and report.decision is not None:
            raise ConflictError(
                "This report has already been decided. Reopen it first before changing "
                "anything that affects its score."
            )
        report.assessment_revision += 1
        await session.flush([report])
        yield report
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
