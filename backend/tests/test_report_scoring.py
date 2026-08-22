"""V-019 unit tests: the scoring engine is a pure function (results,
flags, config) -> (score, status) — table-driven against hand-computed
examples, no DB, no LLM.
"""

from app.config import get_settings
from app.models.enums import FlagSeverity, ReadinessStatus, ResultOutcome
from app.report.scoring import (
    ScorableFlag,
    ScorableResult,
    flag_ai_verdict_summary,
    score_check_run,
)


def _result(criterion_id, weight, outcome, score=None):
    return ScorableResult(criterion_id=criterion_id, weight=weight, outcome=outcome, score=score)


def test_hand_computed_composite_two_criteria_equal_weight():
    # (100*50 + 0*50) / 100 * 100 = 50.0
    results = [
        _result(1, 50, ResultOutcome.passed, 100.0),
        _result(2, 50, ResultOutcome.failed, 0.0),
    ]
    scoring = score_check_run(results, [], get_settings())
    assert scoring.composite_score == 50.0
    assert scoring.status == ReadinessStatus.not_ready  # 50.0 < not_ready_max_score (60.0)


def test_hand_computed_composite_weighted_example():
    # (100*70 + 50*20 + 0*10) / 100 = 80.0
    results = [
        _result(1, 70, ResultOutcome.passed, 100.0),
        _result(2, 20, ResultOutcome.passed, 50.0),
        _result(3, 10, ResultOutcome.failed, 0.0),
    ]
    scoring = score_check_run(results, [], get_settings())
    assert scoring.composite_score == 80.0
    assert scoring.status == ReadinessStatus.conditionally_ready


def test_ready_requires_at_least_85_and_no_high_flag():
    results = [_result(1, 100, ResultOutcome.passed, 90.0)]
    scoring = score_check_run(results, [], get_settings())
    assert scoring.composite_score == 90.0
    assert scoring.status == ReadinessStatus.ready


def test_boundary_84_9_is_not_ready_for_ready_84_point_9_vs_85_point_0():
    just_under = score_check_run([_result(1, 100, ResultOutcome.passed, 84.9)], [], get_settings())
    exactly_85 = score_check_run([_result(1, 100, ResultOutcome.passed, 85.0)], [], get_settings())
    assert just_under.status == ReadinessStatus.conditionally_ready
    assert exactly_85.status == ReadinessStatus.ready


def test_boundary_60_0_is_not_ready_59_9_is_not_ready_too():
    at_60 = score_check_run([_result(1, 100, ResultOutcome.passed, 60.0)], [], get_settings())
    just_under_60 = score_check_run(
        [_result(1, 100, ResultOutcome.passed, 59.9)], [], get_settings()
    )
    assert at_60.status == ReadinessStatus.conditionally_ready
    assert just_under_60.status == ReadinessStatus.not_ready


def test_escalated_and_not_applicable_criteria_excluded_from_score():
    results = [
        _result(1, 50, ResultOutcome.passed, 100.0),
        _result(2, 50, ResultOutcome.escalated),
        _result(3, 999, ResultOutcome.not_applicable),
    ]
    scoring = score_check_run(results, [], get_settings())
    # Only criterion 1 counts: weight_sum=50, composite=100.0
    assert scoring.composite_score == 100.0
    counted_ids = {c.criterion_id for c in scoring.contributions if c.counted}
    assert counted_ids == {1}


def test_all_escalated_run_is_needs_review_with_no_score():
    results = [
        _result(1, 50, ResultOutcome.escalated),
        _result(2, 50, ResultOutcome.escalated),
    ]
    scoring = score_check_run(results, [], get_settings())
    assert scoring.composite_score is None
    assert scoring.status == ReadinessStatus.needs_review
    assert scoring.reason is not None


def test_zero_weight_rubric_is_needs_review_not_a_crash():
    results = [
        _result(1, 0, ResultOutcome.passed, 100.0),
        _result(2, 0, ResultOutcome.failed, 0.0),
    ]
    scoring = score_check_run(results, [], get_settings())
    assert scoring.composite_score is None
    assert scoring.status == ReadinessStatus.needs_review


