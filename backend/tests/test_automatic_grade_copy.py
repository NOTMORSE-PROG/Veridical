"""The PDF export and the adviser view say how an automatic grade was chosen,
in the same words as the report screen, and say nothing once the instructor
has set the grade."""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.report.automatic_grade import _copy, automatic_grade_caution, automatic_grade_lines
from app.report.export import build_report_pdf
from app.report.schemas import ResolutionOut
from app.share.service import _to_public_report
from tests.test_report_export_details import _data, _text

FRONTEND_COPY = Path(__file__).parents[2] / "frontend/src/domain/automaticGrade.ts"
CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "automatic_grade_cases.json").read_text("utf-8")
)
CAUTION = "The quote shows the method, not the results."


def _row(**fields):
    base = dict(
        decision_basis=None, support_caution=None, agreeing_passes=None, grading_passes=None
    )
    return SimpleNamespace(**{**base, **fields})


def test_the_backend_sentences_match_the_report_screen():
    source = FRONTEND_COPY.read_text(encoding="utf-8")
    for key, sentence in _copy().items():
        match = re.search(rf'^\s*{key}: "(.*)",$', source, re.MULTILINE)
        assert match is not None, key
        assert match.group(1) == sentence


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_lines_and_caution_follow_the_shared_cases(case):
    row = _row(**case["row"])
    assert automatic_grade_lines(row) == case["lines"]
    assert automatic_grade_caution(row) == case["caution"]


def _with_first_result(**fields):
    data = _data()
    first = data.report.results[0].model_copy(update=fields)
    report = data.report.model_copy(update={"results": [first, *data.report.results[1:]]})
    return data.model_copy(update={"report": report})


def test_the_pdf_export_shows_how_an_automatic_grade_was_chosen():
    data = _with_first_result(decision_basis="single_verified_pass", support_caution=CAUTION)
    text = _text(build_report_pdf(data))
    assert _copy()["single_verified_pass"] in text
    assert f"Evidence check not confirmed: {CAUTION}" in text


def test_the_pdf_export_never_says_no_grade_was_assigned_beside_a_kept_grade():
    data = _with_first_result(
        agreeing_passes=3,
        grading_passes=3,
        support_caution="Review the criterion directly; no automatic grade was assigned.",
    )
    text = " ".join(_text(build_report_pdf(data)).split())
    assert "Graded automatically: 3 of 3 grading passes chose this grade." in text
    assert "Evidence check not confirmed: Review the criterion directly." in text
    assert "no automatic grade was assigned" not in text


def test_the_adviser_view_carries_the_basis_and_caution():
    data = _with_first_result(
        decision_basis="middle_of_three",
        support_caution=CAUTION,
        agreeing_passes=1,
        grading_passes=3,
    )
    public = _to_public_report(data.report).results[0]
    assert public.decision_basis == "middle_of_three"
    assert public.support_caution == CAUTION
    assert (public.agreeing_passes, public.grading_passes) == (1, 3)


def test_the_adviser_view_never_shows_an_instructor_set_grade_as_automatic():
    data = _with_first_result(
        decision_basis="middle_of_three",
        support_caution=CAUTION,
        agreeing_passes=1,
        grading_passes=3,
    )
    # A row that carries both an instructor's resolution and the automatic
    # fields; the report never builds one today, but the adviser view must
    # not depend on that.
    first = data.report.results[0].model_copy(
        update={
            "resolution": ResolutionOut(
                type="override", reason="Checked by hand.", ai_majority_verdict="pass"
            )
        }
    )
    report = data.report.model_copy(update={"results": [first, *data.report.results[1:]]})
    public = _to_public_report(report).results[0]
    assert public.resolved is True
    assert public.decision_basis is None
    assert public.support_caution is None
    assert (public.agreeing_passes, public.grading_passes) == (None, None)
