"""V-085 (F1): explicit saved-group identity, separate from proposal matching."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_event
from app.db import DbInt, DbText
from app.errors import GroupAssignmentChangedError, NotFoundError, SavedGroupChangedError
from app.models.enums import ManuscriptOrigin
from app.models.group import Group, GroupMember, Program
from app.models.manuscript import Manuscript


class SavedGroupFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: PositiveInt
    name: str
    member_names: list[str]
    program_id: PositiveInt | None
    program: str | None
    title: str | None


def facts_fingerprint(instructor_id: int, facts: SavedGroupFacts) -> str:
    """Bind exact displayed values to owner and identity; this is not permission."""
    encoded = json.dumps(
        {"instructor_id": instructor_id, "group": facts.model_dump(mode="json")},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SavedGroupOut(SavedGroupFacts):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class SelectGroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    saved_group_id: Annotated[DbInt, Field(gt=0)]
    fingerprint: DbText = Field(pattern=r"^[0-9a-f]{64}$")
    # Required even when NULL: the upload's display label is not the current FK.
    expected_group_id: Annotated[DbInt, Field(gt=0)] | None


class SelectionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[2]
    manuscript_id: PositiveInt
    instructor_id: PositiveInt
    assigned_group_id: PositiveInt
    request: SelectGroupRequest
    effective: SavedGroupOut

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Selection receipt version must be an integer.")
        return value

    @model_validator(mode="after")
    def bound_snapshot(self) -> "SelectionReceipt":
        facts = SavedGroupFacts.model_validate(self.effective.model_dump(exclude={"fingerprint"}))
        if not (
            self.assigned_group_id == self.request.saved_group_id == self.effective.id
            and self.request.fingerprint == self.effective.fingerprint
            and self.effective.fingerprint == facts_fingerprint(self.instructor_id, facts)
        ):
            raise ValueError("Selection receipt does not bind its saved group facts.")
        return self


class SelectGroupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["select_saved_group"] = "select_saved_group"
    manuscript_id: PositiveInt
    outcome: Literal["selected", "unchanged"]
    receipt: SelectionReceipt


class PriorAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    group_id: PositiveInt | None
    group_label: str = Field(min_length=1)


class SelectionAuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    instructor_id: PositiveInt
    manuscript_id: PositiveInt
    before: PriorAssignment
    selection: SelectionReceipt

    @model_validator(mode="after")
    def bound_action(self) -> "SelectionAuditEvent":
        if (
            self.instructor_id != self.selection.instructor_id
            or self.manuscript_id != self.selection.manuscript_id
            or self.before.group_id != self.selection.request.expected_group_id
        ):
            raise ValueError("Selection audit does not bind its prior assignment and actor.")
        return self


def selection_audit_event(
    value: object, *, manuscript_id: int, instructor_id: int
) -> SelectionAuditEvent | None:
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int:
        return None
    try:
        event = SelectionAuditEvent.model_validate(value)
    except ValidationError:
        return None
    if event.manuscript_id != manuscript_id or event.instructor_id != instructor_id:
        return None
    return event


def current_selection_receipt(
    value: object, group_id: int | None, *, manuscript_id: int, instructor_id: int
) -> SelectionReceipt | None:
    if not isinstance(value, dict) or type(value.get("version")) is not int:
        return None
    try:
        receipt = SelectionReceipt.model_validate(value)
    except ValidationError:
        return None
    if (
        receipt.assigned_group_id != group_id
        or receipt.manuscript_id != manuscript_id
        or receipt.instructor_id != instructor_id
    ):
        return None
    return receipt


async def _saved_snapshot(session: AsyncSession, group: Group, *, lock: bool) -> SavedGroupOut:
    program_query = select(Program.name).where(Program.id == group.program_id)
    members_query = (
        select(GroupMember.name).where(GroupMember.group_id == group.id).order_by(GroupMember.id)
    )
    if lock:
        # The parent Group lock coordinates with proposal member-union writers
        # and FK insert checks; child locks also protect existing member rows.
        program_query = program_query.with_for_update(read=True)
        members_query = members_query.with_for_update(read=True)
    facts = SavedGroupFacts(
        id=group.id,
        name=group.name,
        member_names=list((await session.scalars(members_query)).all()),
        program_id=group.program_id,
        program=await session.scalar(program_query) if group.program_id is not None else None,
        title=group.title,
    )
    return SavedGroupOut(
        **facts.model_dump(), fingerprint=facts_fingerprint(group.instructor_id, facts)
    )


async def get_saved_group(
    session: AsyncSession, group_id: int, instructor_id: int
) -> SavedGroupOut:
    group = await session.scalar(
        select(Group)
        .where(Group.id == group_id, Group.instructor_id == instructor_id)
        .execution_options(populate_existing=True)
    )
    if group is None:
        raise NotFoundError(f"No saved group with id {group_id}.")
    return await _saved_snapshot(session, group, lock=False)


async def select_saved_group(
    session: AsyncSession,
    manuscript_id: int,
    instructor_id: int,
    request: SelectGroupRequest,
) -> SelectGroupResponse:
    manuscript = await session.scalar(
        select(Manuscript)
        .where(
            Manuscript.id == manuscript_id,
            Manuscript.instructor_id == instructor_id,
            Manuscript.origin == ManuscriptOrigin.check_upload,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if manuscript is None:
        raise NotFoundError(f"No manuscript with id {manuscript_id}.")
    if manuscript.group_id is not None:
        current_owned_id = await session.scalar(
            select(Group.id).where(
                Group.id == manuscript.group_id, Group.instructor_id == instructor_id
            )
        )
        if current_owned_id is None:
            raise NotFoundError("This manuscript's group is unavailable.")
    group = await session.scalar(
        select(Group)
        .where(Group.id == request.saved_group_id, Group.instructor_id == instructor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if group is None:
        raise NotFoundError(f"No saved group with id {request.saved_group_id}.")
    snapshot = await _saved_snapshot(session, group, lock=True)
    if snapshot.fingerprint != request.fingerprint:
        raise SavedGroupChangedError(
            "This saved group's details changed. "
            "Refresh its details and review them before assigning."
        )
    previous = current_selection_receipt(
        manuscript.group_confirmation,
        manuscript.group_id,
        manuscript_id=manuscript_id,
        instructor_id=instructor_id,
    )
    if previous is not None and previous.request == request and previous.effective == snapshot:
        await session.commit()
        return SelectGroupResponse(
            manuscript_id=manuscript_id, outcome="unchanged", receipt=previous
        )
    if manuscript.group_id != request.expected_group_id:
        raise GroupAssignmentChangedError(
            "This manuscript's group assignment changed. "
            "Check the saved assignment before choosing a group again."
        )
    receipt = SelectionReceipt(
        version=2,
        manuscript_id=manuscript_id,
        instructor_id=instructor_id,
        assigned_group_id=group.id,
        request=request,
        effective=snapshot,
    )
    before = {"group_id": manuscript.group_id, "group_label": manuscript.group_label}
    manuscript.group_id = group.id
    manuscript.group_label = group.name
    manuscript.group_confirmation = receipt.model_dump(mode="json")
    await write_audit_event(
        session,
        event_type="manuscript_group_selected",
        check_run_id=None,
        manuscript_id=manuscript_id,
        payload={
            "schema_version": 1,
            "instructor_id": instructor_id,
            "manuscript_id": manuscript_id,
            "before": before,
            "selection": receipt.model_dump(mode="json"),
        },
    )
    await session.commit()
    return SelectGroupResponse(manuscript_id=manuscript_id, outcome="selected", receipt=receipt)
