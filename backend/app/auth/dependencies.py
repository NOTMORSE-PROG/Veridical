"""FastAPI dependency: the current authenticated instructor — the ONE
route guard every protected route uses (F9.1, CODING.md §2)."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import get_instructor_by_token
from app.config import get_settings
from app.db import get_session
from app.errors import UnauthenticatedError
from app.models.instructor import Instructor


async def get_current_instructor(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> Instructor:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if token is None:
        raise UnauthenticatedError("Not signed in.")
    instructor = await get_instructor_by_token(session, token)
    if instructor is None:
        raise UnauthenticatedError("Session expired or invalid. Please sign in again.")
    return instructor
