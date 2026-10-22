"""F3 short database admission transactions and renewable fencing tokens."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.enums import CheckRunStatus
from app.models.manuscript import Manuscript
from app.models.run import CheckRun
from app.pipeline.guards import try_guard
from app.pipeline.machine import is_blocked

NON_TERMINAL = (
    CheckRunStatus.queued,
    CheckRunStatus.ingesting,
    CheckRunStatus.structural,
    CheckRunStatus.semantic,
    CheckRunStatus.integrity,
    CheckRunStatus.aggregating,
)


class AdmissionState(StrEnum):
    admitted = "admitted"
    busy = "busy"
    idle = "idle"


@dataclass(frozen=True)
class Admission:
    state: AdmissionState
    run: CheckRun | None = None
    token: datetime | None = None
    attempt_started: float | None = None


async def fair_candidates(session: AsyncSession) -> list[CheckRun]:
    """Oldest instructor heads rotate by last service; parked heads may yield."""
    rows = (
        await session.execute(
            select(CheckRun, Manuscript.instructor_id)
            .join(Manuscript, CheckRun.manuscript_id == Manuscript.id)
            .where(CheckRun.status.in_(NON_TERMINAL))
            .order_by(CheckRun.created_at, CheckRun.id)
        )
    ).all()
    if not rows:
        return []
    last_started = dict(
        (
            await session.execute(
                select(Manuscript.instructor_id, func.max(CheckRun.started_at))
                .join(Manuscript, CheckRun.manuscript_id == Manuscript.id)
                .where(Manuscript.instructor_id.in_({owner for _, owner in rows}))
                .group_by(Manuscript.instructor_id)
            )
        ).all()
    )
    heads, rest = [], []
    seen = set()
    for run, owner in rows:
        if owner in seen:
            rest.append(run)
        else:
            seen.add(owner)
            heads.append((run, owner))
    never_started = datetime.min.replace(tzinfo=UTC)
    heads.sort(
        key=lambda item: (
            last_started.get(item[1]) is not None,
            last_started.get(item[1]) or never_started,
        )
    )
    return [run for run, _ in heads] + rest


async def admit_next(
    session: AsyncSession, settings: Settings, *, requested_run_id: int | None = None
) -> Admission:
    """Guard visibility, fair selection and claim in one short transaction.

    Fresh claims include terminal callbacks until fenced release. Never skip
    locked rows in that visibility check. Individual queued candidates may be
    skipped without waiting on pipeline-owned row locks while holding the guard.
    """
    if session.new or session.dirty or session.deleted:
        raise ValueError("Admission requires a clean coordination session")
    attempt_started = asyncio.get_running_loop().time()
    try:
        async with asyncio.timeout(settings.pipeline_coordination_timeout_seconds):
            if not await try_guard(session, settings):
                await session.rollback()
                return Admission(AdmissionState.busy)
            now = await session.scalar(select(func.clock_timestamp()))
            stale_before = now - timedelta(seconds=settings.pipeline_claim_stale_seconds)
            if await session.scalar(
                select(CheckRun.id).where(CheckRun.claimed_at >= stale_before).limit(1)
            ):
                await session.rollback()
                return Admission(AdmissionState.busy)
            contended = False
            eligibility_now = datetime.now(UTC)
            for candidate in await fair_candidates(session):
                if is_blocked(candidate, now=eligibility_now):
                    continue
                if requested_run_id is not None and candidate.id != requested_run_id:
                    await session.rollback()
                    return Admission(AdmissionState.busy)
                locked = await session.scalar(
                    select(CheckRun)
                    .where(CheckRun.id == candidate.id)
                    .with_for_update(skip_locked=True, key_share=True)
                    .execution_options(populate_existing=True)
                )
                if locked is None:
                    contended = True
                    continue
                if locked.status not in NON_TERMINAL or is_blocked(locked, now=eligibility_now):
                    continue
                locked.claimed_at = now
                await session.commit()
                return Admission(AdmissionState.admitted, locked, now, attempt_started)
            await session.rollback()
            return Admission(AdmissionState.busy if contended else AdmissionState.idle)
    except BaseException:
        await session.rollback()
        raise


async def release_claim(session: AsyncSession, run_id: int, token: datetime) -> None:
    await session.execute(
        update(CheckRun)
        .where(CheckRun.id == run_id, CheckRun.claimed_at == token)
        .values(claimed_at=None)
    )
    await session.commit()
