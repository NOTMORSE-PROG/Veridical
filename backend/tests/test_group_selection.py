"""V-085 saved selection is explicit identity, never a typed-name proposal."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.groups.assignment import confirmation_recorded, current_receipt
from app.groups.selection import (
    SavedGroupFacts,
    SavedGroupOut,
    SelectGroupRequest,
    SelectionReceipt,
    current_selection_receipt,
    facts_fingerprint,
)


@pytest.fixture
def receipt():
    facts = SavedGroupFacts(
        id=7,
        name="Practice team (2)",
        member_names=["Dela Cruz, Maria", "Santos, Ana"],
        program_id=3,
        program="IT",
        title="Saved title",
    )
    fingerprint = facts_fingerprint(4, facts)
    return SelectionReceipt(
        version=2,
        manuscript_id=9,
        instructor_id=4,
        assigned_group_id=7,
        request=SelectGroupRequest(
            saved_group_id=7, fingerprint=fingerprint, expected_group_id=None
        ),
        effective=SavedGroupOut(**facts.model_dump(), fingerprint=fingerprint),
    ).model_dump(mode="json")


@pytest.mark.parametrize(
    "change",
    [
        {"saved_group_id": True},
        {"saved_group_id": "7"},
        {"saved_group_id": 0},
        {"expected_group_id": False},
        {"expected_group_id": "7"},
        {"expected_group_id": -1},
        {"fingerprint": "A" * 64},
        {"fingerprint": "0" * 63},
        {"name": "Override"},
        {"member_names": ["Override"]},
        {"program_id": 1},
        {"title": "Override"},
        {"instructor_id": 4},
    ],
)
def test_selection_request_refuses_ambiguous_identity_or_fact_overrides(receipt, change):
    with pytest.raises(ValidationError):
        SelectGroupRequest.model_validate({**receipt["request"], **change})


def test_unassigned_expected_id_is_required_not_inferred_from_a_label(receipt):
    del receipt["request"]["expected_group_id"]
    with pytest.raises(ValidationError):
        SelectGroupRequest.model_validate(receipt["request"])


def test_separate_v2_receipt_binds_owner_manuscript_group_and_leaves_v1_parser_closed(receipt):
    original = deepcopy(receipt)
    assert current_receipt(receipt, 7) is None
    assert current_selection_receipt(receipt, 7, manuscript_id=9, instructor_id=4)
    assert confirmation_recorded(receipt, 7, manuscript_id=9, instructor_id=4)
    for group, manuscript, owner in [(8, 9, 4), (None, 9, 4), (7, 10, 4), (7, 9, 5)]:
        assert not confirmation_recorded(
            receipt, group, manuscript_id=manuscript, instructor_id=owner
        )
    assert receipt == original


@pytest.mark.parametrize("version", [True, 2.0, "2", 1, None])
def test_receipt_never_coerces_version(receipt, version):
    receipt["version"] = version
    assert current_selection_receipt(receipt, 7, manuscript_id=9, instructor_id=4) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 8),
        ("name", "Practice team"),
        ("member_names", ["Santos, Ana", "Dela Cruz, Maria"]),
        ("program_id", None),
        ("program", "CS"),
        ("title", "Changed title"),
        ("fingerprint", "0" * 64),
    ],
)
def test_receipt_rejects_partial_or_mismatched_fact_snapshot(receipt, field, value):
    receipt["effective"][field] = value
    assert current_selection_receipt(receipt, 7, manuscript_id=9, instructor_id=4) is None


def test_fingerprint_preserves_actual_display_case_nulls_and_order(receipt):
    facts = SavedGroupFacts.model_validate(
        {key: value for key, value in receipt["effective"].items() if key != "fingerprint"}
    )
    assert facts_fingerprint(4, facts) == receipt["request"]["fingerprint"]
    assert facts_fingerprint(5, facts) != receipt["request"]["fingerprint"]
    assert (
        facts_fingerprint(4, facts.model_copy(update={"name": facts.name.lower()}))
        != (receipt["request"]["fingerprint"])
    )
    reordered = dict(reversed(list(facts.model_dump().items())))
    assert (
        facts_fingerprint(4, SavedGroupFacts.model_validate(reordered))
        == (receipt["request"]["fingerprint"])
    )
