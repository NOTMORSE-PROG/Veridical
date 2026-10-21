"""V-081: recorded confirmation must not certify malformed provenance."""

from copy import deepcopy

import pytest

from app.groups.assignment import current_receipt


@pytest.fixture
def recorded():
    return {
        "version": 1,
        "assigned_group_id": 7,
        "request": {
            "name": "source team",
            "members": [],
            "program_id": None,
            "title": "Source title",
            "expected_group_id": 3,
            "expected_provided": True,
        },
        "effective": {
            "name": "source team (2)",
            "members": [],
            "program_id": None,
            "title": "Source title",
        },
    }


@pytest.mark.parametrize("version", [True, False, 1.0, "1", 0, 2, None])
def test_noninteger_or_unknown_receipt_version_is_not_recorded(recorded, version):
    recorded["version"] = version
    assert current_receipt(recorded, 7) is None


def test_receipt_belongs_to_the_current_assignment_and_does_not_mutate_history(recorded):
    before = deepcopy(recorded)
    assert current_receipt(recorded, 7) is not None
    assert current_receipt(recorded, 8) is None
    assert current_receipt(recorded, None) is None
    assert recorded == before


def test_legacy_or_incomplete_receipt_does_not_invent_confirmation(recorded):
    assert current_receipt(None, 7) is None
    del recorded["effective"]
    assert current_receipt(recorded, 7) is None
