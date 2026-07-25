"""GET /quota — the dashboard quota meter (screens 4e/4u, V-009)."""

from fastapi import APIRouter

from app.config import get_settings
from app.llm import get_llm_queue
from app.llm.queue import next_reset_for, quota_day_for
from app.llm.schemas import QuotaStatus

router = APIRouter(tags=["llm"])


@router.get("/quota", response_model=QuotaStatus)
async def get_quota() -> QuotaStatus:
    settings = get_settings()
    if settings.veridical_fake_llm:
        # Fake mode spends no real quota — report that honestly rather than
        # inventing numbers (charter rule 3: honesty compounds).
        return QuotaStatus(
            mode="fake",
            quota_day=quota_day_for(settings.llm_quota_reset_timezone),
            calls_used=0,
            daily_limit=settings.llm_daily_quota,
            calls_remaining=settings.llm_daily_quota,
            cache_hits_today=0,
            cache_hit_rate=0.0,
            reset_at=next_reset_for(settings.llm_quota_reset_timezone).isoformat(),
            rpm_limit=settings.llm_rpm,
        )
    status = await get_llm_queue(settings).get_quota_status()
    return QuotaStatus(mode="live", **status)
