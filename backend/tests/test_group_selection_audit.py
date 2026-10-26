"""V-085: historical selection facts require a bound stored snapshot checksum."""

from copy import deepcopy

import pytest

from app.groups.selection import selection_audit_event
from tests.test_group_selection import receipt as receipt


def audit_payload(receipt):
    return {
        "schema_version": 1,
        "instructor_id": 4,
        "manuscript_id": 9,
        "before": {"group_id": None, "group_label": "Ungrouped"},
        "selection": receipt,
    }


def test_historical_audit_projects_only_exact_stored_facts_with_matching_checksum(receipt):
    payload = audit_payload(receipt)
    original = deepcopy(payload)
    projected = selection_audit_event(payload, manuscript_id=9, instructor_id=4)
    assert projected is not None and projected.model_dump(mode="json") == payload
    receipt["effective"]["name"] = "UNSUPPORTED changed team"
    receipt["effective"]["member_names"] = ["UNSUPPORTED private member"]
    assert selection_audit_event(payload, manuscript_id=9, instructor_id=4) is None
    assert original["selection"]["effective"]["name"] == "Practice team (2)"


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"schema_version": 1.0},
        {"instructor_id": 5},
        {"manuscript_id": 10},
        {"before": {"group_id": 3, "group_label": "Changed"}},
        {"extra": "unrecognized"},
    ],
)
def test_historical_audit_binds_prior_assignment_owner_and_manuscript(receipt, change):
    payload = {**audit_payload(receipt), **change}
    assert selection_audit_event(payload, manuscript_id=9, instructor_id=4) is None


@pytest.mark.parametrize("version", [True, "2", 2.0])
def test_historical_audit_refuses_nested_receipt_version_coercion(receipt, version):
    receipt["version"] = version
    assert selection_audit_event(audit_payload(receipt), manuscript_id=9, instructor_id=4) is None
