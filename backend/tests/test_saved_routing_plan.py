"""A recorded execution plan is data to validate, not an invitation to re-route."""

from copy import deepcopy

import pytest

from app.checks.router import decode_routing_plan
from app.models.enums import CheckKind

STRUCTURAL = {
    "criterion_id": 1,
    "kind": "structural",
    "rule_id": "removed_rule",
    "degraded": False,
    "note": None,
    "unroutable": False,
}
SEMANTIC = {
    "criterion_id": 2,
    "kind": "semantic",
    "rule_id": None,
    "degraded": True,
    "note": "Recorded earlier plan",
    "unroutable": False,
}


def test_saved_plan_preserves_order_method_and_even_a_removed_rule_identity():
    payload = {"decisions": [SEMANTIC, STRUCTURAL]}
    before = deepcopy(payload)
    result = decode_routing_plan(payload, {1, 2})
    assert [row.criterion_id for row in result] == [2, 1]
    assert result[0].kind == CheckKind.semantic and result[0].degraded
    assert result[1].rule_id == "removed_rule"
    assert payload == before


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        [],
        {"decisions": None},
        {"decisions": {}},
        {"decisions": []},
        {"decisions": [STRUCTURAL]},
        {"decisions": [STRUCTURAL, STRUCTURAL]},
    ],
)
def test_missing_partial_duplicate_or_nonlist_plan_never_becomes_a_new_plan(payload):
    with pytest.raises(ValueError):
        decode_routing_plan(payload, {1, 2})


@pytest.mark.parametrize(
    "change",
    [
        {"criterion_id": True},
        {"criterion_id": 0},
        {"criterion_id": "1"},
        {"criterion_id": 99},
        {"kind": "originality_reuse"},
        {"kind": None},
        {"rule_id": None},
        {"rule_id": " "},
        {"degraded": "false"},
        {"unroutable": 0},
        {"unroutable": True},
        {"note": []},
        {"future": "ignored?"},
    ],
)
def test_saved_plan_strict_shape_and_criterion_binding(change):
    with pytest.raises(ValueError):
        decode_routing_plan({"decisions": [{**STRUCTURAL, **change}, SEMANTIC]}, {1, 2})


@pytest.mark.parametrize("key", list(STRUCTURAL))
def test_no_required_saved_field_is_reconstructed(key):
    row = dict(STRUCTURAL)
    row.pop(key)
    with pytest.raises(ValueError):
        decode_routing_plan({"decisions": [row, SEMANTIC]}, {1, 2})


def test_semantic_plan_cannot_carry_a_structural_rule_identity():
    with pytest.raises(ValueError):
        decode_routing_plan(
            {"decisions": [STRUCTURAL, {**SEMANTIC, "rule_id": "page_limit"}]}, {1, 2}
        )
