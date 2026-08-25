"""V-069 unit tests: `app.checks.levels` — the shared verdict-string ->
(outcome, score, level) mapping, pure functions, no DB.
"""

from dataclasses import dataclass, field

from app.checks.levels import (
    is_levelled,
    match_level,
    match_level_by_ordinal,
    max_points,
    outcome_and_score,
)
from app.models.enums import ResultOutcome


@dataclass
class FakeCriterion:
    id: int = 1
    levels: list[dict] | None = field(default=None)


TIP_SCALE = [
    {"level": 1, "name": "Beginner", "descriptor": "no clear structure", "points": 1},
    {"level": 2, "name": "Acceptable", "descriptor": "states the topic", "points": 2},
    {"level": 3, "name": "Proficient", "descriptor": "states and previews", "points": 3},
    {"level": 4, "name": "Exemplary", "descriptor": "engaging and complete", "points": 4},
]


def test_is_levelled_false_for_none_and_empty():
    assert is_levelled(None) is False
    assert is_levelled(FakeCriterion(levels=None)) is False
    assert is_levelled(FakeCriterion(levels=[])) is False


def test_is_levelled_true_for_a_real_scale():
    assert is_levelled(FakeCriterion(levels=TIP_SCALE)) is True


def test_max_points_is_none_for_non_levelled():
    assert max_points(FakeCriterion(levels=None)) is None


def test_max_points_is_the_highest_rung():
    assert max_points(FakeCriterion(levels=TIP_SCALE)) == 4.0


def test_match_level_finds_the_named_rung():
    criterion = FakeCriterion(levels=TIP_SCALE)
    match = match_level(criterion, "Proficient")
    assert match is not None
    assert match.name == "Proficient"
    assert match.ordinal == 3
    assert match.points == 3.0
    assert match.max_points == 4.0


def test_match_level_returns_none_for_an_unrecognized_string():
    criterion = FakeCriterion(levels=TIP_SCALE)
    assert match_level(criterion, "proficient") is None  # case-sensitive, exact match only
    assert match_level(criterion, "pass") is None
    assert match_level(criterion, "Level 3") is None


def test_match_level_by_ordinal():
    criterion = FakeCriterion(levels=TIP_SCALE)
    match = match_level_by_ordinal(criterion, 1)
    assert match is not None
    assert match.name == "Beginner"
    assert match.points == 1.0
    assert match_level_by_ordinal(criterion, 99) is None
    assert match_level_by_ordinal(criterion, None) is None


# --- outcome_and_score: non-levelled path, must be byte-identical to the
# old _OUTCOME_BY_VERDICT/_SCORE_BY_VERDICT lookup (AC3) --------------------


def test_outcome_and_score_pass_partial_fail_unaffected():
    non_levelled = FakeCriterion(levels=None)
    assert outcome_and_score(non_levelled, "pass") == (ResultOutcome.passed, 100.0, None)
    assert outcome_and_score(non_levelled, "partial") == (ResultOutcome.passed, 50.0, None)
    assert outcome_and_score(non_levelled, "fail") == (ResultOutcome.failed, 0.0, None)


def test_outcome_and_score_with_criterion_none_matches_non_levelled():
    assert outcome_and_score(None, "pass") == (ResultOutcome.passed, 100.0, None)


def test_outcome_and_score_unrecognized_verdict_escalates_not_keyerror():
    # V-069 hardening side effect: `GradeVerdict.verdict` was relaxed from
    # Literal["pass","partial","fail"] to a plain string, so a garbage
    # value is now reachable where it previously couldn't validate at all.
    outcome, score, level = outcome_and_score(FakeCriterion(levels=None), "maybe")
    assert outcome == ResultOutcome.escalated
    assert score is None
    assert level is None


# --- outcome_and_score: levelled path ---------------------------------------


def test_outcome_and_score_levelled_criterion_maps_level_name_to_score():
    criterion = FakeCriterion(levels=TIP_SCALE)
    outcome, score, level = outcome_and_score(criterion, "Proficient")
    assert outcome == ResultOutcome.passed
    assert score == 75.0  # 3/4 * 100
    assert level.name == "Proficient"
    assert level.ordinal == 3


def test_outcome_and_score_levelled_criterion_top_rung_is_100():
    criterion = FakeCriterion(levels=TIP_SCALE)
    outcome, score, level = outcome_and_score(criterion, "Exemplary")
    assert score == 100.0
    assert level.ordinal == 4


def test_outcome_and_score_levelled_criterion_bottom_rung_is_not_zero():
    # A levelled criterion's lowest rung is still a real, graded score --
    # never conflated with "fail" (there is no pass/fail concept on a
    # pure point scale).
    criterion = FakeCriterion(levels=TIP_SCALE)
    outcome, score, level = outcome_and_score(criterion, "Beginner")
    assert outcome == ResultOutcome.passed
    assert score == 25.0  # 1/4 * 100


def test_outcome_and_score_all_zero_points_scale_escalates_never_silently_decided():
    """`backend-critic` finding: a degenerate all-zero-points scale (now
    blocked at the source by `ParsedLevel`'s own validator, guarded here
    too for any criterion that predates that check) used to return
    `(passed, score=None, match)` -- a row that LOOKS decided (a named
    level shows on screen) but is silently excluded from the composite
    with no reason ever surfaced. Every other "can't score this" state in
    this codebase is honest about being undecided; this must be too."""
    zero_scale = [
        {"level": 1, "name": "None", "descriptor": "x", "points": 0},
        {"level": 2, "name": "Some", "descriptor": "y", "points": 0},
    ]
    criterion = FakeCriterion(levels=zero_scale)
    outcome, score, level = outcome_and_score(criterion, "Some")
    assert outcome == ResultOutcome.escalated
    assert score is None
    assert level is None


def test_outcome_and_score_levelled_criterion_rejects_pass_fail_vocabulary():
    # A model that ignores the per-criterion scale instruction and answers
    # "pass" for a levelled criterion must escalate, never silently score
    # as if "pass" were one of this criterion's own levels.
    criterion = FakeCriterion(levels=TIP_SCALE)
    outcome, score, level = outcome_and_score(criterion, "pass")
    assert outcome == ResultOutcome.escalated
    assert score is None
    assert level is None
