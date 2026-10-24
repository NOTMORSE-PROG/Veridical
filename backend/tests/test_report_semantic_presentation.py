"""V-081 F3/F8: expose saved basis without relaxing grading or public privacy."""

import pytest

from app.checks.escalation import EscalatedItem
from app.report.export import (
    _criterion_flowable,
    _pending_review_reason,
    _pending_semantic_basis_flowables,
    _semantic_support_labels,
    _styles,
)
from app.report.schemas import CriterionLevelOut, EvidenceItem, ResolutionOut
from app.report.service import _to_escalated_out
from app.share.service import _to_public_report
from tests.test_report_export_perf import _sixty_criterion_report


@pytest.mark.parametrize("resolution", ["mark_fail", "needs_document", "accept_majority", "mark_level"])
def test_instructor_attribution_is_inside_its_splittable_reason_not_a_trailing_caption(resolution):
    row = _sixty_criterion_report().report.results[0]
    reason = "Saved <instructor> & reason. " * 100
    row.resolution = ResolutionOut(type=resolution, reason=reason, ai_majority_verdict="Recorded suggestion")
    if resolution == "mark_level":
        row.level = CriterionLevelOut(ordinal=0, name="Recorded <level>", points=2, max_points=5)
    parts = _criterion_flowable(row, _styles("Helvetica"))
    reason_parts = [part for part in parts if reason.strip() in part.getPlainText()]
    assert len(reason_parts) == 1
    assert reason_parts[0].getPlainText().startswith("Resolved by instructor.")
    assert not reason_parts[0].getKeepWithNext()
    assert all(part.getPlainText() != "Resolved by instructor" for part in parts)
    assert sum(part.getPlainText().count("Resolved by instructor") for part in parts) == 1
    assert any("Recorded suggestion" in part.getPlainText() for part in parts)
    if resolution == "needs_document":
        assert any("No grade recorded." in part.getPlainText() for part in parts)
    if resolution == "mark_level":
        assert any("Marked Recorded <level>" in part.getPlainText() for part in parts)
        assert any(part.getPlainText() == "2/5 pts" for part in parts)


@pytest.mark.parametrize("excluded", [False, True])
def test_document_exclusion_is_named_only_from_its_recorded_resolution(excluded):
    row = _sixty_criterion_report().report.results[0]
    row.outcome = "not_applicable"
    row.score = None
    row.resolution = (
        ResolutionOut(
            type="needs_document",
            reason="Separate <demonstration> & supporting material required.",
            ai_majority_verdict=None,
        )
        if excluded
        else None
    )
    text = " ".join(part.getPlainText() for part in _criterion_flowable(row, _styles("Helvetica")))
    if excluded:
        assert "Excluded from readiness calculation: needs another document." in text
        assert "No grade recorded." in text
        assert "Separate <demonstration> & supporting material required." in text
    else:
        assert "Excluded from readiness calculation: needs another document" not in text
        assert "No grade recorded." not in text


@pytest.mark.parametrize("recorded", [None, "The saved representative explanation."])
def test_escalated_projection_uses_only_actual_reasoning(recorded):
    detail = {
        "verdict": "pass",
        "proposed_reasoning": "MUST_NOT_SUBSTITUTE",
        "raw_audit": "PRIVATE",
    }
    if recorded is not None:
        detail["reasoning"] = recorded
    item = EscalatedItem(
        check_result_id=1,
        criterion_id=2,
        criterion_text="Custom criterion",
        weight=1,
        agreement=0.667,
        votes=["pass", "fail", "pass"],
        reason=None,
        detail=detail,
        review_reason="low_confidence",
    )
    projected = _to_escalated_out(item)
    assert projected.reasoning == recorded
    assert projected.ai_majority_verdict == "pass"
    assert projected.reason is None
    assert "MUST_NOT_SUBSTITUTE" not in projected.model_dump_json()
    assert "PRIVATE" not in projected.model_dump_json()


@pytest.mark.parametrize("unverified", [False, True])
def test_public_support_fact_is_explicit_and_private_fields_stay_private(unverified):
    report = _sixty_criterion_report().report
    report.historical_semantic_support_unverified = unverified
    public = _to_public_report(report).model_dump()
    assert public["semantic_support_unverified"] is unverified
    assert "historical_semantic_support_unverified" not in public
    assert "pending_review_count" not in public
    assert "integrity_check_status" not in public
    assert all("resolution" not in row for row in public["results"])
    assert all("raw_audit" not in row for row in public["results"])


def test_pending_copy_does_not_erase_tentative_votes_or_invent_an_ungraded_cause():
    assert _pending_review_reason("escalated") == "No grade has been accepted for this criterion."
    assert _pending_review_reason("quota_exhausted") == _pending_review_reason("escalated")
    assert _pending_review_reason("api_down") == _pending_review_reason("escalated")
    assert _pending_review_reason("unverifiable") == _pending_review_reason("escalated")
    labels = _semantic_support_labels()
    assert labels["title"] == "AI evidence support needs review"
    assert "saved record" in labels["body"]
    assert "predate" not in labels["body"] and "Run a new check" not in labels["body"]


def test_resolved_export_does_not_invent_a_threshold_failure():
    row = _sixty_criterion_report().report.results[0]
    row.resolution = ResolutionOut(
        type="mark_fail",
        reason="The instructor checked the actual requirement.",
        ai_majority_verdict="pass",
    )
    # Flowable text inspection exercises export composition without authoring a PDF.
    text = " ".join(part.getPlainText() for part in _criterion_flowable(row, _styles("Helvetica")))
    assert "Marked Fail. The instructor checked the actual requirement." in text
    assert "Recorded AI suggestion: pass." in text
    assert "agreement threshold" not in text and "auto-decide" not in text


@pytest.mark.parametrize("reasoning", [None, " \n\t "])
def test_system_note_does_not_replace_missing_academic_basis(reasoning):
    row = _sixty_criterion_report().report.results[0]
    row.kind, row.basis, row.outcome = "semantic", "llm", "escalated"
    row.reason, row.reasoning, row.evidence = "No winning grade", reasoning, []
    parts = _pending_semantic_basis_flowables(row, _styles("Helvetica"))
    text = " ".join(part.getPlainText() for part in parts)
    assert "No AI explanation is available for this review item." in text
    assert "No located excerpt is available for this review item." in text
    assert row.reason == "No winning grade"


def test_pending_basis_preserves_real_explanation_and_unlocated_excerpt():
    row = _sixty_criterion_report().report.results[0]
    row.kind, row.basis, row.outcome = "semantic", "llm", "escalated"
    row.reason, row.reasoning = "No winning grade", "The actual saved explanation."
    row.evidence = [EvidenceItem(quote="The actual saved excerpt.", anchor="  ")]
    parts = _pending_semantic_basis_flowables(row, _styles("Helvetica"))
    text = " ".join(part.getPlainText() for part in parts)
    assert row.reasoning in text and row.evidence[0].quote in text
    assert "No manuscript location is available for this excerpt." in text
    assert "No AI explanation" not in text and "No located excerpt" not in text
