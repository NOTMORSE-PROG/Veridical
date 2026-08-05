"""GET /quota — the dashboard quota meter (screens 4e/4u, V-009)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_instructor
from app.config import get_settings
from app.llm import get_llm_queue
from app.llm.pool import load_model_pool, pool_daily_capacity
from app.llm.queue import next_reset_for, quota_day_for
from app.llm.schemas import ModelQuotaStatus, QuotaStatus
from app.models.instructor import Instructor

router = APIRouter(tags=["llm"])


@router.get("/quota", response_model=QuotaStatus)
async def get_quota(
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> QuotaStatus:
    settings = get_settings()
    if settings.veridical_fake_llm:
        # Fake mode spends no real quota — report that honestly rather than
        # inventing numbers (charter rule 3: honesty compounds). The LIMITS
        # are still the real pool's, so the meter never implies fake mode
        # has more capacity than production does.
        pool = load_model_pool(settings.llm_model_pool_file)
        return QuotaStatus(
            mode="fake",
            quota_day=quota_day_for(settings.llm_quota_reset_timezone),
            calls_used=0,
            daily_limit=pool_daily_capacity(pool),
            calls_remaining=pool_daily_capacity(pool),
            cache_hits_today=0,
            cache_hit_rate=0.0,
            reset_at=next_reset_for(settings.llm_quota_reset_timezone).isoformat(),
            rpm_limit=pool[0].rpm,
            models=[
                ModelQuotaStatus(
                    model=spec.model,
                    calls_used=0,
                    daily_limit=spec.daily_quota,
                    calls_remaining=spec.daily_quota,
                    cache_hits_today=0,
                    rpm_limit=spec.rpm,
                    vision=spec.vision,
                    exhausted=False,
                )
                for spec in pool
            ],
        )
    status = await get_llm_queue(settings).get_quota_status()
    return QuotaStatus(mode="live", **status)
