"""Tier-promotion table (V-025, D-012 mechanism #1): a Tier 0/1 signal
class NEVER auto-decides a criterion until it's earned it — shadow
agreement against ground truth (golden labels or Gemini) must clear
`TIER_PROMOTION_AGREEMENT` over at least `TIER_PROMOTION_MIN_N` samples.
This module is the pure gate `tools/golden_harness.py` (well,
`backend/scripts/golden_harness.py` — see that file's own path-deviation
note) calls every run; it owns no state itself (the harness writes its
output to a dated report, same as the rest of golden.py).

Honest limitation (2026-07-25): no Tier 0/1 class has a binary
pass/fail VERDICT function yet — V-016's signal layer (readability,
vocab diversity) records raw metrics in SHADOW mode but nothing maps a
metric to a verdict to compare against a golden label. This module's
gate is real, tested, and ready; it simply has nothing to promote until
that mapping exists (a future ticket's scope, not invented here to pad
this one out).
"""

from dataclasses import dataclass

from app.config import Settings, get_settings


@dataclass(frozen=True)
class ShadowClassStats:
    """One criterion class's shadow-tier performance against ground
    truth, for one run of the harness."""

    class_name: str
    tier: str  # "tier0" | "tier1"
    n: int
    agreement: float  # 0..1, against golden labels (or Gemini, per D-012)


@dataclass(frozen=True)
class Promotion:
    class_name: str
    tier: str
    n: int
    agreement: float
    dated: str  # ISO date this promotion was earned


def evaluate_promotions(
    stats: list[ShadowClassStats], *, dated: str, settings: Settings | None = None
) -> list[Promotion]:
    """A class is promoted the moment it clears both bars in the SAME
    run — never averaged across runs, never carried over from a smaller
    sample (D-012: "never on thin data")."""
    settings = settings or get_settings()
    return [
        Promotion(s.class_name, s.tier, s.n, s.agreement, dated)
        for s in stats
        if s.n >= settings.tier_promotion_min_n and s.agreement >= settings.tier_promotion_agreement
    ]
