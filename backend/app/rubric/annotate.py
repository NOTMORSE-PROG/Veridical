"""Check an already-stored format version against its own source (BUG-566).

A version decomposed before the source check existed may hold level
descriptors that are not the document's words, and if checks are pinned to it
its criteria can no longer change (F2.4). This records what the check finds as
parse issues on that version, so its review screen and its reports say so,
without touching a criterion or a level and without calling the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.errors import NotFoundError
from app.ingest.service import detect_format, select_extractor
from app.models.audit import AuditLog
from app.models.enums import RubricParseStatus
from app.models.rubric import Rubric
from app.models.run import CheckRun
from app.rubric.documents import RubricSource, _source_path
from app.rubric.issues import parsed_criterion
from app.rubric.source import build_decomposition_source
from app.rubric.verbatim import verify_against_source


@dataclass
class AnnotationResult:
    rubric_id: int
    applied: bool
    already_annotated: bool
    criteria: int
    check_run_ids: list[int]
    copy_pages: list[int]
    withheld_count: int
    issues: list[dict] = field(default_factory=list)


async def annotate_rubric(
    session: AsyncSession, settings: Settings, rubric_id: int, *, apply: bool
) -> AnnotationResult:
    rubric = await session.scalar(
        select(Rubric)
        .where(Rubric.id == rubric_id)
        .options(selectinload(Rubric.criteria))
        .with_for_update()
    )
    if rubric is None:
        raise NotFoundError(f"No rubric with id {rubric_id}.")
    check_run_ids = list(
        (
            await session.scalars(
                select(CheckRun.id).where(CheckRun.rubric_id == rubric_id).order_by(CheckRun.id)
            )
        ).all()
    )
    recorded = Path(rubric.source_file) if rubric.source_file else None
    was_local = recorded is not None and recorded.is_file()
    path = _source_path(
        RubricSource(rubric.id, rubric.rubric_family_id, rubric.version, rubric.source_file),
        settings,
    )
    try:
        extraction = select_extractor(detect_format(path, settings))(str(path), settings)
    finally:
        if not was_local:
            # A copy fetched from storage for this run carries the format's
            # filled-in values (names); it is not left on the operator's disk.
            path.unlink(missing_ok=True)
    source = build_decomposition_source(extraction, settings)
    ordered = sorted(rubric.criteria, key=lambda row: row.position)
    _, found = verify_against_source([parsed_criterion(row) for row in ordered], source, settings)

    # Issues the parser already recorded as text stay first, as whole-format notes.
    details = [
        {
            "id": number,
            "code": "other",
            "criterion_id": None,
            "level": None,
            "message": message,
            "status": "open",
            "resolution": None,
        }
        for number, message in enumerate(rubric.parse_issues or [], start=1)
    ]
    details.extend(
        {
            "id": len(details) + offset,
            "code": issue.code,
            "criterion_id": ordered[issue.criterion].id,
            "level": issue.level,
            "message": issue.message,
            "status": "open",
            "resolution": None,
        }
        for offset, issue in enumerate(found, start=1)
    )
    snapshot = source.to_snapshot()
    result = AnnotationResult(
        rubric_id=rubric.id,
        applied=False,
        already_annotated=rubric.parse_issue_details is not None,
        criteria=len(ordered),
        check_run_ids=check_run_ids,
        copy_pages=snapshot["copy_pages"],
        withheld_count=snapshot["withheld_count"],
        issues=[
            {k: d[k] for k in ("id", "code", "criterion_id", "level", "message")} for d in details
        ],
    )
    if not apply or result.already_annotated:
        await session.rollback()
        return result
    rubric.parse_issue_details = details or None
    rubric.parse_issues = [d["message"] for d in details] or None
    if details:
        rubric.parse_status = RubricParseStatus.needs_review
    rubric.source_snapshot = snapshot
    rubric.revision += 1
    session.add(
        AuditLog(
            event_type="rubric_issues_annotated",
            payload={
                "rubric_id": rubric.id,
                "issue_count": len(details),
                "source_issue_count": len(found),
                "check_run_ids": check_run_ids,
            },
        )
    )
    await session.commit()
    result.applied = True
    return result
