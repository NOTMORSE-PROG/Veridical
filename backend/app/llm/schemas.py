"""Pydantic response models for the LLM quota surface (GET /quota, V-009)."""

from typing import Literal

from pydantic import BaseModel, Field


class ModelQuotaStatus(BaseModel):
    """One configured local budget, including any historical aliases."""

    model: str
    calls_used: int
    daily_limit: int
    calls_remaining: int
    cache_hits_today: int
    rpm_limit: int
    vision: bool
    exhausted: bool
    budget_aliases: list[str] = Field(default_factory=list)
    recorded_budget_debits: int = 0
    historical_budget_debits: int = 0


class LegacyModelBudget(BaseModel):
    """Saved requested-ID records outside the active routing configuration."""

    model: str
    recorded_budget_debits: int
    cache_hits_today: int
    budget_model: str | None = None


class QuotaStatus(BaseModel):
    mode: Literal["fake", "live"]
    # V-052 (BYOK): which key this status describes -- "own" only when the
    # requesting instructor has their own Gemini key configured and this
    # reflects THEIR island, "shared" otherwise (including fake mode, which
    # never spends a real key of either kind -- charter rule 9, honesty).
    key_source: Literal["own", "shared"]
    quota_day: str
    # Effective configured local budget usage, not physical API attempts.
    calls_used: int
    daily_limit: int
    calls_remaining: int
    cache_hits_today: int
    cache_hit_rate: float | None
    reset_at: str
    # The primary model's RPM — kept for the existing meter; per-model RPM
    # is in `models`.
    rpm_limit: int
    models: list[ModelQuotaStatus] = Field(default_factory=list)
    recorded_budget_debits: int = 0
    legacy_models: list[LegacyModelBudget] = Field(default_factory=list)
