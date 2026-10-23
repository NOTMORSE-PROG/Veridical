"""F8.4/F8.5: the printed decision and incomplete assessment must be clear together."""

from datetime import UTC, datetime

import fitz
import pytest

from app.report.export import build_report_pdf
from app.report.schemas import CriterionResultOut, LevelledRatingOut, ReportExportData, ReportOut


def row(criterion_id=1, *, pending=True):
    return CriterionResultOut(
        criterion_id=criterion_id,
        text=f"Custom criterion {criterion_id}",
        type="semantic",
        kind="semantic",
        weight=1,
        weight_importance="med",
        outcome="escalated" if pending else "passed",
        score=None if pending else 25,
        basis="llm",
        anchor=None,
        reasoning=None,
        reason=None,
        evidence=[],
        resolution=None,
    )


def report(**overrides):
    values = dict(
        check_run_id=1,
        manuscript_id=1,
        manuscript_group_label="Export control",
        manuscript_original_filename="manuscript.pdf",
        rubric_title="Custom format",
        status="needs_review",
        composite_score=None,
        thresholds={"ready_min_score": 85, "not_ready_max_score": 60},
        reason="Evidence needs review.",
        flag_deduction=0,
        unresolved_high_flag_count=0,
        llm_mode="real",
        results=[row()],
        decision="returned",
        decided_at=datetime(2026, 10, 23, tzinfo=UTC),
        decision_note="Review the original sources before recording further judgments.",
        pending_review_count=1,
        rubric_is_current=True,
        assessment_provenance="recorded_decision",
    )
    values.update(overrides)
    return ReportOut(**values)


def rendered(value):
    return fitz.open(
        stream=build_report_pdf(ReportExportData(report=value, flags=[], archive_size_n=0)),
        filetype="pdf",
    )


def text(document):
    return " ".join(page.get_text().replace("\n", " ") for page in document)


def test_return_is_on_the_first_page_before_the_distinct_system_band():
    with rendered(report()) as document:
        first = document[0].get_text()
        assert first.index("Instructor decision") < first.index("Returned for revision")
        assert first.index("Returned for revision") < first.index("System readiness")
        assert "1 criterion remains unresolved" in first
        assert "Returning did not grade or exclude them" in first
        assert "Composite detail: not available" in first
        assert "Reopen the report before recording or changing their judgments" in text(document)


def test_mixed_composite_and_rubric_formula_keep_their_actual_partial_coverage():
    value = report(
        results=[row(1, pending=False), row(2)],
        composite_score=25,
        levelled_rating=LevelledRatingOut(
            achieved_points=1, max_points=8, rating_percent=12.5, n_decided=1, n_levelled=2
        ),
    )
    with rendered(value) as document:
        extracted = text(document)
        assert "Recorded composite detail: 25.0% (partial criterion coverage" in extracted
        assert "1 of 2 criteria remain unresolved" in extracted
        assert "1/8 points = 12.5%" in extracted
        assert "1 of 2 levelled criteria with recorded grades" in extracted
        assert "not the VERIDICAL readiness verdict" in extracted


def test_recomputed_breakdown_is_not_labelled_as_the_recorded_decision_calculation():
    with rendered(
        report(composite_score=25, assessment_provenance="recomputed_without_recorded_breakdown")
    ) as document:
        extracted = text(document)
        assert "Recomputed composite detail under the current policy: 25.0%" in extracted
        assert "Recorded composite detail" not in extracted


def test_undecided_partial_export_does_not_invent_a_return_or_reopen_instruction():
    with rendered(
        report(decision=None, decided_at=None, decision_note=None, assessment_provenance="current")
    ) as document:
        extracted = text(document)
        assert "Instructor decision Not recorded" in extracted
        assert "DRAFT - NO DECISION RECORDED" in extracted
        assert "Returned for revision" not in extracted
        assert "Reopen the report before" not in extracted


def test_zero_pending_return_does_not_claim_incomplete_judgments_or_require_a_note():
    with rendered(
        report(
            results=[row(pending=False)],
            composite_score=25,
            pending_review_count=0,
            decision_note=None,
        )
    ) as document:
        extracted = text(document)
        assert "Returned for revision" in document[0].get_text()
        assert "remain unresolved" not in extracted
        assert "partial criterion coverage" not in extracted
        assert "Other criteria remain ungraded" not in extracted


@pytest.mark.parametrize(
    "note",
    [
        "A reason with manuscript characters: α, β and café.",
        "Long reason: " + "Review the recorded evidence. " * 30,
    ],
)
def test_front_summary_link_reaches_the_complete_decision_note(note):
    with rendered(report(decision_note=note)) as document:
        links = [link for link in document[0].get_links() if link["kind"] == fitz.LINK_GOTO]
        assert links, "the decision summary must have a real internal destination"
        destination = links[0]["page"]
        assert "Final Decision" in document[destination].get_text()
        assert " ".join(note.split()) in " ".join(text(document).split())


def test_long_decision_reason_is_labelled_on_each_continuation_page():
    note = "LONG-NOTE-CONTROL αβ café. " * 900
    with rendered(report(decision_note=note)) as document:
        note_pages = [
            page.get_text() for page in document if "LONG-NOTE-CONTROL" in page.get_text()
        ]
        assert len(note_pages) > 1
        assert all("Decision reason" in page for page in note_pages)
        assert text(document).count("LONG-NOTE-CONTROL") == 900


@pytest.mark.parametrize("decision", [None, "returned"])
@pytest.mark.parametrize(
    ("kind", "outcome", "reason"),
    [
        ("structural", "unverifiable", "Reference <rows> & source inventory cannot be bound."),
        ("structural", "unverifiable", None),
        ("structural", "unverifiable", " \n\t "),
        ("semantic", "escalated", "The recorded model votes did not reach agreement."),
        ("semantic", "escalated", "The saved passages do not establish support for a grade."),
        ("semantic", "quota_exhausted", "The recorded daily grading allowance was exhausted."),
        ("semantic", "api_down", "The grading provider was unavailable during this attempt."),
        ("semantic", "api_down", None),
    ],
)
def test_ungraded_export_preserves_only_the_recorded_cause(decision, kind, outcome, reason):
    value = report(decision=decision)
    item = value.results[0]
    item.kind, item.basis = kind, "rule" if kind == "structural" else "llm"
    item.outcome, item.reason = outcome, reason
    before = value.model_dump_json()
    with rendered(value) as document:
        content = " ".join(text(document).split())
        assert "No grade has been accepted for this criterion." in content
        assert "This criterion was not graded by AI" not in content
        assert "capacity spent" not in content
        if reason and reason.strip():
            assert " ".join(reason.split()) in content
        assert (
            "Needs Your Review" if decision is None else "Unresolved At Time Of Decision"
        ) in content
        if kind == "semantic" and outcome == "escalated":
            assert "No AI explanation is available for this review item." in content
    assert value.model_dump_json() == before


def test_long_structural_pending_reason_keeps_escaped_text_and_continuation():
    value = report()
    item = value.results[0]
    item.kind, item.basis, item.outcome = "structural", "rule", "unverifiable"
    item.reason = (
        "CAUSE_START <rows> & " + "Source inventory remains incomplete. " * 500 + "CAUSE_END"
    )
    before = value.model_dump_json()
    with rendered(value) as document:
        content = " ".join(text(document).split())
        assert len(document) > 1
        assert "CAUSE_START <rows> &" in content and "CAUSE_END" in content
        assert "Criterion #1 (continued)" in content
        assert "capacity spent" not in content
    assert value.model_dump_json() == before
