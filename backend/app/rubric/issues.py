"""Parse issues after the upload (F2.3, BUG-566): rechecking edited levels
against the stored source, the instructor keeping a flagged wording, and the
activation gate.

An issue is open until the instructor acknowledges it or an edit makes it no
longer reproduce. A format whose issues are open cannot be activated: VERIDICAL
escalates a level it could not trace to the document instead of grading with
it (ground rule 1). Rechecking needs no model call: it reads the cells stored
with the rubric at upload.
"""

import copy
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import NotFoundError
from app.models.audit import AuditLog
from app.models.enums import RubricParseStatus
from app.models.instructor import Instructor
from app.models.rubric import Criterion, Rubric
from app.rubric.schemas import IssueAcknowledgement, ParsedCriterion, ParsedLevel
from app.rubric.source import DecompositionSource
from app.rubric.verbatim import verify_against_source


def initial_details(outcome_details: list[dict], rows: list[Criterion]) -> list[dict] | None:
    """The decomposition's issues with draft positions replaced by the ids of
    the stored criteria."""
    details = [
        {
            "id": detail["id"],
            "code": detail["code"],
            "criterion_id": rows[detail["criterion"]].id
            if detail["criterion"] is not None
            else None,
            "level": detail["level"],
            "message": detail["message"],
            "status": detail.get("status", "open"),
            "resolution": detail.get("resolution"),
        }
        for detail in outcome_details
    ]
    return details or None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def parsed_criterion(row: Criterion) -> ParsedCriterion:
    return ParsedCriterion(
        text=row.text,
        type=row.type.value,
        evidence_needed=row.evidence,
        weight=float(row.weight),
        levels=[ParsedLevel.model_validate(level) for level in row.levels] if row.levels else None,
    )


def recheck(rubric: Rubric, rows: list[Criterion], settings: Settings) -> None:
    """Recompute every level's source and every criterion issue against the
    stored source, after an edit. The instructor's wording is kept as saved;
    only where it was found, and whether an issue still holds, change.
    Whole-format notes are not about a level and stay as they are."""
    source = DecompositionSource.from_snapshot(rubric.source_snapshot or {})
    if source is None:
        return  # read before BUG-566: nothing stored to check against
    ordered = sorted(rows, key=lambda row: row.position)
    verified, issues = verify_against_source(
        [parsed_criterion(r) for r in ordered], source, settings
    )
    for row, criterion in zip(ordered, verified, strict=True):
        if row.levels and criterion.levels:
            row.levels = [
                {**stored, "source": level.source.model_dump() if level.source else None}
                for stored, level in zip(row.levels, criterion.levels, strict=True)
            ]
    found = {
        (issue.code, ordered[issue.criterion].id, issue.level): issue.message for issue in issues
    }
    live = {row.id for row in ordered}
    details = copy.deepcopy(rubric.parse_issue_details or [])
    seen: set[tuple[Any, ...]] = set()
    for detail in details:
        if detail["criterion_id"] is None:
            continue
        key = (detail["code"], detail["criterion_id"], detail["level"])
        if key in found:
            seen.add(key)
            detail["message"] = found[key]
            resolution = detail.get("resolution") or {}
            if detail["status"] == "resolved" and resolution.get("kind") == "rechecked":
                detail["status"], detail["resolution"] = "open", None  # it came back
            continue
        if detail["status"] != "open":
            continue
        kind = "rechecked" if detail["criterion_id"] in live else "criterion_removed"
        detail["status"] = "resolved"
        detail["resolution"] = {"kind": kind, "at": _now()}
    next_id = max((detail["id"] for detail in details), default=0) + 1
    for key, message in found.items():
        if key in seen:
            continue
        code, criterion_id, level = key
        details.append(
            {
                "id": next_id,
                "code": code,
                "criterion_id": criterion_id,
                "level": level,
                "message": message,
                "status": "open",
                "resolution": None,
            }
        )
        next_id += 1
    _store(rubric, details)


def _store(rubric: Rubric, details: list[dict]) -> None:
    rubric.parse_issue_details = details or None
    if details:
        # parse_issues keeps every message ever raised, in the same order:
        # the parser's history is not rewritten by a later fix (BUG-052).
        rubric.parse_issues = [detail["message"] for detail in details]
        rubric.parse_status = RubricParseStatus.needs_review


def acknowledge(
    session: AsyncSession,
    rubric: Rubric,
    acknowledgements: list[IssueAcknowledgement],
    instructor: Instructor,
) -> None:
    """Record the instructor keeping flagged wordings as written, with who,
    when and why, in the rubric and in the audit log."""
    if not acknowledgements:
        return
    details = copy.deepcopy(rubric.parse_issue_details or [])
    by_id = {detail["id"]: detail for detail in details}
    for ack in acknowledgements:
        detail = by_id.get(ack.issue_id)
        if detail is None:
            raise NotFoundError(f"No parse issue {ack.issue_id} on this format.")
        if detail["status"] != "open":
            continue
        detail["status"] = "acknowledged"
        detail["resolution"] = {
            "kind": "acknowledged",
            "reason_code": ack.reason_code,
            "note": ack.note,
            "by_instructor_id": instructor.id,
            "by_name": instructor.display_name,
            "at": _now(),
        }
        session.add(
            AuditLog(
                event_type="rubric_issue_acknowledged",
                payload={
                    "rubric_id": rubric.id,
                    "issue_id": ack.issue_id,
                    "code": detail["code"],
                    "criterion_id": detail["criterion_id"],
                    "level": detail["level"],
                    "reason_code": ack.reason_code,
                    "note": ack.note,
                    "instructor_id": instructor.id,
                },
            )
        )
    _store(rubric, details)


def open_issue_ids(rubric: Rubric) -> list[int]:
    return [d["id"] for d in rubric.parse_issue_details or [] if d["status"] == "open"]


def source_summary(rubric: Rubric) -> dict | None:
    snapshot = rubric.source_snapshot
    if not isinstance(snapshot, dict):
        return None
    return {
        "copy_pages": list(snapshot.get("copy_pages", [])),
        "withheld_count": int(snapshot.get("withheld_count", 0)),
    }
