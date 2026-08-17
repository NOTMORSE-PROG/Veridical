"""D-023 (BUG-051/052/098): criterion weight -> Low/Medium/High importance,
relative to the rubric's own equal-split average -- pure function, no DB.
"""

import pytest

from app.config import Settings, get_settings
from app.report.weight_importance import weight_importance


def test_weight_at_the_average_is_medium():
    settings = get_settings()
    assert weight_importance(10.0, average_weight=10.0, settings=settings) == "med"


def test_weight_well_below_average_is_low():
    settings = get_settings()
    assert weight_importance(2.0, average_weight=10.0, settings=settings) == "low"


def test_weight_well_above_average_is_high():
    settings = get_settings()
    assert weight_importance(18.0, average_weight=10.0, settings=settings) == "high"


def test_weight_exactly_at_the_low_boundary_is_not_low():
    settings = get_settings()
    # ratio == low_max_ratio (0.5) is the boundary -- "< " not "<=", so
    # exactly at the boundary reads as med, not low.
    assert weight_importance(5.0, average_weight=10.0, settings=settings) == "med"


def test_weight_exactly_at_the_high_boundary_is_high():
    settings = get_settings()
    # ratio == high_min_ratio (1.5) is ">= ", so exactly at the boundary
    # already reads as high.
    assert weight_importance(15.0, average_weight=10.0, settings=settings) == "high"


def test_equal_weights_across_a_rubric_are_all_medium():
    """The property that matters most: a rubric where every criterion is
    weighted identically must never show artificial spread across
    severity tiers -- every criterion is exactly 1x the average."""
    settings = get_settings()
    for w in (5.0, 5.0, 5.0, 5.0):
        assert weight_importance(w, average_weight=5.0, settings=settings) == "med"


def test_zero_average_defaults_to_medium_not_a_crash():
    """Guarded, not divided by zero -- can't happen for a real persisted
    criterion (API-validated weight > 0), but defensive rather than
    assuming that invariant holds forever."""
    settings = get_settings()
    assert weight_importance(0.0, average_weight=0.0, settings=settings) == "med"


def test_default_ratios_match_the_frontend_live_preview():
    """backend-critic finding: `ReviewCriteria.tsx`'s `liveWeightImportance`
    hardcodes the SAME 0.5/1.5 ratios client-side (a deliberate, disclosed
    exception -- the frontend can't read `Settings` per-request), so the
    live preview matches what the report will actually show in the common
    (default-config) case. This is the tripwire: if these defaults ever
    change, this test fails and says so, rather than the two copies
    silently drifting apart."""
    settings = Settings()
    assert settings.weight_importance_low_max_ratio == 0.5
    assert settings.weight_importance_high_min_ratio == 1.5


def test_low_max_ratio_must_be_below_high_min_ratio():
    """backend-critic finding: `weight_importance()` checks low first, then
    high, else med -- swapped or equal thresholds would make "med"
    unreachable and silently relabel high-weight criteria as low."""
    with pytest.raises(ValueError, match="weight_importance_low_max_ratio"):
        Settings(weight_importance_low_max_ratio=1.5, weight_importance_high_min_ratio=0.5)
    with pytest.raises(ValueError, match="weight_importance_low_max_ratio"):
        Settings(weight_importance_low_max_ratio=1.0, weight_importance_high_min_ratio=1.0)
