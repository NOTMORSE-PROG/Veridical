"""Auth HTTP surface (F9.1): login, logout, me."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor
from app.auth.schemas import InstructorOut, LoginRequest
from app.auth.service import (
    RateLimitedError,
    authenticate,
    create_session,
    delete_session,
    mark_onboarding_dismissed,
    mark_onboarding_replayed,
)
from app.config import get_settings
from app.db import get_session
from app.errors import UnauthenticatedError
from app.models.instructor import Instructor

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=InstructorOut)
async def login(
    body: LoginRequest, response: Response, session: Annotated[AsyncSession, Depends(get_session)]
) -> InstructorOut:
    settings = get_settings()
    try:
        instructor = await authenticate(session, body.email, body.password, settings)
    except RateLimitedError as exc:
        raise UnauthenticatedError(
            "Too many failed sign-in attempts. Please try again in a few minutes."
        ) from exc
    if instructor is None:
        raise UnauthenticatedError("Incorrect email or password.")

    row = await create_session(session, instructor, settings)
    response.set_cookie(
        settings.session_cookie_name,
        row.token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )
    return InstructorOut.model_validate(instructor)


@router.post("/logout")
async def logout(
    request: Request, response: Response, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, bool]:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if token is not None:
        await delete_session(session, token)
    response.delete_cookie(settings.session_cookie_name)
    return {"ok": True}


@router.get("/me", response_model=InstructorOut)
async def me(instructor: Annotated[Instructor, Depends(get_current_instructor)]) -> InstructorOut:
    return InstructorOut.model_validate(instructor)


@router.post("/onboarding/dismiss", response_model=InstructorOut)
async def dismiss_onboarding(
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InstructorOut:
    updated = await mark_onboarding_dismissed(session, instructor)
    return InstructorOut.model_validate(updated)


@router.post("/onboarding/replay", response_model=InstructorOut)
async def replay_onboarding(
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InstructorOut:
    """V-057 (F9.2 follow-up): the coach-mark tour's replay control."""
    updated = await mark_onboarding_replayed(session, instructor)
    return InstructorOut.model_validate(updated)
