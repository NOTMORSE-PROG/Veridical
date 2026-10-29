"""FastAPI dependency: the current authenticated instructor — the ONE
route guard every protected route uses (F9.1, CODING.md §2)."""

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import messages
from app.auth.service import get_instructor_by_token
from app.config import get_settings
from app.db import DbInt, get_session
from app.errors import ActorContextChangedError, UnauthenticatedError
from app.models.instructor import Instructor


async def get_optional_instructor(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> Instructor | None:
    """Resolve the browser session without treating signed-out as an error.

    This is deliberately separate from ``get_current_instructor``: only the
    read-only session-status route uses it. Authorization guards remain strict.
    """

    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if token is None:
        return None
    return await get_instructor_by_token(session, token)


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


async def get_upload_instructor(
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
    expected_instructor: Annotated[
        DbInt | None, Header(alias="X-Veridical-Expected-Instructor", ge=1)
    ] = None,
) -> Instructor:
    """F1/F2/F9: optional intent assertion; authentication remains authoritative.

    Updated upload forms bind their captured actor before application upload
    bookkeeping. This is neither an actor selector nor request idempotency.
    Omitted assertions preserve the existing authenticated API contract.
    """
    if expected_instructor is not None and expected_instructor != instructor.id:
        raise ActorContextChangedError(messages.UPLOAD_ACTOR_CONTEXT_CHANGED)
    return instructor
