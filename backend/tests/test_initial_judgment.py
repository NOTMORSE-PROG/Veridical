"""D-034 distinguishes first instructor judgment from uncertain/legacy records."""

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.audit import AuditLog
from app.models.enums import CheckKind, CriterionType, ResultOutcome
from app.models.rubric import Criterion
from app.models.run import CheckResult, ReadinessReport
from app.report.judgment_schemas import JudgmentRecordIn, JudgmentRevisionIn
from app.report.judgments import _context, _original, _recordable, history_entry


def automated():
    criterion = Criterion(
        id=5, rubric_id=3, type=CriterionType.semantic, text="Explain design", weight=1, position=0
    )
    result = CheckResult(
        id=7,
        check_run_id=10,
        criterion_id=5,
        kind=CheckKind.semantic,
        outcome=ResultOutcome.passed,
        score=Decimal("100.00"),
        judgment_revision=None,
        detail={"reasoning": "Recorded automated explanation", "verdict": "pass", "evidence": []},
    )
    return result, criterion


def test_settled_context_offers_first_judgment_with_original_facts_without_a_manual_before():
    result, criterion = automated()
    before = deepcopy(result.detail)
    context = _context(10, ReadinessReport(assessment_revision=4, decision=None), result, criterion)
    assert context.can_record and not context.can_revise and context.current is None
    assert context.unavailable_reason is None
    assert context.original_assessment.original_outcome == "passed"
    assert context.original_assessment.original_score == 100
    assert context.original_assessment.reasoning == before["reasoning"]
    assert result.detail == before and result.judgment_revision is None


@pytest.mark.parametrize(
    "outcome", ["escalated", "unverifiable", "api_down", "quota_exhausted", "not_applicable"]
)
def test_nonsettled_outcomes_do_not_gain_initial_judgment_authority(outcome):
    result, criterion = automated()
    result.outcome = ResultOutcome(outcome)
    assert not _recordable(result, criterion)


@pytest.mark.parametrize(
    "change",
    [
        {"judgment_revision": 0},
        {"judgment_revision": 1},
        {"score": None},
        {"score": Decimal("NaN")},
        {"score": Decimal("-1")},
        {"score": Decimal("101")},
        {"kind": CheckKind.citation_integrity},
        {"detail": []},
        {"detail": {"resolution": None}},
        {"detail": {"resolution": {"type": "broken"}}},
        {"detail": {"original_result": {}}},
        {"detail": {"judgment_history_incomplete": False}},
    ],
)
def test_inconsistent_or_previous_manual_records_fail_closed(change):
    result, criterion = automated()
    for key, value in change.items():
        setattr(result, key, value)
    assert not _recordable(result, criterion)


def test_not_assessable_scale_failure_and_final_decision_remain_distinct():
    result, criterion = automated()
    report = ReadinessReport(assessment_revision=0, decision="returned")
    context = _context(10, report, result, criterion)
    assert not context.can_record and context.unavailable_reason == "report_decided"
    report.decision = None
    criterion.type = CriterionType.not_assessable
    assert not _recordable(result, criterion)
    criterion.type = CriterionType.semantic
    criterion.levels = [{"level": 1, "name": "Malformed scale", "points": 0}]
    context = _context(10, report, result, criterion)
    assert not context.can_record and context.unavailable_reason == "scale_unavailable"


def test_failed_and_named_grade_automated_assessments_remain_eligible():
    result, criterion = automated()
    result.outcome, result.score = ResultOutcome.failed, Decimal("0")
    assert _recordable(result, criterion)
    result.score = Decimal("50")
    assert not _recordable(result, criterion)
    result.outcome = ResultOutcome.passed
    assert _recordable(result, criterion)


def test_initial_body_requires_reason_and_rejects_a_fictional_manual_revision():
    valid = {
        "expected_revision": 0,
        "resolution": "mark_pass",
        "reason": "I inspected the source before affirming this assessment.",
    }
    assert JudgmentRecordIn.model_validate(valid).reason == valid["reason"]
    for change in (
        {"reason": "  "},
        {"expected_revision": True},
        {"expected_judgment_revision": 0},
        {"resolution": "accept_majority"},
        {"resolution": "mark_level"},
    ):
        with pytest.raises(ValidationError):
            JudgmentRecordIn.model_validate({**valid, **change})
    with pytest.raises(ValidationError):
        JudgmentRevisionIn.model_validate(valid)


