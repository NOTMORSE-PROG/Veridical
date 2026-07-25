"""Pydantic response models for the LLM quota surface (GET /quota, V-009)."""

from typing import Literal

from pydantic import BaseModel


class QuotaStatus(BaseModel):
    mode: Literal["fake", "live"]
    quota_day: str
    calls_used: int
    daily_limit: int
    calls_remaining: int
    cache_hits_today: int
    cache_hit_rate: float
    reset_at: str
    rpm_limit: int
