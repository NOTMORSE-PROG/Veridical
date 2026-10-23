"""V-085 stored facts, authority inputs and actual grade identity controls."""

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.models.enums import CheckKind, ResultOutcome
from app.report.judgment_schemas import JudgmentRevisionIn, JudgmentSnapshot
from app.report.judgment_snapshot import (
    judgment_grade_identity,
    manual_resolution,
    snapshot_judgment,
)


def result(resolution="mark_pass", score="100.00", outcome=ResultOutcome.passed, **overrides):
    value = SimpleNamespace(
        criterion_id=4,
        kind=CheckKind.semantic,
        outcome=outcome,
        score=Decimal(score) if score is not None else None,
        judgment_revision=0,
        detail={
            "reason": "Original AI explanation",
            "resolution": {
                "type": resolution,
                "reason": "Earlier recorded instructor reason",
            },
        },
    )
    for key, item in overrides.items():
        setattr(value, key, item)
    return value


@pytest.mark.parametrize(
    "resolution,score,outcome",
    [
        ("mark_pass", "100", ResultOutcome.passed),
        ("mark_fail", "0", ResultOutcome.failed),
        ("mark_level", "33.33", ResultOutcome.passed),
        ("accept_majority", "50", ResultOutcome.passed),
        ("accept_majority", "0", ResultOutcome.failed),
        ("needs_document", None, ResultOutcome.not_applicable),
    ],
)
def test_legacy_missing_optional_attribution_and_rung_is_not_fabricated(resolution, score, outcome):
    row = result(resolution, score, outcome)
    before = deepcopy(row.detail)
    snapshot = snapshot_judgment(row)
    assert snapshot is not None and snapshot.judgment_revision == 0
    assert snapshot.actor_id is None and snapshot.recorded_at is None and snapshot.level is None
    assert snapshot.provenance == "legacy_partial"
    assert row.detail == before


@pytest.mark.parametrize(
    "change",
    [
        {"criterion_id": None},
        {"kind": CheckKind.internal_agreement},
        {"detail": []},
        {"detail": {"resolution": True}},
        {"detail": {"resolution": {"type": "invented", "reason": "actual reason"}}},
        {"detail": {"resolution": {"type": "mark_pass", "reason": 42}}},
        {"detail": {"resolution": {"type": "mark_pass", "reason": "\t\u2003\n"}}},
        {"outcome": ResultOutcome.escalated},
        {"score": Decimal("50")},
        {"score": Decimal("NaN")},
    ],
)
def test_malformed_or_nonmanual_rows_do_not_gain_authority(change):
    row = result(**change)
    assert manual_resolution(row) is None
    assert snapshot_judgment(row) is None


def test_unversioned_manual_is_not_silently_given_a_current_token():
    assert manual_resolution(result(judgment_revision=None)) is not None
    assert snapshot_judgment(result(judgment_revision=None)) is None


def test_accept_verb_equivalence_does_not_collapse_partial_or_distinct_named_levels():
    accepted = snapshot_judgment(result("accept_majority"))
    manual = snapshot_judgment(result())
    partial = snapshot_judgment(result("accept_majority", "50"))
    assert judgment_grade_identity(accepted) == judgment_grade_identity(manual)
    assert judgment_grade_identity(partial) != judgment_grade_identity(manual)
    row = result("mark_level", "0")
    row.detail["level"] = {"ordinal": 0, "name": "Absent", "points": 0, "max_points": 3}
    zero = snapshot_judgment(row)
    row.detail["level"] = {"ordinal": 1, "name": "No evidence yet", "points": 0, "max_points": 3}
    other = snapshot_judgment(row)
    assert zero.level.ordinal == 0 and zero.level.points == 0
    assert judgment_grade_identity(zero) != judgment_grade_identity(other)


@pytest.mark.parametrize("actor,time", [(True, "bad"), (0, "2026-10-23T01:00:00"), ("1", None)])
def test_unknown_attribution_does_not_become_current_actor_or_utc(actor, time):
    row = result(judgment_revision=1)
    row.detail["resolution"].update(by_instructor_id=actor, at=time)
    snapshot = snapshot_judgment(row)
    assert snapshot.actor_id is None and snapshot.recorded_at is None
    assert snapshot.provenance == "legacy_partial"


def test_actual_new_timestamp_and_normalized_score_survive_exactly():
    row = result("mark_level", "33.33", judgment_revision=2)
    row.detail["resolution"].update(by_instructor_id=7, at="2026-10-23T01:00:00+08:00")
    row.detail["level"] = {"ordinal": 2, "name": "Developing", "points": 1, "max_points": 3}
    snapshot = snapshot_judgment(row)
    assert snapshot.provenance == "recorded" and snapshot.actor_id == 7
    assert (
        snapshot.score == 33.33 and snapshot.recorded_at.isoformat() == "2026-10-22T17:00:00+00:00"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"expected_revision": True},
        {"expected_judgment_revision": "0"},
        {"resolution": "accept_majority"},
        {"level": 1},
        {"resolution": "mark_level"},
        {"resolution": "mark_level", "level": False},
        {"reason": "short"},
        {"reason": "x" * 1001},
        {"extra_actor": 1},
    ],
)
def test_revision_authority_and_choice_input_are_strict(change):
    payload = dict(
        expected_revision=2,
        expected_judgment_revision=0,
        resolution="mark_pass",
        reason="Explicit new instructor reason",
    )
    payload.update(change)
    with pytest.raises(ValidationError):
        JudgmentRevisionIn.model_validate(payload)


def test_supported_zero_ordinal_and_trimmed_new_reason():
    value = JudgmentRevisionIn(
        expected_revision=0,
        expected_judgment_revision=0,
        resolution="mark_level",
        level=0,
        reason="  Explicit new instructor reason  ",
    )
    assert value.level == 0 and value.reason == "Explicit new instructor reason"


def test_configuration_rejects_impossible_limits():
    with pytest.raises(ValidationError):
        Settings(judgment_revision_reason_max_length=5)
    with pytest.raises(ValidationError):
        Settings(judgment_history_page_size=101, judgment_history_max_page_size=100)


@pytest.mark.parametrize(
    "change",
    [
        {"score": 999},
        {"score": "NaN"},
        {"score": float("nan")},
        {"score": float("inf")},
        {"score": True},
        {"score": "100"},
        {"score": None},
        {"outcome": "failed"},
        {"resolution_type": "needs_document"},
        {"reason": "\t\u2003\n"},
        {"actor_id": None},
        {"recorded_at": "2026-10-23T01:00:00"},
        {"level": {"name": "Grade", "ordinal": False, "points": 1, "max_points": 3}},
        {"level": {"name": "Grade", "ordinal": 0, "points": "1", "max_points": 3}},
        {"level": {"name": "Grade", "ordinal": 0, "points": 1, "max_points": "NaN"}},
        {"level": {"name": "Grade", "ordinal": 0, "points": 4, "max_points": 3}},
    ],
)
def test_malformed_typed_snapshots_cannot_claim_recorded_grade_facts(change):
    row = result(judgment_revision=1)
    row.detail["resolution"].update(by_instructor_id=7, at="2026-10-23T01:00:00+08:00")
    raw = snapshot_judgment(row).model_dump(mode="json")
    with pytest.raises(ValidationError):
        JudgmentSnapshot.model_validate({**raw, **change})
