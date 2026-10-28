"""Saved F6 state has the same bounded meaning in private/public exports."""

import pytest

from app.models.enums import ResultOutcome
from app.report.schemas import ReportOut
from app.report.statistical_scope import statistical_check_scope
from app.share.service import _to_public_report


def scope_report(state):
    return ReportOut(
        check_run_id=1,
        manuscript_id=1,
        manuscript_group_label="Synthetic statistical-scope control",
        manuscript_original_filename="scope-control.docx",
        rubric_title="Synthetic format",
        status="needs_review",
        composite_score=None,
        thresholds={"ready_min_score": 85, "not_ready_max_score": 60},
        reason="Review the recorded evidence and assessment limits.",
        flag_deduction=0,
        unresolved_high_flag_count=0,
        llm_mode="fake",
        results=[],
        statistical_check_scope={"state": state, "detail": {"private_prompt": "private"}},
        historical_native_math_unverified=True,
    )


def test_actual_saved_enum_maps_to_processing_completion():
    assert statistical_check_scope([ResultOutcome.passed]).state == "completed"


@pytest.mark.parametrize("state", ["completed", "not_applicable", "incomplete", "not_recorded"])
def test_public_projection_deliberately_publishes_only_safe_state(state):
    report = scope_report(state)
    public = _to_public_report(report).model_dump()
    assert public["statistical_check_scope"] == {
        "state": state, "unassessed_descriptive_rows": 0
    }
    assert public["historical_native_math_unverified"] is True
    assert "integrity_check_status" not in public
    assert "pending_review_count" not in public
    assert "manuscript_id" not in public
    assert "private_prompt" not in str(public)


@pytest.mark.parametrize("count", [0, 1])
def test_concern_count_does_not_change_saved_processing_status(count):
    report = scope_report("completed")
    report.unresolved_high_flag_count = count
    public = _to_public_report(report)
    assert public.statistical_check_scope.state == "completed"
    assert public.unresolved_high_flag_count == count
