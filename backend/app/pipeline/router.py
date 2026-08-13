"""Check-run HTTP surface (screens 4f/4g)."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor
from app.config import get_settings
from app.db import get_session
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.pipeline.schemas import CheckRunOut, CreateCheckRunRequest
from app.pipeline.service import create_check_run, get_check_run, list_check_runs, queue_position
from app.pipeline.worker import advance_once
from app.ratelimit import enforce_action_rate_limit

router = APIRouter(tags=["check-runs"])


async def _to_out(session: AsyncSession, check_run) -> CheckRunOut:
    position = await queue_position(session, check_run)
    # session.get() hits the identity map when already loaded this
    # session (the common case, since create/get already touched these
    # rows) — display-only fields for screen 4g, never load-bearing.
    manuscript = await session.get(Manuscript, check_run.manuscript_id)
    rubric = await session.get(Rubric, check_run.rubric_id)
    return CheckRunOut(
        id=check_run.id,
        manuscript_id=check_run.manuscript_id,
        rubric_id=check_run.rubric_id,
        status=check_run.status.value,
        stage_status=check_run.stage_status,
        queue_position=position,
        started_at=check_run.started_at,
        finished_at=check_run.finished_at,
        created_at=check_run.created_at,
        manuscript_group_label=manuscript.group_label if manuscript else None,
        manuscript_original_filename=manuscript.original_filename if manuscript else None,
        manuscript_uploaded_at=manuscript.created_at if manuscript else None,
        rubric_title=rubric.title if rubric else None,
    )


@router.post("/check-runs", response_model=CheckRunOut, status_code=201)
async def create_check_run_route(
    body: CreateCheckRunRequest,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> CheckRunOut:
    enforce_action_rate_limit(get_settings(), "check_run", instructor.id)
    check_run = await create_check_run(session, instructor.id, body.manuscript_id, body.rubric_id)
    # Kick it now rather than waiting for the poll loop's next tick — the
    # background worker still owns steady-state progress and any queueing
    # behind an already-running check (ticket AC: second upload queues).
    background_tasks.add_task(advance_once, check_run.id)
    return await _to_out(session, check_run)


@router.get("/check-runs/{check_run_id}", response_model=CheckRunOut)
async def get_check_run_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> CheckRunOut:
    check_run = await get_check_run(session, check_run_id, instructor.id)
    return await _to_out(session, check_run)


@router.get("/check-runs", response_model=list[CheckRunOut])
async def list_check_runs_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> list[CheckRunOut]:
    runs = await list_check_runs(session, instructor.id)
    return [await _to_out(session, run) for run in runs]
