"""Versioned native-cell placement proof; canonical source lines stay atomic."""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, field_validator, model_validator

SourceRectangle = tuple[StrictFloat, StrictFloat, StrictFloat, StrictFloat]


class TableCharacterRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_line_id: str
    raw_character_count: int = Field(gt=0, strict=True)
    raw_character_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def valid_range(self):
        if not self.start < self.end <= self.raw_character_count:
            raise ValueError("Native character range is outside its original source line")
        return self


class TableCellOrderWitness(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row: int = Field(ge=0, strict=True)
    column: int = Field(ge=0, strict=True)
    bbox: SourceRectangle
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_line_ids: list[str]
    character_runs: list[TableCharacterRun] = Field(default_factory=list)

    @field_validator("bbox")
    @classmethod
    def valid_rectangle(cls, value):
        if not all(math.isfinite(v) for v in value) or value[0] >= value[2] or value[1] >= value[3]:
            raise ValueError("Native cell must have a finite positive source rectangle")
        return value


class TableGridOrderWitness(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bbox: SourceRectangle
    row_count: int = Field(gt=0, strict=True)
    column_count: int = Field(gt=0, strict=True)
    cells: list[TableCellOrderWitness]

    @field_validator("bbox")
    @classmethod
    def valid_rectangle(cls, value):
        return TableCellOrderWitness.valid_rectangle(value)


class NativeTableOrderWitness(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["native_cells_v1"] = "native_cells_v1"
    page_bbox: SourceRectangle
    geometry_tolerance_pts: float = Field(ge=0, allow_inf_nan=False)
    row_tolerance_pts: float = Field(ge=0, allow_inf_nan=False)
    source_inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    character_inventory_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    tables: list[TableGridOrderWitness] = Field(min_length=1)