def test_unresolved_high_flag_forces_not_ready_regardless_of_score():
    results = [_result(1, 100, ResultOutcome.passed, 100.0)]  # would otherwise be Ready
    flags = [ScorableFlag(severity=FlagSeverity.high, overridden=False)]
    scoring = score_check_run(results, flags, get_settings())
    assert scoring.status == ReadinessStatus.not_ready
    assert scoring.unresolved_high_flag_count == 1


def test_overridden_high_flag_does_not_force_not_ready():
    results = [_result(1, 100, ResultOutcome.passed, 100.0)]
    flags = [ScorableFlag(severity=FlagSeverity.high, overridden=True)]
    scoring = score_check_run(results, flags, get_settings())
    assert scoring.status == ReadinessStatus.ready
    assert scoring.unresolved_high_flag_count == 0


# --- BUG-053 Option A: a flag the check reached no real finding on -----------------


def test_no_verdict_high_flag_does_not_force_not_ready():
    """A flag whose underlying check left no real determination behind
    must not decide the verdict by default (charter rule 1) -- same
    protection an overridden flag already gets. Not reachable by any
    shipped check today (every one always sets "kind"/"reason"/"verdict"/
    "basis"), so this is a defensive floor, exercised here directly."""
    results = [_result(1, 100, ResultOutcome.passed, 100.0)]  # would otherwise be Ready
    flags = [ScorableFlag(severity=FlagSeverity.high, overridden=False, has_verdict=False)]
    scoring = score_check_run(results, flags, get_settings())
    assert scoring.status == ReadinessStatus.ready
    assert scoring.unresolved_high_flag_count == 0
    assert scoring.flag_deduction == 0.0


def test_flag_ai_verdict_summary_reads_kind_before_calling_it_unavailable():
    """REGRESSION (BUG-053): F4-F7 flags store their finding under "kind"/
    "reason" (V-033), never "verdict"/"basis" (semantic grading's own
    vocabulary, V-020) -- a naive `detail.get("verdict") or
    detail.get("basis")` was None for every one of them, rendering a REAL
    finding (a retraction, a contradiction, a reuse match) as "AI verdict:
    unavailable" on screen."""
    assert flag_ai_verdict_summary({"kind": "retracted_source", "reason": "..."}) == (
        "retracted_source"
    )
    assert flag_ai_verdict_summary({"verdict": "fail"}) == "fail"
    assert flag_ai_verdict_summary({"basis": "llm"}) == "llm"
    assert flag_ai_verdict_summary({}) is None
    assert flag_ai_verdict_summary(None) is None


def test_flag_deduction_is_capped():
    settings = get_settings()
    results = [_result(1, 100, ResultOutcome.passed, 100.0)]
    # Many low-severity flags would deduct far past the cap without one.
    flags = [ScorableFlag(severity=FlagSeverity.low, overridden=False) for _ in range(50)]
    scoring = score_check_run(results, flags, settings)
    assert scoring.flag_deduction == settings.scoring_flag_deduction_cap
    assert scoring.composite_score == 100.0 - settings.scoring_flag_deduction_cap


def test_thresholds_are_visible_in_the_result():
    settings = get_settings()
    scoring = score_check_run([_result(1, 100, ResultOutcome.passed, 100.0)], [], settings)
    assert scoring.thresholds == {
        "ready_min_score": settings.scoring_ready_min_score,
        "not_ready_max_score": settings.scoring_not_ready_max_score,
    }


def test_score_is_monotonic_in_criterion_results():
    """Property (ticket QA step): swapping a fail for a pass never
    lowers the composite score, all else equal."""
    base = [
        _result(1, 50, ResultOutcome.passed, 100.0),
        _result(2, 50, ResultOutcome.failed, 0.0),
    ]
    improved = [
        _result(1, 50, ResultOutcome.passed, 100.0),
        _result(2, 50, ResultOutcome.passed, 100.0),
    ]
    before = score_check_run(base, [], get_settings()).composite_score
    after = score_check_run(improved, [], get_settings()).composite_score
    assert after >= before