def event():
    result, criterion = automated()
    stamp = datetime(2026, 10, 25, 15, 0, tzinfo=UTC)
    return AuditLog(
        id=20,
        check_run_id=10,
        event_type="judgment_recorded",
        created_at=stamp,
        payload={
            "judgment": {
                "schema_version": 2,
                "check_run_id": 10,
                "criterion_id": 5,
                "check_result_id": 7,
                "action": "judgment_recorded",
                "assessment_revision_before": 0,
                "assessment_revision_after": 1,
                "before": None,
                "performed_by": 1,
                "original_assessment": _original(result, criterion).model_dump(mode="json"),
                "after": {
                    "judgment_revision": 1,
                    "resolution_type": "mark_pass",
                    "outcome": "passed",
                    "score": 100,
                    "level": None,
                    "reason": "I checked this requirement independently.",
                    "actor_id": 1,
                    "recorded_at": stamp.isoformat(),
                    "provenance": "recorded",
                },
            }
        },
    )


def test_initial_history_distinguishes_automated_assessment_from_null_manual_before():
    record = history_entry(event())
    assert record.schema_version == 2 and record.action == "judgment_recorded"
    assert record.before is None and record.after.judgment_revision == 1
    assert record.original_assessment.original_outcome == "passed"


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 1},
        {"action": "judgment_revised"},
        {"original_assessment": None},
        {"assessment_revision_after": 2},
        {"performed_by": 2},
    ],
)
def test_corrupt_initial_history_does_not_become_a_private_record(change):
    row = event()
    row.payload["judgment"].update(change)
    with pytest.raises((ValueError, ValidationError)):
        history_entry(row)


def test_original_pending_or_invalid_scores_cannot_be_relabelled_as_settled_history():
    for outcome, score in (
        ("escalated", None),
        ("not_applicable", None),
        ("passed", float("nan")),
        ("passed", True),
        ("passed", "100"),
        ("failed", 100),
    ):
        row = event()
        row.payload["judgment"]["original_assessment"].update(
            original_outcome=outcome, original_score=score
        )
        with pytest.raises(ValidationError):
            history_entry(row)


def test_original_named_level_refuses_coerced_or_inconsistent_facts():
    for level in (
        {"ordinal": True, "name": "Established", "points": "1", "max_points": "2"},
        {"ordinal": 1, "name": "Established", "points": 3, "max_points": 2},
    ):
        row = event()
        row.payload["judgment"]["original_assessment"]["original_level"] = level
        with pytest.raises(ValidationError):
            history_entry(row)


def test_initial_event_does_not_substitute_accepting_an_ai_vote_for_an_explicit_judgment():
    row = event()
    row.payload["judgment"]["after"]["resolution_type"] = "accept_majority"
    with pytest.raises(ValidationError):
        history_entry(row)


def test_new_named_judgment_history_requires_consistent_stored_score():
    row = event()
    after = row.payload["judgment"]["after"]
    after.update(
        resolution_type="mark_level",
        level={"ordinal": 0, "name": "Absent", "points": 0, "max_points": 2},
    )
    with pytest.raises(ValidationError):
        history_entry(row)
    after["score"] = 0
    assert history_entry(row).after.score == 0


@pytest.mark.parametrize(
    "resolution,outcome,score",
    [
        ("mark_pass", "passed", 100),
        ("mark_fail", "failed", 0),
        ("needs_document", "not_applicable", None),
    ],
)
def test_initial_binary_or_excluded_history_cannot_carry_an_unrelated_named_grade(
    resolution, outcome, score
):
    row = event()
    row.payload["judgment"]["after"].update(
        resolution_type=resolution,
        outcome=outcome,
        score=score,
        level={"ordinal": 0, "name": "Unrelated named grade", "points": 0, "max_points": 2},
    )
    with pytest.raises(ValidationError):
        history_entry(row)
