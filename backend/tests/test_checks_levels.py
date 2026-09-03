"""V-069 unit tests: `app.checks.levels` — the shared verdict-string ->
(outcome, score, level) mapping, pure functions, no DB.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.checks.levels import (
    is_levelled,
    level_scale_prompt_fragment,
    match_level,
    match_level_by_ordinal,
    max_points,
    outcome_and_score,
)
from app.models.enums import ResultOutcome

# BUG-146's own reproduction artifact -- local-only, same D-007 convention
# `test_ingest_pdf.py`'s `demo_pdf_only` already uses for the owner's
# proposal PDF (gitignored project-wide, `*.pdf`, never committed).
RUBRIC_PDF = Path(__file__).resolve().parents[2] / "Rubric-for-Oral-Presentation.pdf"
rubric_pdf_only = pytest.mark.skipif(
    not RUBRIC_PDF.exists(),
    reason="BUG-146's reproduction rubric is local-only (D-007); run this suite locally",
)


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


# --- level_scale_prompt_fragment: BUG-135 regression -----------------------
# A real rubric (this project's own TIP oral-presentation one) names each
# rung "Exemplary 4"/"Proficient 3" style -- the name already ends in a
# digit that duplicates its own ordinal. The prompt must not glue the
# ordinal onto that name with no delimiter, or the model can't tell the
# trailing "(4)" isn't part of "the exact level name" -- it echoes back
# "Exemplary 4 (4)" and match_level's deliberately-exact comparison then
# escalates every criterion on this scale for a formatting reason, not a
# real grading difficulty.

NAME_ENDS_IN_ORDINAL_SCALE = [
    {"level": 1, "name": "Beginner 1", "descriptor": "no clear structure", "points": 1},
    {"level": 2, "name": "Acceptable 2", "descriptor": "states the topic", "points": 2},
    {"level": 3, "name": "Proficient 3", "descriptor": "states and previews", "points": 3},
    {"level": 4, "name": "Exemplary 4", "descriptor": "engaging and complete", "points": 4},
]


def test_level_scale_prompt_fragment_quotes_the_name():
    fragment = level_scale_prompt_fragment(FakeCriterion(levels=TIP_SCALE))
    assert fragment is not None
    assert '"Proficient"' in fragment
    assert '"Exemplary"' in fragment


def test_level_scale_prompt_fragment_does_not_glue_ordinal_onto_a_name_that_already_ends_in_one():
    fragment = level_scale_prompt_fragment(FakeCriterion(levels=NAME_ENDS_IN_ORDINAL_SCALE))
    assert fragment is not None
    # The quoted name is reproducible verbatim by a model that follows the
    # "EXACT quoted level name" instruction...
    assert '"Exemplary 4"' in fragment
    # ...and the old ambiguous glued form must never appear again.
    assert "Exemplary 4 (4)" not in fragment


def test_level_scale_prompt_fragment_none_for_non_levelled():
    assert level_scale_prompt_fragment(FakeCriterion(levels=None)) is None


def test_match_level_accepts_the_verdict_the_new_prompt_actually_teaches():
    # The regression this whole bug was about: a level whose name already
    # ends in its own ordinal digit must still match on the bare quoted
    # name, with no trailing ordinal artifact.
    criterion = FakeCriterion(levels=NAME_ENDS_IN_ORDINAL_SCALE)
    match = match_level(criterion, "Exemplary 4")
    assert match is not None
    assert match.points == 4.0
    # The old bug's literal failure mode must still correctly escalate --
    # this proves the fix is in the prompt, not a loosened matcher.
    assert match_level(criterion, "Exemplary 4 (4)") is None


# --- match_level: BUG-146 regression -----------------------------------
# A real rubric laid out as a table with narrow column headers wraps a
# level name mid-word at extraction time -- sometimes a genuine line
# break `app/ingest/normalize.py` collapses to a space, sometimes (this
# project's own TIP oral-presentation rubric, `Rubric-for-Oral-
# Presentation.pdf`) a space that's literally IN the PDF's content
# stream, which no extraction setting removes. VERIDICAL's own stored
# level name ends up split ("BEGIN ER", "EXEMPLAR Y"); the model,
# despite being told to echo it character-for-character, answers with
# the correctly-spelled version -- which the OLD exact-string
# `match_level` then rejected as "unrecognized", escalating a criterion
# for a self-inflicted formatting reason, not a real grading difficulty.

SPLIT_MID_WORD_SCALE = [
    {"level": 1, "name": "BEGIN ER 1", "descriptor": "no clear structure", "points": 1},
    {"level": 2, "name": "ACCEPTABL E 2", "descriptor": "states the topic", "points": 2},
    {"level": 3, "name": "PROFICIEN T 3", "descriptor": "states and previews", "points": 3},
    {"level": 4, "name": "EXEMPLAR Y 4", "descriptor": "engaging and complete", "points": 4},
]


def test_match_level_accepts_a_correctly_spelled_verdict_against_a_split_stored_name():
    """The ticket's own reproduction, verbatim: VERIDICAL's stored name is
    corrupted ("EXEMPLAR Y 4"); the model answers with the correct
    spelling ("EXEMPLARY 4"). This must now match -- the corruption is in
    VERIDICAL's own extraction, never a reason to escalate the model's
    right answer."""
    criterion = FakeCriterion(levels=SPLIT_MID_WORD_SCALE)
    match = match_level(criterion, "EXEMPLARY 4")
    assert match is not None
    assert match.ordinal == 4
    assert match.points == 4.0
    # The stored (corrupted) name is what's displayed/persisted -- BUG-146's
    # own fix section: "display the level name honestly as the rubric
    # produced it rather than guessing a repair." This fix normalizes the
    # COMPARISON only, never the stored/returned name itself.
    assert match.name == "EXEMPLAR Y 4"


def test_match_level_also_accepts_the_verbatim_corrupted_echo():
    """A model that (correctly) followed the "echo character-for-character"
    instruction and reproduced the corrupted name verbatim must also
    match -- whitespace normalization is symmetric, not a one-way repair
    of only the model's side."""
    criterion = FakeCriterion(levels=SPLIT_MID_WORD_SCALE)
    match = match_level(criterion, "EXEMPLAR Y 4")
    assert match is not None
    assert match.ordinal == 4


