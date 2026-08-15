"""Settings screen HTTP surface (V-042, screen 4u; BYOK V-052)."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor
from app.config import get_settings
from app.db import get_session
from app.models.instructor import Instructor
from app.settings.schemas import SetApiKeyIn, SettingsOut
from app.settings.service import (
    delete_instructor_api_key,
    get_settings_view,
    set_instructor_api_key,
)

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=SettingsOut)
async def get_settings_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> SettingsOut:
    return await get_settings_view(session, get_settings(), instructor)


@router.post("/settings/api-key", response_model=SettingsOut, status_code=status.HTTP_200_OK)
async def set_api_key_route(
    body: SetApiKeyIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> SettingsOut:
    settings = get_settings()
    await set_instructor_api_key(session, settings, instructor, body.api_key)
    return await get_settings_view(session, settings, instructor)


@router.delete("/settings/api-key", response_model=SettingsOut)
async def delete_api_key_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> SettingsOut:
    settings = get_settings()
    await delete_instructor_api_key(session, instructor)
    return await get_settings_view(session, settings, instructor)
