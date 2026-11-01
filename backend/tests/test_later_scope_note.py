"""BUG-611: a grade saved as "supported" before the whole-paper rule gets a
note at report time, labelled as added later. Nothing stored changes."""

import copy
from types import SimpleNamespace

import pytest

from app.models.enums import CheckKind, ResultOutcome
from app.report.automatic_grade import automatic_grade_caution
from app.report.service import later_note

GRAMMAR = SimpleNamespace(
    text=(
        "Grammar, Spelling, Writing Mechanics (punctuation, italics, capitalization, etc.): "
        "Absence of mechanical and grammatical errors."
    ),
    evidence="A reader examines the manuscript for grammar and spelling errors.",
    levels=[
        {"name": "BEGINNER", "level": 1, "points": 1.0, "descriptor": "Many errors."},
        {
            "name": "PROFICIENT",
            "level": 3,
            "points": 3.0,
            "descriptor": "There are occasional errors, but they don't obscure meaning.",
        },
    ],
)
PURPOSE = SimpleNamespace(
    text="Purpose: Clarity of the paper's central purpose.",
    evidence="A reader checks the statement of purpose.",
    levels=[
        {"name": "BEGINNER", "level": 1, "points": 1.0, "descriptor": "Unclear."},
        {
            "name": "PROFICIENT",
            "level": 3,
            "points": 3.0,
            "descriptor": "The paper's purpose is clearly stated in a single sentence.",
        },
    ],
)


def saved(outcome=ResultOutcome.passed, kind=CheckKind.semantic, **detail):
    base = {
        "basis": "llm",
        "verdict": "PROFICIENT",
        "votes": ["PROFICIENT", "PROFICIENT"],
        "support_check": {"status": "supported", "reason": "The sentence is grammatical."},
    }
    base.update(detail)
    return SimpleNamespace(outcome=outcome, kind=kind), base


def test_an_old_supported_whole_paper_grade_gets_a_note_that_says_it_came_later():
    result, detail = saved()
    note = later_note(result, GRAMMAR, detail)
    assert note.startswith("This note was added later by a newer VERIDICAL rule, not by the original check.")
    assert "grade and readiness band are unchanged" in note
    assert "whole paper" in note


def test_the_note_changes_nothing_stored():
    result, detail = saved()
    before = (result.outcome, copy.deepcopy(detail))
    later_note(result, GRAMMAR, detail)
    assert (result.outcome, detail) == before


@pytest.mark.parametrize(
    ("case", "criterion", "outcome", "kind", "detail"),
    [
        ("a locatable level", PURPOSE, ResultOutcome.passed, CheckKind.semantic, {}),
        (
            "the check already guarded it",
            GRAMMAR,
            ResultOutcome.passed,
            CheckKind.semantic,
            {"support_check": {"status": "unsupported", "scope_guard": "x"}},
        ),
        (
            "the check did not support it",
            GRAMMAR,
            ResultOutcome.passed,
            CheckKind.semantic,
            {"support_check": {"status": "unsupported"}},
        ),
        (
            "an instructor judged it",
            GRAMMAR,
            ResultOutcome.passed,
            CheckKind.semantic,
            {"resolution": {"type": "mark_level"}},
        ),
        ("it was escalated", GRAMMAR, ResultOutcome.escalated, CheckKind.semantic, {}),
        ("a rule, not the model", GRAMMAR, ResultOutcome.passed, CheckKind.structural, {}),
        (
            "supported but guarded",
            GRAMMAR,
            ResultOutcome.passed,
            CheckKind.semantic,
            {"support_check": {"status": "supported", "scope_guard": "x"}},
        ),
        (
            "not graded by the model",
            GRAMMAR,
            ResultOutcome.passed,
            CheckKind.semantic,
            {"basis": "structural-alignment"},
        ),
    ],
)
def test_no_note_where_the_rule_does_not_apply(case, criterion, outcome, kind, detail):
    result, saved_detail = saved(outcome=outcome, kind=kind, **detail)
    assert later_note(result, criterion, saved_detail) is None, case


def row(**fields):
    base = dict(
        decision_basis=None,
        support_caution=None,
        support_caution_scope=False,
        later_note=None,
        agreeing_passes=2,
        grading_passes=2,
    )
    return SimpleNamespace(**{**base, **fields})


def test_the_note_stands_where_a_caution_would_and_a_saved_caution_comes_first():
    note = "This note was added later by a newer VERIDICAL rule, not by the original check."
    assert automatic_grade_caution(row(later_note=note)) == note
    with_caution = row(later_note=note, support_caution="The quote shows the topic only.")
    assert automatic_grade_caution(with_caution).endswith("The quote shows the topic only.")
    # A grade the instructor set has no automatic-grade lines, so no note.
    assert automatic_grade_caution(row(later_note=note, agreeing_passes=None)) is None


def test_the_pdf_export_prints_the_note():
    from app.report.export import build_report_pdf
    from tests.test_report_export_details import _data, _text

    data = _data()
    note = "This note was added later by a newer VERIDICAL rule, not by the original check."
    first = data.report.results[0].model_copy(
        update={"later_note": note, "agreeing_passes": 2, "grading_passes": 2, "resolution": None}
    )
    report = data.report.model_copy(update={"results": [first, *data.report.results[1:]]})
    text = _text(build_report_pdf(data.model_copy(update={"report": report})))
    # The note wraps across lines in the PDF.
    assert "This note was added later by a newer VERIDICAL rule" in " ".join(text.split())


def test_the_note_reaches_the_report_row_and_leaves_the_grade_alone():
    from app.models import CheckResult, Criterion
    from app.models.enums import CriterionType
    from app.report.service import _to_criterion_result

    criterion = Criterion(
        id=7,
        text=GRAMMAR.text,
        evidence=GRAMMAR.evidence,
        levels=GRAMMAR.levels,
        type=CriterionType.semantic,
        weight=1,
    )
    _, detail = saved(
        level={"name": "PROFICIENT", "ordinal": 3, "points": 3.0, "max_points": 3.0},
        evidence=[],
        reasoning="The quoted sentence is free of errors.",
        grading_reasoning="Errors are occasional across the chapters read.",
    )
    stored = copy.deepcopy(detail)
    result = CheckResult(
        id=1,
        kind=CheckKind.semantic,
        outcome=ResultOutcome.passed,
        score=3,
        detail=detail,
        judgment_revision=0,
    )
    out = _to_criterion_result(result, criterion, importance="high")
    assert out.later_note and out.later_note.startswith("This note was added later by a newer VERIDICAL rule")
    assert (out.outcome, out.score, result.detail) == (ResultOutcome.passed, 3, stored)
    # The quote check's "supported" sentence would contradict the note, so the
    # grader's own reasoning is shown, as it is for grades checked today.
    assert out.reasoning == "Errors are occasional across the chapters read."

    result.detail = {**detail, "support_check": {"status": "unsupported"}}
    unnoted = _to_criterion_result(result, criterion, importance="high")
    assert unnoted.later_note is None
    assert unnoted.reasoning == "The quoted sentence is free of errors."
