"""Share-link HTTP surface (F8.7, V-040): instructor-side management
(screen 4k) is auth-gated like every other route; the public read
(screen 4l) deliberately has NO auth dependency at all -- the token
itself is the credential, matching FEATURES' own "no adviser accounts"
design.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor
from app.db import get_session
from app.models.instructor import Instructor
from app.share.schemas import CreateShareLinkIn, SharedReportOut, ShareLinkOut
from app.share.service import (
    create_share_link,
    get_active_share_link,
    get_shared_report,
    revoke_share_link,
)

router = APIRouter(tags=["share"])


@router.get("/check-runs/{check_run_id}/share", response_model=ShareLinkOut | None)
async def get_share_link_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> ShareLinkOut | None:
    return await get_active_share_link(session, check_run_id, instructor.id)


@router.post("/check-runs/{check_run_id}/share", response_model=ShareLinkOut)
async def create_share_link_route(
    check_run_id: int,
    body: CreateShareLinkIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> ShareLinkOut:
    return await create_share_link(session, check_run_id, instructor.id, body.expires_at)


@router.delete("/check-runs/{check_run_id}/share")
async def revoke_share_link_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> dict[str, bool]:
    await revoke_share_link(session, check_run_id, instructor.id)
    return {"ok": True}


@router.get("/shared/{token}/report", response_model=SharedReportOut)
async def get_shared_report_route(
    token: str,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SharedReportOut:
    # AC: token not indexed -- belt-and-suspenders with the frontend's
    # own <meta name="robots"> on the adviser-view page (a search engine
    # honors whichever it sees; a raw API response only has the header).
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return await get_shared_report(session, token)
