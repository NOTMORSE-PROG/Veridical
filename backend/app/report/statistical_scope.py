"""F6/F8: transcribe saved processing status, independently of findings."""

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field


class ReconstructionScopeOut(BaseModel):
    state: Literal["candidate_found", "incomplete", "not_applicable", "not_recorded"] = (
        "not_recorded"
    )
    considered_rows: int | None = Field(default=None, ge=0, strict=True)
    attempted_rows: int | None = Field(default=None, ge=0, strict=True)
    compatible_rows: int | None = Field(default=None, ge=0, strict=True)
    unassessed_rows: int | None = Field(default=None, ge=0, strict=True)


class StatisticalCheckScopeOut(BaseModel):
    state: Literal["completed", "not_applicable", "incomplete", "not_recorded"] = "not_recorded"
    unassessed_descriptive_rows: int = Field(default=0, ge=0, strict=True)
    reconstruction: ReconstructionScopeOut | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


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


def _recorded_candidate_shape(row: dict) -> bool:
    candidate, values = row.get("candidate"), row.get("values")
    if not isinstance(candidate, dict) or not isinstance(values, dict):
        return False
    n = candidate.get("n")
    if type(n) is not int or n < 2 or type(values.get("n")) is not int or values["n"] != n:
        return False
    if any(
        not isinstance(candidate.get(key), str) or not candidate[key].strip()
        for key in ("mean", "sample_variance")
    ):
        return False
    histogram = candidate.get("histogram")
    if not isinstance(histogram, list) or not histogram or len(histogram) > n:
        return False
    count = 0
    for entry in histogram:
        if not isinstance(entry, dict) or any(
            type(entry.get(key)) is not int for key in ("numerator", "denominator", "count")
        ):
            return False
        if entry["denominator"] < 1 or entry["count"] < 1:
            return False
        count += entry["count"]
    return count == n


def reconstruction_scope(detail: object) -> ReconstructionScopeOut | None:
    if not isinstance(detail, dict):
        return None
    if "sprite_reconstruction" not in detail:
        count = detail.get("n_descriptive_stats")
        if "n_descriptive_stats" in detail and (type(count) is not int or count < 0):
            return ReconstructionScopeOut()
        return ReconstructionScopeOut() if type(count) is int and count > 0 else None
    saved = detail["sprite_reconstruction"]
    if not isinstance(saved, dict) or saved.get("version") != "sprite_pipeline_v1":
        return ReconstructionScopeOut()
    names = ("considered_rows", "attempted_rows", "compatible_rows", "unassessed_rows")
    if any(type(saved.get(name)) is not int or saved[name] < 0 for name in names):
        return ReconstructionScopeOut()
    considered, attempted, compatible, unassessed = [saved[name] for name in names]
    if compatible + unassessed != considered or not compatible <= attempted <= considered:
        return ReconstructionScopeOut()
    rows, omitted = saved.get("rows"), saved.get("omitted_receipt_rows")
    if (
        not isinstance(rows, list)
        or type(omitted) is not int
        or omitted < 0
        or len(rows) + omitted != considered
    ):
        return ReconstructionScopeOut()
    searched_states = {
        "candidate_found",
        "not_found_within_budget",
        "library_error",
        "deadline_reached",
        "worker_unavailable",
        "invalid_worker_response",
        "invalid_candidate",
    }
    unsearched_states = {
        "unassessed",
        "input_size_limit",
        "row_budget_reached",
        "check_deadline_reached",
    }
    identities = set()
    recorded_compatible = recorded_attempted = 0
    for row in rows:
        if not isinstance(row, dict) or any(
            type(row.get(key)) is not int for key in ("table_index", "row_index")
        ):
            return ReconstructionScopeOut()
        identity = (row["table_index"], row["row_index"])
        state = row.get("state")
        if (
            identity[0] < 0
            or identity[1] < 1
            or identity in identities
            or not isinstance(state, str)
            or state not in searched_states | unsearched_states
        ):
            return ReconstructionScopeOut()
        identities.add(identity)
        if state == "candidate_found" and not _recorded_candidate_shape(row):
            return ReconstructionScopeOut()
        recorded_compatible += state == "candidate_found"
        recorded_attempted += state in searched_states
    if not (
        recorded_compatible <= compatible <= recorded_compatible + omitted
        and recorded_attempted <= attempted <= recorded_attempted + omitted
    ):
        return ReconstructionScopeOut()
    state = (
        "not_applicable" if not considered else "incomplete" if unassessed else "candidate_found"
    )
    return ReconstructionScopeOut(state=state, **{name: saved[name] for name in names})


def statistical_reconstruction_incomplete(detail: object) -> bool:
    scope = reconstruction_scope(detail)
    return bool(scope and scope.state in {"incomplete", "not_recorded"})


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
    reconstruction = (
        reconstruction_scope(details[0]) if details is not None and len(details) == 1 else None
    )
    if (
        reconstruction
        and reconstruction.state in {"incomplete", "not_recorded"}
        and state in {"completed", "not_applicable"}
    ):
        state = "incomplete"
    return StatisticalCheckScopeOut(
        state=state, unassessed_descriptive_rows=count, reconstruction=reconstruction
    )


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
    if scope.reconstruction:
        reconstruction = scope.reconstruction
        if reconstruction.state == "candidate_found":
            detail += (
                " SPRITE found compatible candidate distributions for "
                f"{reconstruction.compatible_rows} "
                "declared rows. These are possible reconstructions under the reported assumptions, "
                "not recovered or verified original data."
            )
        elif reconstruction.state == "incomplete":
            detail += (
                f" SPRITE reconstruction remains unassessed for {reconstruction.unassessed_rows} "
                "rows. Required scale assumptions may be missing or a bounded search did not "
                "produce a verified candidate. Search failure is not proof of inconsistency."
            )
        elif reconstruction.state == "not_recorded":
            detail += (
                " No SPRITE reconstruction receipt was recorded for these descriptive statistics."
            )
    return label, detail
