"""Settings screen HTTP surface (V-042, screen 4u)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor
from app.config import get_settings
from app.db import get_session
from app.models.instructor import Instructor
from app.settings.schemas import SettingsOut
from app.settings.service import get_settings_view

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=SettingsOut)
async def get_settings_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    # Auth-gated (settings is instructor-only surface) even though the
    # response itself isn't per-instructor data yet -- see service.py.
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> SettingsOut:
    return await get_settings_view(session, get_settings())
