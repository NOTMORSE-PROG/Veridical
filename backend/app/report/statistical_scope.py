"""F6/F8: transcribe saved processing status, independently of findings."""

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field


class StatisticalCheckScopeOut(BaseModel):
    state: Literal["completed", "not_applicable", "incomplete", "not_recorded"] = "not_recorded"
    unassessed_descriptive_rows: int = Field(default=0, ge=0, strict=True)


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


def unassessed_descriptive_rows(detail: object) -> int | None:
    if detail is None:
        return 0
    if not isinstance(detail, dict):
        return None
    count = detail.get("n_grim_skipped_likely_composite", 0)
    return count if type(count) is int and count >= 0 else None


def statistical_check_scope(
    outcomes: Sequence[str | None], details: Sequence[object] | None = None
) -> StatisticalCheckScopeOut:
    """Missing or ambiguous result rows cannot establish a saved completion."""
    state = "not_recorded"
    if len(outcomes) == 1 and isinstance(outcomes[0], str):
        state = _SAVED_STATES.get(outcomes[0], "not_recorded")
    count = 0
    if details is not None:
        count = unassessed_descriptive_rows(details[0]) if len(details) == 1 else None
        if count is None:
            state, count = "not_recorded", 0
        elif count and state in {"completed", "not_applicable"}:
            state = "incomplete"
    return StatisticalCheckScopeOut(state=state, unassessed_descriptive_rows=count)


def statistical_scope_copy(scope: StatisticalCheckScopeOut) -> tuple[str, str]:
    label, detail = STATISTICAL_SCOPE_COPY[scope.state]
    if scope.unassessed_descriptive_rows:
        count = scope.unassessed_descriptive_rows
        detail += (
            f" {count} descriptive {'row was' if count == 1 else 'rows were'} not assessed by "
            "GRIM/GRIMMER because single-item score assumptions were not established. "
            "Review the score scale and number of items in the original tables. "
            "Independently supported statistical findings remain available."
        )
    return label, detail
