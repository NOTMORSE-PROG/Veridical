"""F3/F8: every academic action must name the assessment the instructor reviewed."""

import pytest
from pydantic import ValidationError

from app.flags.schemas import ConfirmCitationSourceIn, OverrideFlagIn
from app.report.schemas import DecisionIn, ReopenIn, ResolveEscalationIn

ACTIONS = [
    (DecisionIn, {"decision": "returned"}),
    (ReopenIn, {"reason": "Review newly available evidence."}),
    (ResolveEscalationIn, {"resolution": "mark_pass", "reason": "Read the actual manuscript."}),
    (OverrideFlagIn, {"reason": "Checked the cited evidence."}),
    (ConfirmCitationSourceIn, {"reason": "Checked the publisher catalogue."}),
]


@pytest.mark.parametrize("schema,body", ACTIONS)
@pytest.mark.parametrize("token", [None, True, False, -1, 0.0, 1.25, "0"])
def test_an_academic_action_rejects_unreviewed_or_coerced_revisions(schema, body, token):
    with pytest.raises(ValidationError):
        schema.model_validate({**body, "expected_revision": token})


@pytest.mark.parametrize("schema,body", ACTIONS)
def test_missing_revision_is_not_silently_bound_to_the_server_current_state(schema, body):
    with pytest.raises(ValidationError):
        schema.model_validate(body)


@pytest.mark.parametrize("schema,body", ACTIONS)
@pytest.mark.parametrize("token", [0, 2**32])
def test_explicit_revision_supports_the_baseline_and_more_than_32_bit_history(schema, body, token):
    assert schema.model_validate({**body, "expected_revision": token}).expected_revision == token
