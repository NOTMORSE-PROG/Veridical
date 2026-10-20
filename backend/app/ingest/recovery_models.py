"""F1 recovery proposals and source mappings, separate from the native ledger."""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Rect = tuple[float, float, float, float]


class RecoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, serialize_by_alias=True)


class LayoutBox(RecoveryModel):
    left: float = Field(alias="l")
    top: float = Field(alias="t")
    right: float = Field(alias="r")
    bottom: float = Field(alias="b")
    coord_origin: Literal["TOPLEFT", "BOTTOMLEFT"]

    def top_left(self, height: float) -> Rect:
        if self.coord_origin == "BOTTOMLEFT":
            return self.left, height - self.top, self.right, height - self.bottom
        return self.left, self.top, self.right, self.bottom


class LayoutCaption(RecoveryModel):
    text: str
    bbox: LayoutBox
    page: int = Field(gt=0)


class LayoutCell(RecoveryModel):
    text: str
    bbox: LayoutBox | None
    row_start: int = Field(ge=0)
    row_end: int = Field(gt=0)
    col_start: int = Field(ge=0)
    col_end: int = Field(gt=0)
    column_header: bool = False
    row_header: bool = False


class LayoutTable(RecoveryModel):
    region_id: str
    bbox: LayoutBox
    rows: int = Field(gt=0)
    cols: int = Field(gt=0)
    cells: list[LayoutCell]
    captions: list[LayoutCaption] = Field(default_factory=list)


class LayoutFigure(RecoveryModel):
    region_id: str
    bbox: LayoutBox
    captions: list[LayoutCaption] = Field(default_factory=list)


class LayoutPage(RecoveryModel):
    page: int = Field(gt=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    status: Literal["complete", "unavailable"]
    reason: str | None = None
    tables: list[LayoutTable] = Field(default_factory=list)
    figures: list[LayoutFigure] = Field(default_factory=list)


class LayoutReply(RecoveryModel):
    schema_version: Literal[1] = 1
    source_sha256: str
    request_sha256: str
    manifest_sha256: str
    versions: dict[str, str]
    pages: list[LayoutPage]


class NativeSourceSpan(RecoveryModel):
    """Exact native characters and their existing whole-line rectangle."""

    page: int = Field(gt=0)
    block_index: int = Field(ge=0)
    source_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str
    bbox: Rect

    @field_validator("bbox")
    @classmethod
    def valid_rectangle(cls, value: Rect) -> Rect:
        if not all(math.isfinite(v) for v in value):
            raise ValueError("Source rectangle is not finite")
        if value[0] >= value[2] or value[1] >= value[3]:
            raise ValueError("Source rectangle has no area")
        return value


class RecoveredCell(RecoveryModel):
    row_start: int
    row_end: int
    col_start: int
    col_end: int
    proposed_text: str
    proposed_bbox: Rect | None
    column_header: bool
    row_header: bool
    status: Literal["source_mapped", "unresolved", "empty"]
    sources: list[NativeSourceSpan] = Field(default_factory=list)


class RecoveredCaption(RecoveryModel):
    proposed_text: str
    sources: list[NativeSourceSpan] = Field(default_factory=list)


class RecoveredTable(RecoveryModel):
    region_id: str
    page: int
    proposed_bbox: Rect
    rows: int
    cols: int
    cells: list[RecoveredCell]
    captions: list[RecoveredCaption]
    status: Literal["source_mapped", "partial"]
    reasons: list[str]
    unmapped_source_ids: list[str]
    # Source mapping is not validation of statistical assumptions or context.
    analytical_status: Literal["not_assessed"] = "not_assessed"


class RecoveredFigure(RecoveryModel):
    region_id: str
    page: int
    proposed_bbox: Rect
    captions: list[RecoveredCaption]
    analytical_status: Literal["not_assessed"] = "not_assessed"


class RecoveryPage(RecoveryModel):
    page: int
    status: Literal["processed", "unavailable"]
    reason: str | None = None
    tables: list[RecoveredTable] = Field(default_factory=list)
    figures: list[RecoveredFigure] = Field(default_factory=list)


class RecoveryEnvelope(RecoveryModel):
    schema_version: Literal[1] = 1
    adapter: Literal["native_line_mapping_v1"] = "native_line_mapping_v1"
    source_sha256: str
    native_sha256: str
    request_sha256: str
    manifest_sha256: str
    versions: dict[str, str] = Field(default_factory=dict)
    requested_pages: list[int]
    pages: list[RecoveryPage]
    # Retain the untrusted proposal, so persisted evidence can be re-derived
    # from canonical source instead of trusting saved mapping/status fields.
    proposal: LayoutReply | None = None
    peak_process_rss_bytes: int = 0
    elapsed_seconds: float = 0
