"""F8/V-081: immutable confirmed-assignment identity for newly created checks.

An assignment records instructor-confirmed group facts, not independently
verified manuscript authorship. Historical runs are never enriched on read.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_event
from app.groups.assignment import current_receipt
from app.groups.selection import current_selection_receipt
from app.models.audit import AuditLog
from app.models.group import Group
from app.models.manuscript import Manuscript
from app.models.run import CheckRun

SUBJECT_EVENT = "check_subject_recorded"


class ReportSubjectOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    basis: Literal["confirmed_assignment_at_check", "not_recorded"] = "not_recorded"
    title: str | None = None
    member_names: list[str] = Field(default_factory=list)


class SubjectReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    instructor_id: PositiveInt
    manuscript_id: PositiveInt
    check_run_id: PositiveInt
    group_id: PositiveInt
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis: Literal["confirmed_assignment_at_check"]
    title: str | None
    member_names: list[str]

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Receipt version must be an integer.")
        return value


def subject_from_receipt(
    value: object, *, run: CheckRun, manuscript: Manuscript
) -> ReportSubjectOut:
    try:
        receipt = SubjectReceipt.model_validate(value)
    except ValidationError:
        return ReportSubjectOut()
    if (
        receipt.instructor_id != manuscript.instructor_id
        or receipt.manuscript_id != manuscript.id
        or receipt.check_run_id != run.id
        or run.manuscript_id != manuscript.id
        or receipt.source_sha256 != manuscript.content_hash
    ):
        return ReportSubjectOut()
    # The current group may have changed. Only the recorded assignment is used.
    return ReportSubjectOut(
        basis=receipt.basis, title=receipt.title, member_names=receipt.member_names
    )


async def record_check_subject(
    session: AsyncSession, run: CheckRun, manuscript: Manuscript
) -> None:
    """Called only during NEW run creation, under the manuscript row lock."""
    assignment = current_receipt(manuscript.group_confirmation, manuscript.group_id)
    selection = current_selection_receipt(
        manuscript.group_confirmation, manuscript.group_id,
        manuscript_id=manuscript.id, instructor_id=manuscript.instructor_id,
    )
    if assignment is None and selection is None:
        return
    owned_group = await session.scalar(select(Group.id).where(
        Group.id == manuscript.group_id, Group.instructor_id == manuscript.instructor_id,
    ))
    if owned_group is None:
        return
    values = {
        "version": 1,
        "instructor_id": manuscript.instructor_id,
        "manuscript_id": manuscript.id,
        "check_run_id": run.id,
        "group_id": owned_group,
        "source_sha256": manuscript.content_hash,
        "basis": "confirmed_assignment_at_check",
        "title": selection.effective.title if selection else assignment.effective.title,
        "member_names": (
            selection.effective.member_names if selection else assignment.effective.members
        ),
    }
    try:
        receipt = SubjectReceipt.model_validate(values)
    except ValidationError:
        # Legacy uploads without a recorded source hash have no bound identity.
        return
    await write_audit_event(
        session, event_type=SUBJECT_EVENT, check_run_id=run.id,
        manuscript_id=manuscript.id, payload=receipt.model_dump(mode="json"),
    )


async def recorded_check_subject(
    session: AsyncSession, run: CheckRun, manuscript: Manuscript
) -> ReportSubjectOut:
    events = list((await session.scalars(select(AuditLog).where(
        AuditLog.check_run_id == run.id, AuditLog.event_type == SUBJECT_EVENT,
    ).limit(2))).all())
    if (
        len(events) != 1
        or events[0].manuscript_id != manuscript.id
        or events[0].created_at != run.created_at
    ):
        return ReportSubjectOut()
    return subject_from_receipt(events[0].payload, run=run, manuscript=manuscript)
