"""V-081 F3/F8: expose saved basis without relaxing grading or public privacy."""

import pytest

from app.checks.escalation import EscalatedItem
from app.report.export import _pending_review_reason, _semantic_support_labels
from app.report.service import _to_escalated_out
from app.share.service import _to_public_report
from tests.test_report_export_perf import _sixty_criterion_report


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


def test_pending_copy_does_not_erase_tentative_votes_or_rename_not_graded():
    assert _pending_review_reason("escalated") == "No grade has been accepted for this criterion."
    assert "not graded" in _pending_review_reason("quota_exhausted")
    assert "not graded" in _pending_review_reason("api_down")
    labels = _semantic_support_labels()
    assert labels["title"] == "AI evidence support needs review"
    assert "saved record" in labels["body"]
    assert "predate" not in labels["body"] and "Run a new check" not in labels["body"]
