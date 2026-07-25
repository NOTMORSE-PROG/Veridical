"""Audit log HTTP surface (F8.10, screen 4s) — instructor-only (ticket
edge case: audit UI is instructor-only, never exposed via share links)."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.schemas import AuditLogDetail, PaginatedAuditLog
from app.audit.service import get_audit_log_detail, list_audit_log
from app.auth.dependencies import get_current_instructor
from app.db import get_session
from app.models.instructor import Instructor

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=PaginatedAuditLog)
async def list_audit_log_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
    check_run_id: int | None = None,
    event_type: str | None = None,
    event_type_prefix: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> PaginatedAuditLog:
    return await list_audit_log(
        session,
        instructor.id,
        check_run_id=check_run_id,
        event_type=event_type,
        event_type_prefix=event_type_prefix,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )


@router.get("/{audit_id}", response_model=AuditLogDetail)
async def get_audit_log_detail_route(
    audit_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> AuditLogDetail:
    return await get_audit_log_detail(session, instructor.id, audit_id)
