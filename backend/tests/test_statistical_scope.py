"""F6 saved execution status must survive an empty findings collection."""

import pytest
from pydantic import ValidationError

from app.report.statistical_scope import StatisticalCheckScopeOut, statistical_check_scope


@pytest.mark.parametrize(
    ("outcomes", "state"),
    [
        (["passed"], "completed"),
        (["not_applicable"], "not_applicable"),
        (["unverifiable"], "incomplete"),
        (["api_down"], "incomplete"),
        (["quota_exhausted"], "incomplete"),
        (["escalated"], "incomplete"),
        (["failed"], "incomplete"),
        ([], "not_recorded"),
        ([None], "not_recorded"),
        (["future_outcome"], "not_recorded"),
        (["passed", "not_applicable"], "not_recorded"),
        (["passed", "passed"], "not_recorded"),
    ],
)
def test_saved_state_preserves_applicability_and_uncertainty(outcomes, state):
    assert statistical_check_scope(outcomes).model_dump() == {"state": state}


def test_safe_projection_contains_no_raw_detail_or_private_fields():
    projected = StatisticalCheckScopeOut.model_validate(
        {"state": "completed", "detail": {"prompt": "private", "n_flags": 0}}
    )
    assert projected.model_dump() == {"state": "completed"}


def test_unrecognized_public_state_cannot_claim_completion():
    with pytest.raises(ValidationError):
        StatisticalCheckScopeOut(state="all_clear")
