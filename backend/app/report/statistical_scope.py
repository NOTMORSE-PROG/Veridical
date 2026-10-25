"""F6/F8: transcribe saved processing status, independently of findings."""

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel


class StatisticalCheckScopeOut(BaseModel):
    state: Literal["completed", "not_applicable", "incomplete", "not_recorded"] = "not_recorded"


_SAVED_STATES = {
    "passed": "completed",
    "not_applicable": "not_applicable",
    "failed": "incomplete",
    "unverifiable": "incomplete",
    "api_down": "incomplete",
    "quota_exhausted": "incomplete",
    "escalated": "incomplete",
}

STATISTICAL_SCOPE_COPY = {
    "completed": (
        "Statistical checks: Completion recorded.",
        "The saved check records completed statistical processing. This does not establish "
        "that every reported value is correct or verify the raw data.",
    ),
    "not_applicable": (
        "Statistical checks: Not applicable.",
        "This is the saved applicability result for the extracted content the check supports. "
        "It does not establish that the manuscript contains no statistics.",
    ),
    "incomplete": (
        "Statistical checks: Not fully assessed.",
        "The saved statistical assessment is incomplete. "
        "Unassessed content is not treated as passed.",
    ),
    "not_recorded": (
        "Statistical checks: Not recorded.",
        "This run has no usable saved statistical status. "
        "Do not infer that the check ran or was not applicable.",
    ),
}


def statistical_check_scope(outcomes: Sequence[str | None]) -> StatisticalCheckScopeOut:
    """Missing or ambiguous result rows cannot establish a saved completion."""
    state = "not_recorded"
    if len(outcomes) == 1 and isinstance(outcomes[0], str):
        state = _SAVED_STATES.get(outcomes[0], "not_recorded")
    return StatisticalCheckScopeOut(state=state)


def statistical_scope_copy(scope: StatisticalCheckScopeOut) -> tuple[str, str]:
    return STATISTICAL_SCOPE_COPY[scope.state]
