"""Pydantic response models for the LLM quota surface (GET /quota, V-009)."""

from typing import Literal

from pydantic import BaseModel, Field


class ModelQuotaStatus(BaseModel):
    """One model's own quota island (V-049) — the free tier meters per
    model, so "how much is left" is only answerable per model."""

    model: str
    calls_used: int
    daily_limit: int
    calls_remaining: int
    cache_hits_today: int
    rpm_limit: int
    vision: bool
    exhausted: bool


class QuotaStatus(BaseModel):
    mode: Literal["fake", "live"]
    quota_day: str
    # Aggregated across the pool: the number capacity claims are priced
    # against (D-001/D-014).
    calls_used: int
    daily_limit: int
    calls_remaining: int
    cache_hits_today: int
    cache_hit_rate: float
    reset_at: str
    # The primary model's RPM — kept for the existing meter; per-model RPM
    # is in `models`.
    rpm_limit: int
    models: list[ModelQuotaStatus] = Field(default_factory=list)
