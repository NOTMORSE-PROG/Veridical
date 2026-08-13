"""Ingestion output models (F1.1/F1.2/F1.4) — ONE contract for every format.

`ExtractionResult` is the raw-store payload (everything later checks need to
navigate and cite the document); `SectionTree` alone is persisted to
`manuscript.section_tree` (JSONB) — this module owns that shape
(schema_version guards future evolution).

Anchors: PDFs cite pages (`page`, `bbox`); DOCX has no fixed pages until
rendered, so it cites body-item ordinals (`paragraph`) and reports cite the
section path instead of a page number. `anchor_kind` says which applies —
downstream code handles anchors through this one contract and never
branches on file format.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Bbox = tuple[float, float, float, float]


@dataclass
class Line:
    """One text line as laid out on a PDF page — internal unit for heading
    detection and furniture stripping; not persisted."""

    page: int  # 1-based, like every page anchor in the system
    block_no: int
    bbox: Bbox
    text: str  # normalized (see normalize.py)
    max_size: float
    bold_ratio: float  # fraction of characters in bold spans
    is_furniture: bool = field(default=False)


class TextBlock(BaseModel):
    """A citable text unit; anchored by (page, bbox) or paragraph ordinal."""

    page: int | None = None  # PDF: 1-based page
    paragraph: int | None = None  # DOCX: 0-based body-item ordinal
    bbox: Bbox | None = None  # PDF only: layout position
    text: str
    max_font_size: float
    bold_ratio: float
    # Repeating page furniture (running headers/footers, bare page numbers)
    # is kept but flagged, so checks can skip it without us silently
    # deleting document content. Always False for DOCX (headers/footers
    # live outside the body there and are never extracted).
    is_furniture: bool = False


class ImageBlock(BaseModel):
    """Inventory entry for an embedded image — V-007's work queue."""

    page: int | None = None
    paragraph: int | None = None
    bbox: Bbox | None = None


class TableBlock(BaseModel):
    """A table with rows/cells intact. `native` = read directly from the
    file (DOCX, F1.2); `vision` = recovered from an embedded image by the
    multimodal model (F1.3). Vision tables the model itself was not sure
    about carry `low_confidence` — F6's hard checks must skip those and
    the report says the image could not be reliably read."""

    page: int | None = None
    paragraph: int | None = None
    rows: list[list[str]]
    source: Literal["native", "vision"] = "native"
    low_confidence: bool = False
    caption: str | None = None


class EquationBlock(BaseModel):
    """An equation/statistical expression recovered from an embedded image
    (F1.3) — input for the F6 statistics extraction."""

    page: int | None = None
    paragraph: int | None = None
    latex: str
    low_confidence: bool = False


class PageGeometry(BaseModel):
    """Per-page (PDF) or per-Word-section (DOCX) layout facts for the
    structural checks (F3.2). All lengths in points."""

    page: int | None = None  # PDF: 1-based page number
    docx_section: int | None = None  # DOCX: 1-based Word section ordinal
    width: float
    height: float
    rotation: int = 0
    # PDF: extent of ALL text on the page (furniture included — the margin
    # checker decides what counts); None on textless pages and for DOCX.
    text_bbox: Bbox | None = None
    # (left, top, right, bottom) in points. PDF: derived from text_bbox vs
    # the page edge; DOCX: the declared section margins.
    margins: tuple[float, float, float, float] | None = None


class SectionNode(BaseModel):
    """One heading in the chapter tree. `level` 1 = chapter."""

    title: str
    level: int
    page: int | None = None
    paragraph: int | None = None
    numbering: str | None = None  # e.g. "2" or "2.1.3"; None for unnumbered
    children: list["SectionNode"] = Field(default_factory=list)


class SectionTree(BaseModel):
    schema_version: int = 1
    # Honest provenance: which strategy produced the tree.
    # embedded_toc = PDF outline metadata (validated against the pages);
    # styles = Word heading styles; heuristics = bold/size + numbering scan.
    source: Literal["embedded_toc", "styles", "heuristics", "none"]
    nodes: list[SectionNode] = Field(default_factory=list)


class IngestSummary(BaseModel):
    """Response body of POST /manuscripts/ingest — what the upload screen
    (4c/4f) needs to show; the full extraction lives in the raw store."""

    manuscript_id: int
    ingest_status: str
    page_count: int
    anchor_kind: Literal["page", "paragraph"]
    image_only: bool
    text_chars: int
    images: int
    tables: int
    equations: int
    citations: int
    vision_status: Literal["none", "done", "unavailable"]
    section_tree: "SectionTree"
    # User-facing notes (message templates) — e.g. the limited-checks note
    # for scans. Honest states, not errors.
    notes: list[str] = Field(default_factory=list)


class ManuscriptListItem(BaseModel):
    """One row of a manuscript picker/list (screens 4e/4f)."""

    id: int
    group_label: str
    # BUG-022: group_label defaults to "Ungrouped" and can't distinguish
    # two manuscripts alone; None for rows ingested before this column
    # existed.
    original_filename: str | None = None
    ingest_status: str
    # None unless ingest_status is "failed" AND the reason was captured
    # (BUG-016) — NULL, never fabricated, for pre-existing failed rows.
    ingest_failure_reason: str | None = None
    created_at: datetime
    # None until a check has been run against this manuscript at least
    # once; lets the dashboard table (V-021) link "view progress"/"open
    # report" without a second request per row.
    latest_check_run_id: int | None = None
    latest_check_run_status: str | None = None
    # The latest DONE run specifically, which can differ from
    # latest_check_run_id if a newer re-run failed or is still in
    # progress — lets the UI always link to the last known-good report
    # instead of it going dark under a superseding run (backend-critic
    # finding on BUG-012, V-055: the two "latest run" definitions used to
    # diverge silently between this endpoint and the dashboard's KPI
    # aggregation).
    latest_done_check_run_id: int | None = None
    # V-038 / ux-critic finding: without this, the dashboard gave no way
    # to tell which manuscripts had already been decided (approved/
    # returned/rejected) without opening every single report — a real
    # cost at defense-season density (~20 manuscripts). Sourced from the
    # SAME latest-done run `latest_done_check_run_id` points at, never a
    # different one, so the two can't silently disagree.
    latest_decision: str | None = None

    model_config = {"from_attributes": True}


class PaginatedManuscripts(BaseModel):
    """Server-side pagination from day one (V-021 edge case: 100+
    manuscripts in defense season) — never a single unbounded list."""

    items: list[ManuscriptListItem]
    total: int
    page: int
    page_size: int


class ExtractionResult(BaseModel):
    page_count: int  # DOCX: 0 — pages don't exist before rendering
    anchor_kind: Literal["page", "paragraph"]
    # True = little/no selectable text (likely a scan): NOT an error state;
    # the pipeline continues with limited checks (F1.7).
    image_only: bool
    text_chars: int  # selectable non-furniture characters, drives image_only
    section_tree: SectionTree
    blocks: list[TextBlock]
    images: list[ImageBlock]
    tables: list[TableBlock] = Field(default_factory=list)
    equations: list[EquationBlock] = Field(default_factory=list)
    geometry: list[PageGeometry] = Field(default_factory=list)
    # F1.3 vision pass over embedded images: "none" = no images selected,
    # "done" = ran, "unavailable" = no LLM client configured (honest state
    # — ingestion continues, image content simply stays unread).
    vision_status: Literal["none", "done", "unavailable"] = "none"
