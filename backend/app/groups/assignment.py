"""V-081 (F1): canonical assignment values and validated replay receipts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, PositiveInt, ValidationError

from app.groups.selection import current_selection_receipt
from app.groups.service import normalize_group_name


class AssignmentValues(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    members: list[str]
    program_id: PositiveInt | None
    title: str | None


class AssignmentRequest(AssignmentValues):
    expected_group_id: PositiveInt | None
    expected_provided: bool


class AssignmentReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    assigned_group_id: PositiveInt
    request: AssignmentRequest
    effective: AssignmentValues


def assignment_values(
    name: str, members: list[str], program_id: int | None, title: str | None
) -> AssignmentValues:
    return AssignmentValues(
        name=normalize_group_name(name),
        members=sorted({normalize_group_name(n) for n in members if n.strip()}),
        program_id=program_id,
        title=(title.strip() or None) if title else None,
    )


def current_receipt(value: object, group_id: int | None) -> AssignmentReceipt | None:
    if not isinstance(value, dict) or type(value.get("version")) is not int:
        return None
    try:
        receipt = AssignmentReceipt.model_validate(value)
    except ValidationError:
        return None
    return receipt if receipt.assigned_group_id == group_id else None


def confirmation_recorded(
    value: object, group_id: int | None, *, manuscript_id: int, instructor_id: int
) -> bool:
    """A historical confirmation is distinct from current group facts."""
    return current_receipt(value, group_id) is not None or current_selection_receipt(
        value, group_id, manuscript_id=manuscript_id, instructor_id=instructor_id
    ) is not None
