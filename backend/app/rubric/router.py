"""Rubric HTTP surface (F2.1/F2.3/F2.4, screens 4c/4d/4m). Auth arrives
with V-014 — see the demo-instructor note in service.py."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.llm import get_llm_client
from app.models.instructor import Instructor
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
    title: str = "Untitled rubric",
    family_id: uuid.UUID | None = None,
) -> RubricOut:
    async def chunks():
        while chunk := await file.read(_CHUNK_BYTES):
            yield chunk

    settings = get_settings()
    rubric = await create_rubric_from_upload(
        session,
        chunks(),
        file.filename or "",
        title,
        get_llm_client(settings),
        settings,
        family_id=family_id,
    )
    return RubricOut.model_validate(await get_rubric(session, rubric.id))


@router.get("/rubrics/{rubric_id}", response_model=RubricOut)
async def get_rubric_route(
    rubric_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> RubricOut:
    rubric = await get_rubric(session, rubric_id)
    return RubricOut.model_validate(rubric)


@router.put("/rubrics/{rubric_id}/criteria", response_model=RubricOut)
async def put_rubric_criteria(
    rubric_id: int,
    body: UpdateCriteriaRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RubricOut:
    rubric = await update_criteria(session, rubric_id, body)
    return RubricOut.model_validate(rubric)


@router.post("/rubrics/{rubric_id}/activate", response_model=RubricOut)
async def activate_rubric_route(
    rubric_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> RubricOut:
    rubric = await activate_rubric(session, rubric_id)
    return RubricOut.model_validate(rubric)


@router.delete("/rubrics/{rubric_id}", status_code=204)
async def delete_rubric_route(
    rubric_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> None:
    await delete_rubric(session, rubric_id)


@router.get("/rubric-families", response_model=list[RubricListItem])
async def list_rubric_families_route(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[RubricListItem]:
    # Demo-instructor convention (service.py) until real auth is wired
    # onto this module — same as every other route here.
    instructor = await session.scalar(select(Instructor).order_by(Instructor.id).limit(1))
    if instructor is None:
        return []
    return await list_rubric_families(session, instructor.id)


@router.get("/rubric-families/{family_id}/versions", response_model=list[RubricListItem])
async def list_rubric_versions_route(
    family_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]
) -> list[RubricListItem]:
    return await list_rubric_versions(session, family_id)