def test_match_level_whitespace_tolerance_is_not_fuzzy_matching():
    """The ticket's own invariant, preserved exactly: an invented level
    name, or a name from another criterion's vocabulary, must still fail
    to match. Only whitespace differences stop mattering -- nothing else
    about the comparison loosens."""
    criterion = FakeCriterion(levels=SPLIT_MID_WORD_SCALE)
    assert match_level(criterion, "Excellent 4") is None  # invented
    assert match_level(criterion, "Proficient") is None  # wrong vocabulary
    assert match_level(criterion, "EXEMPLARY 5") is None  # wrong digit
    # Case still matters -- unchanged from before this fix (see the
    # existing `test_match_level_returns_none_for_an_unrecognized_string`).
    assert match_level(criterion, "exemplary 4") is None


def test_match_level_refuses_to_guess_between_two_names_that_collapse_the_same_way():
    """`backend-critic` finding (BUG-146 review, live-reproduced): a
    whitespace-collapsing comparison scheme can make two GENUINELY
    DIFFERENT level names on the same criterion's own scale normalize to
    the same string ("Very Good" / "VeryGood"). Before this guard,
    `match_level` returned the FIRST list match after normalization --
    silently resolving an unambiguous verdict to the WRONG rung with no
    escalation, exactly the "silently snap to the nearest-looking rung"
    this function's own docstring says never to do. Ambiguous must
    escalate, the same as unrecognized."""
    ambiguous_scale = [
        {"level": 3, "name": "Very Good", "descriptor": "x", "points": 3},
        {"level": 4, "name": "VeryGood", "descriptor": "y", "points": 4},
    ]
    criterion = FakeCriterion(levels=ambiguous_scale)
    assert match_level(criterion, "VeryGood") is None
    assert match_level(criterion, "Very Good") is None
    # A criterion whose levels don't collide is unaffected.
    assert match_level(FakeCriterion(levels=TIP_SCALE), "Proficient") is not None


@rubric_pdf_only
def test_match_level_known_answer_against_the_real_rubric_pdf():
    """Known-answer test (ticket's own explicit ask): extracts the actual
    level names from `Rubric-for-Oral-Presentation.pdf` -- the artifact
    that exposed this bug -- through the real ingestion pipeline, and
    proves a correctly-spelled verdict matches whatever VERIDICAL's own
    extraction actually produced, split mid-word or not."""
    from app.config import get_settings
    from app.ingest import pdf as pdf_ingest

    result = pdf_ingest.extract_document(str(RUBRIC_PDF), get_settings())
    full_text = " ".join(b.text for b in result.blocks)
    # The ticket's own root-cause section measured this exact split LIVE
    # against this PDF's content stream ("BEGIN ER 1" as one literal
    # extracted span) -- confirm it's still true of the real extraction,
    # so this test keeps meaning what it says rather than silently
    # passing on a since-changed extraction path.
    assert "BEGIN ER 1" in full_text
    assert "EXEMPLAR" in full_text

    # ACCEPTABLE/PROFICIENT/EXEMPLARY: a genuine line-break INSIDE the
    # table cell (ticket cause #1) -- whitespace-join reconstructs these
    # perfectly, verified live against the real PDF's own measured splits.
    corrupted_scale = [
        {"level": 2, "name": "ACCEPTABL E 2", "descriptor": "x", "points": 2},
        {"level": 3, "name": "PROFICIEN T 3", "descriptor": "x", "points": 3},
        {"level": 4, "name": "EXEMPLAR Y 4", "descriptor": "y", "points": 4},
    ]
    criterion = FakeCriterion(levels=corrupted_scale)
    assert match_level(criterion, "ACCEPTABLE 2") is not None
    assert match_level(criterion, "PROFICIENT 3") is not None
    assert match_level(criterion, "EXEMPLARY 4") is not None

    # BEGINNER: an HONEST, disclosed exception, not silently dropped. The
    # ticket's own root-cause section calls this one "different and
    # worse" -- the space is literally inside the PDF's glyph content
    # stream, extraction returns 'BEGIN' + 'ER' with a character
    # genuinely MISSING relative to "BEGINNER" (not just misplaced
    # whitespace), and closing that gap would need actual fuzzy/
    # edit-distance matching -- exactly what this ticket (and
    # `match_level`'s own docstring) rules out on purpose (charter rule
    # 1: a wrong guess must escalate, never silently snap to the
    # nearest-looking rung). This criterion correctly still escalates.
    beginner_scale = [{"level": 1, "name": "BEGIN ER 1", "descriptor": "x", "points": 1}]
    assert match_level(FakeCriterion(levels=beginner_scale), "BEGINNER 1") is None


def test_outcome_and_score_levelled_criterion_rejects_pass_fail_vocabulary():
    # A model that ignores the per-criterion scale instruction and answers
    # "pass" for a levelled criterion must escalate, never silently score
    # as if "pass" were one of this criterion's own levels.
    criterion = FakeCriterion(levels=TIP_SCALE)
    outcome, score, level = outcome_and_score(criterion, "pass")
    assert outcome == ResultOutcome.escalated
    assert score is None
    assert level is None
