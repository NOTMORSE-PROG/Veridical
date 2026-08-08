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

from app.checks.accuracy_stats import wilson_interval
from app.config import Settings, get_settings


@dataclass(frozen=True)
class ShadowClassStats:
    """One criterion class's shadow-tier performance against ground
    truth, for one run of the harness."""

    class_name: str
    tier: str  # "tier0" | "tier1"
    n: int
    agreement: float  # 0..1, against golden labels (or Gemini, per D-012)
    n_agree: int | None = None  # successes behind `agreement`, for the interval


@dataclass(frozen=True)
class Promotion:
    class_name: str
    tier: str
    n: int
    agreement: float
    dated: str  # ISO date this promotion was earned
    agreement_lower_bound: float | None = None


def evaluate_promotions(
    stats: list[ShadowClassStats], *, dated: str, settings: Settings | None = None
) -> list[Promotion]:
    """A class is promoted the moment it clears both bars in the SAME run —
    never averaged across runs, never carried over from a smaller sample
    (D-012: "never on thin data").

    The bar is the LOWER BOUND of the 95% Wilson interval, not the observed
    rate (V-053). The observed rate answers "how did it do on these items?";
    autonomy needs "how badly could it really be doing?". At n=20 an observed
    90% has a lower bound near 70% — so point-estimate gating would hand a
    criterion class permanent autonomy on evidence consistent with it being
    wrong three times in ten. Since a promoted class stops asking the
    instructor, that error is invisible once made, which is exactly the kind
    of one-way door that deserves the conservative estimate.

    `n_agree` is required to compute the interval. A caller that omits it
    gets no promotion rather than a point-estimate fallback — silently
    reverting to the weaker rule is the failure this change exists to stop.
    """
    settings = settings or get_settings()
    promotions = []
    for s in stats:
        if s.n < settings.tier_promotion_min_n:
            continue
        n_agree = s.n_agree if s.n_agree is not None else None
        if n_agree is None:
            continue
        interval = wilson_interval(n_agree, s.n)
        if interval is None or interval.low < settings.tier_promotion_agreement:
            continue
        promotions.append(Promotion(s.class_name, s.tier, s.n, s.agreement, dated, interval.low))
    return promotions
