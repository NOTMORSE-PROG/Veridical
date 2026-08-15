"""Rubric HTTP surface (F2.1/F2.3/F2.4, screens 4c/4d/4m)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor
from app.config import get_settings
from app.db import get_session
from app.llm import get_llm_client_for
from app.models.instructor import Instructor
from app.ratelimit import enforce_action_rate_limit
from app.rubric.schemas import RubricListItem, RubricOut, UpdateCriteriaRequest
from app.rubric.service import (
    activate_rubric,
    create_rubric_from_upload,
    delete_rubric,
    get_rubric,
    list_rubric_families,
    list_rubric_versions,
    update_criteria,
)

router = APIRouter(tags=["rubric"])

# Streaming read size for uploads — an I/O buffer, not a policy knob
# (matches app/ingest/router.py).
_CHUNK_BYTES = 1024 * 1024


@router.post("/rubrics", response_model=RubricOut)
async def upload_rubric(
    file: UploadFile,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
    title: str = "Untitled rubric",
    family_id: uuid.UUID | None = None,
) -> RubricOut:
    async def chunks():
        while chunk := await file.read(_CHUNK_BYTES):
            yield chunk

    settings = get_settings()
    enforce_action_rate_limit(settings, "rubric_upload", instructor.id)
    llm = await get_llm_client_for(session, settings, instructor.id)
    rubric = await create_rubric_from_upload(
        session,
        chunks(),
        file.filename or "",
        title,
        llm,
        settings,
        instructor_id=instructor.id,
        family_id=family_id,
    )
    return RubricOut.model_validate(await get_rubric(session, rubric.id, instructor.id))


@router.get("/rubrics/{rubric_id}", response_model=RubricOut)
async def get_rubric_route(
    rubric_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> RubricOut:
    rubric = await get_rubric(session, rubric_id, instructor.id)
    return RubricOut.model_validate(rubric)


@router.put("/rubrics/{rubric_id}/criteria", response_model=RubricOut)
async def put_rubric_criteria(
    rubric_id: int,
    body: UpdateCriteriaRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> RubricOut:
    rubric = await update_criteria(session, rubric_id, body, instructor.id)
    return RubricOut.model_validate(rubric)


@router.post("/rubrics/{rubric_id}/activate", response_model=RubricOut)
async def activate_rubric_route(
    rubric_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> RubricOut:
    rubric = await activate_rubric(session, rubric_id, instructor.id)
    return RubricOut.model_validate(rubric)


@router.delete("/rubrics/{rubric_id}", status_code=204)
async def delete_rubric_route(
    rubric_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> None:
    await delete_rubric(session, rubric_id, instructor.id)


@router.get("/rubric-families", response_model=list[RubricListItem])
async def list_rubric_families_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> list[RubricListItem]:
    return await list_rubric_families(session, instructor.id)


@router.get("/rubric-families/{family_id}/versions", response_model=list[RubricListItem])
async def list_rubric_versions_route(
    family_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> list[RubricListItem]:
    return await list_rubric_versions(session, family_id, instructor.id)
