"""Rubric HTTP surface (F2.1/F2.3, screens 4c/4d). Auth arrives with
V-014 — see the demo-instructor note in service.py."""

from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.llm import get_llm_client
from app.rubric.schemas import RubricOut, UpdateCriteriaRequest
from app.rubric.service import create_rubric_from_upload, get_rubric, update_criteria

router = APIRouter(tags=["rubric"])

# Streaming read size for uploads — an I/O buffer, not a policy knob
# (matches app/ingest/router.py).
_CHUNK_BYTES = 1024 * 1024


@router.post("/rubrics", response_model=RubricOut)
async def upload_rubric(
    file: UploadFile,
    session: Annotated[AsyncSession, Depends(get_session)],
    title: str = "Untitled rubric",
) -> RubricOut:
    async def chunks():
        while chunk := await file.read(_CHUNK_BYTES):
            yield chunk

    settings = get_settings()
    rubric = await create_rubric_from_upload(
        session, chunks(), file.filename or "", title, get_llm_client(settings), settings
    )
    return RubricOut.model_validate(rubric)


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
