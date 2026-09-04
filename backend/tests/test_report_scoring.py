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


# --- BUG-150: 82 anchors on one finding must not count as 82 findings ------


def test_many_anchors_on_one_reuse_match_count_as_one_finding_for_the_gate():
    """82 passage-level flags against the SAME archived manuscript are 82
    rows but one fact -- the same `(check_kind, problem_kind, matched_ref)`
    boundary `flagClusters.ts` already uses to show "1 finding" on the
    report screen, not 82. The verdict-forcing count must agree with it."""
    results = [_result(1, 100, ResultOutcome.passed, 100.0)]
    flags = [
        ScorableFlag(
            severity=FlagSeverity.high,
            overridden=False,
            id=i,
            check_kind="originality_reuse",
            problem_kind="reuse_exact_duplicate_passage",
            matched_ref=99,
        )
        for i in range(82)
    ]
    scoring = score_check_run(results, flags, get_settings())
    assert scoring.status == ReadinessStatus.not_ready  # still forces Not Ready -- one real match
    assert scoring.unresolved_high_flag_count == 1  # but as ONE finding, not 82


def test_two_distinct_reuse_matches_still_count_as_two_findings():
    """Different `matched_ref` archives are genuinely different facts and
    must not be collapsed into each other."""
    results = [_result(1, 100, ResultOutcome.passed, 100.0)]
    flags = [
        ScorableFlag(
            severity=FlagSeverity.high,
            overridden=False,
            id=1,
            check_kind="originality_reuse",
            problem_kind="reuse_exact_duplicate_passage",
            matched_ref=99,
        ),
        ScorableFlag(
            severity=FlagSeverity.high,
            overridden=False,
            id=2,
            check_kind="originality_reuse",
            problem_kind="reuse_exact_duplicate_passage",
            matched_ref=100,
        ),
    ]
    scoring = score_check_run(results, flags, get_settings())
    assert scoring.unresolved_high_flag_count == 2


def test_a_reuse_flag_with_no_matched_ref_stays_its_own_singleton():
    """No persisted field to safely cluster on -- must not be guessed into
    someone else's finding just because problem_kind happens to match."""
    results = [_result(1, 100, ResultOutcome.passed, 100.0)]
    flags = [
        ScorableFlag(
            severity=FlagSeverity.high,
            overridden=False,
            id=1,
            check_kind="originality_reuse",
            problem_kind="reuse_whole_document",
            matched_ref=None,
        ),
        ScorableFlag(
            severity=FlagSeverity.high,
            overridden=False,
            id=2,
            check_kind="originality_reuse",
            problem_kind="reuse_whole_document",
            matched_ref=None,
        ),
    ]
    scoring = score_check_run(results, flags, get_settings())
    assert scoring.unresolved_high_flag_count == 2


def test_non_reuse_flags_cluster_on_criterion_and_evidence_text():
    """A repeated F4/F5/F6 finding (no matched_ref concept) clusters on
    (check_kind, problem_kind, criterion_text, evidence_excerpt), same as
    the frontend's `flagClusters.ts`; genuinely different evidence text
    stays distinct."""
    results = [_result(1, 100, ResultOutcome.passed, 100.0)]
    flags = [
        ScorableFlag(
            severity=FlagSeverity.high,
            overridden=False,
            id=1,
            check_kind="statistical_forensics",
            problem_kind="grim_inconsistent",
            criterion_text="Results",
            evidence_excerpt="Table 3: n=15, M=2.40",
        ),
        ScorableFlag(
            severity=FlagSeverity.high,
            overridden=False,
            id=2,
            check_kind="statistical_forensics",
            problem_kind="grim_inconsistent",
            criterion_text="Results",
            evidence_excerpt="Table 3: n=15, M=2.40",
        ),
        ScorableFlag(
            severity=FlagSeverity.high,
            overridden=False,
            id=3,
            check_kind="statistical_forensics",
            problem_kind="grim_inconsistent",
            criterion_text="Results",
            evidence_excerpt="Table 4: n=20, M=3.10",
        ),
    ]
    scoring = score_check_run(results, flags, get_settings())
    assert scoring.unresolved_high_flag_count == 2


def test_flag_deduction_is_capped():
    settings = get_settings()
    results = [_result(1, 100, ResultOutcome.passed, 100.0)]
    # 50 DISTINCT findings (real, unique ids -- a real `Flag.id` is always
    # unique, unlike this test's own default before BUG-150's per-finding
    # deduction made an unset `id` collapse every no-identity flag into one
    # shared singleton) would deduct far past the cap without one.
    flags = [ScorableFlag(severity=FlagSeverity.low, overridden=False, id=i) for i in range(50)]
    scoring = score_check_run(results, flags, settings)
    assert scoring.flag_deduction == settings.scoring_flag_deduction_cap
    assert scoring.composite_score == 100.0 - settings.scoring_flag_deduction_cap


def test_multiple_anchors_on_one_finding_deduct_once_not_per_anchor():
    """BUG-150 (backend-critic, second pass): below the cap, the composite
    score itself must not move purely because one real fact happened to
    produce 2+ anchor rows instead of 1 -- the identical multiplicity
    defect the ticket names for the verdict gate, just below the point
    where the cap happened to hide it for the deduction sum."""
    settings = get_settings()
    results = [_result(1, 100, ResultOutcome.passed, 100.0)]
    one_row = [
        ScorableFlag(
            severity=FlagSeverity.high,
            overridden=False,
            id=1,
            check_kind="originality_reuse",
            problem_kind="reuse_exact_duplicate_passage",
            matched_ref=99,
        ),
    ]
    two_rows_same_finding = [
        ScorableFlag(
            severity=FlagSeverity.high,
            overridden=False,
            id=i,
            check_kind="originality_reuse",
            problem_kind="reuse_exact_duplicate_passage",
            matched_ref=99,
        )
        for i in (1, 2)
    ]
    scoring_one = score_check_run(results, one_row, settings)
    scoring_two = score_check_run(results, two_rows_same_finding, settings)
    assert scoring_one.flag_deduction == settings.scoring_flag_deduction_high
    assert scoring_two.flag_deduction == scoring_one.flag_deduction  # same fact, same deduction
    assert scoring_two.composite_score == scoring_one.composite_score


def test_a_finding_deducts_at_its_worst_members_severity():
    """A finding whose own anchors happen to carry mixed severities (the
    identity key doesn't forbid this) deducts once, at the WORST severity
    among its members -- the same "worst wins" rule the report screen's
    own `flagClusters.ts::worstSeverity` already uses to badge it."""
    settings = get_settings()
    results = [_result(1, 100, ResultOutcome.passed, 100.0)]
    flags = [
        ScorableFlag(
            severity=FlagSeverity.med,
            overridden=False,
            id=1,
            check_kind="originality_reuse",
            problem_kind="reuse_exact_duplicate_passage",
            matched_ref=99,
        ),
        ScorableFlag(
            severity=FlagSeverity.high,
            overridden=False,
            id=2,
            check_kind="originality_reuse",
            problem_kind="reuse_exact_duplicate_passage",
            matched_ref=99,
        ),
    ]
    scoring = score_check_run(results, flags, settings)
    assert scoring.flag_deduction == settings.scoring_flag_deduction_high
    assert scoring.unresolved_high_flag_count == 1


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
