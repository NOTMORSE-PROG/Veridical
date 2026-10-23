"""Decode persisted manual facts without reconstructing unavailable history."""

from datetime import UTC, datetime
from math import isfinite
from typing import Any

from pydantic import ValidationError

from app.checks.levels import outcome_and_score
from app.models.enums import CheckKind, ResultOutcome
from app.report.judgment_schemas import JudgmentSnapshot
from app.report.schemas import CriterionLevelOut

MANUAL_RESOLUTIONS = frozenset(
    {"accept_majority", "mark_pass", "mark_fail", "mark_level", "needs_document"}
)


def manual_resolution(result: Any) -> dict[str, Any] | None:
    """Core recognition mirrors migration0040; callers establish run/rubric binding."""
    if result.criterion_id is None or result.kind not in (CheckKind.semantic, CheckKind.structural):
        return None
    if not isinstance(result.detail, dict):
        return None
    value = result.detail.get("resolution")
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        return None
    resolution = value["type"]
    if (
        resolution not in MANUAL_RESOLUTIONS
        or not isinstance(value.get("reason"), str)
        or not value["reason"].strip()
    ):
        return None
    score = float(result.score) if result.score is not None else None
    if score is not None and not isfinite(score):
        return None
    return value if manual_grade_consistent(resolution, result.outcome, score) else None


def manual_grade_consistent(resolution: str, outcome: str, score: float | None) -> bool:
    """The same grade contract applies to current rows and immutable snapshots."""
    pass_outcome, full, _ = outcome_and_score(None, "pass")
    fail_outcome, none, _ = outcome_and_score(None, "fail")
    if resolution == "needs_document":
        valid = outcome == ResultOutcome.not_applicable and score is None
    elif resolution == "mark_pass":
        valid = outcome == pass_outcome and score == full
    elif resolution == "mark_fail":
        valid = outcome == fail_outcome and score == none
    else:
        valid = (outcome == pass_outcome and score is not None and none <= score <= full) or (
            resolution == "accept_majority" and outcome == fail_outcome and score == none
        )
    return valid


def stored_level(detail: dict[str, Any]) -> CriterionLevelOut | None:
    level = detail.get("level")
    if not isinstance(level, dict) or type(level.get("ordinal")) is not int:
        return None
    if not isinstance(level.get("name"), str) or not level["name"].strip():
        return None
    if any(
        type(level.get(key)) not in (int, float) or not isfinite(level[key])
        for key in ("points", "max_points")
    ):
        return None
    if not 0 <= level["points"] <= level["max_points"] or level["max_points"] <= 0:
        return None
    try:
        return CriterionLevelOut.model_validate(level)
    except ValidationError:
        return None


def snapshot_judgment(result: Any) -> JudgmentSnapshot | None:
    resolution = manual_resolution(result)
    revision = getattr(result, "judgment_revision", None)
    if resolution is None or type(revision) is not int or revision < 0:
        return None
    actor = resolution.get("by_instructor_id")
    actor = actor if type(actor) is int and actor > 0 else None
    recorded_at = None
    if isinstance(resolution.get("at"), str):
        try:
            parsed = datetime.fromisoformat(resolution["at"])
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                recorded_at = parsed.astimezone(UTC)
        except ValueError:
            pass
    level = stored_level(result.detail)
    complete = (
        revision > 0
        and actor is not None
        and recorded_at is not None
        and (resolution["type"] != "mark_level" or level is not None)
    )
    return JudgmentSnapshot(
        judgment_revision=revision,
        resolution_type=resolution["type"],
        outcome=result.outcome,
        score=float(result.score) if result.score is not None else None,
        level=level,
        reason=resolution["reason"],
        actor_id=actor,
        recorded_at=recorded_at,
        provenance="recorded" if complete else "legacy_partial",
    )


def judgment_grade_identity(snapshot: JudgmentSnapshot) -> tuple:
    """Resolution verbs alone do not distinguish equivalent recorded grades."""
    if snapshot.resolution_type == "needs_document":
        return ("exclusion", snapshot.outcome, snapshot.score)
    if snapshot.level is not None:
        level = snapshot.level
        return (
            "level",
            level.ordinal,
            level.name,
            level.points,
            level.max_points,
            snapshot.outcome,
            snapshot.score,
        )
    if snapshot.resolution_type == "mark_level":
        return ("unavailable_level", snapshot.outcome, snapshot.score)
    return ("pass_fail", snapshot.outcome, snapshot.score)
