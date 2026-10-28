"""F8: historical identity cannot be invented from today's group details."""

import pytest

from app.models.manuscript import Manuscript
from app.models.run import CheckRun
from app.report.subject_identity import subject_from_receipt


def bound_subject():
    manuscript = Manuscript(id=7, instructor_id=2, content_hash="a" * 64, group_id=3)
    run = CheckRun(id=11, manuscript_id=7)
    receipt = dict(version=1, instructor_id=2, manuscript_id=7, check_run_id=11,
                   group_id=3, source_sha256="a" * 64,
                   basis="confirmed_assignment_at_check", title="An unrelated study",
                   member_names=["Ana Cruz", "José Reyes"])
    return manuscript, run, receipt


def test_recorded_identity_survives_current_group_reassignment_without_enrichment():
    manuscript, run, receipt = bound_subject()
    manuscript.group_id = 98
    out = subject_from_receipt(receipt, run=run, manuscript=manuscript)
    assert out.title == "An unrelated study"
    assert out.member_names == ["Ana Cruz", "José Reyes"]
    assert out.basis == "confirmed_assignment_at_check"
    assert set(out.model_dump()) == {"basis", "title", "member_names"}


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": "1"}, {"version": 2},
    {"instructor_id": 9}, {"manuscript_id": 8}, {"check_run_id": 10},
    {"source_sha256": "b" * 64}, {"source_sha256": None}, {"group_id": False},
    {"member_names": "Ana"}, {"member_names": [9]}, {"basis": "inferred"},
    {"private_note": "must never be exposed"},
])
def test_malformed_or_foreign_receipts_are_unknown(change):
    manuscript, run, receipt = bound_subject()
    receipt.update(change)
    out = subject_from_receipt(receipt, run=run, manuscript=manuscript)
    assert out.basis == "not_recorded"
    assert out.title is None and out.member_names == []


@pytest.mark.parametrize("value", [None, {}, [], "historical unknown"])
def test_legacy_unknown_remains_unknown(value):
    manuscript, run, _ = bound_subject()
    assert subject_from_receipt(value, run=run, manuscript=manuscript).basis == "not_recorded"
