"""Dashboard KPI HTTP surface (screen 4e)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor
from app.dashboard.schemas import DashboardStats
from app.dashboard.service import get_dashboard_stats
from app.db import get_session
from app.models.instructor import Instructor

router = APIRouter(tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
async def get_stats_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> DashboardStats:
    return await get_dashboard_stats(session, instructor.id)
