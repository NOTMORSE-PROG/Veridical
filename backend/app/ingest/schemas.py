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

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.db import DbInt
from app.ingest.native_math_models import NativeMathInventory
from app.ingest.native_table_models import NativeTableOrderWitness
from app.ingest.recovery_models import RecoveryEnvelope

Bbox = tuple[float, float, float, float]


class ManuscriptQueueStatus(StrEnum):
    """Stable queue vocabulary for the instructor's Review Desk (V-071 AC2)."""

    needs_attention = "needs_attention"
    ingestion_failed = "ingestion_failed"
    not_checked = "not_checked"
    checking = "checking"
    check_failed = "check_failed"
    cancelled = "cancelled"
    checked = "checked"
    decided = "decided"


class ManuscriptSort(StrEnum):
    """Server-side orderings exposed by ``GET /manuscripts`` (V-071 AC2)."""

    newest = "newest"
    oldest = "oldest"
    group_asc = "group_asc"
    needs_review_desc = "needs_review_desc"


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
    source_id: str | None = None


class SourceLineSpan(BaseModel):
    """Original line geometry and furniture status at character offsets in a block."""

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    bbox: Bbox
    max_font_size: float = Field(ge=0)
    bold_ratio: float = Field(ge=0, le=1)
    is_furniture: bool = False
    source_id: str | None = None


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
    source_line_ids: list[str] | None = None
    source_lines: list[SourceLineSpan] | None = None


class UriLink(BaseModel):
    """Untrusted PDF hyperlink and the visible text inside its source rectangle."""

    page: int = Field(gt=0)
    bbox: Bbox
    uri: str
    text: str

    @field_validator("bbox")
    @classmethod
    def finite_source_rectangle(cls, value: Bbox) -> Bbox:
        if not all(math.isfinite(coordinate) for coordinate in value):
            raise ValueError("Link rectangle must be finite")
        if value[0] >= value[2] or value[1] >= value[3]:
            raise ValueError("Link rectangle must have positive area")
        return value


class DocxImageSource(BaseModel):
    """Private original-package identity; never a client-supplied filesystem path."""

    relationship_id: str
    part_name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: Literal["image/png", "image/jpeg"]
    byte_length: int = Field(gt=0)
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)

    @field_validator("part_name")
    @classmethod
    def unambiguous_member_name(cls, value: str) -> str:
        if (
            not value
            or value.startswith("/")
            or "\\" in value
            or "\x00" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("Image part must name one normal package member")
        return value


class ImageBlock(BaseModel):
    """Inventory entry for an embedded image — V-007's work queue."""

    page: int | None = None
    paragraph: int | None = None
    bbox: Bbox | None = None
    docx_source: DocxImageSource | None = None
    source_alt_text: str | None = None
    display_unavailable_reason: (
        Literal["unsupported_drawing", "external_image", "invalid_image", "image_limit"] | None
    ) = None
    # How many touching PDF image pieces this image was merged from (BUG-532).
    # Left out of the stored form when 1, so an image that was never merged is
    # stored exactly as before and still matches crops recorded for it.
    piece_count: int = Field(default=1, ge=1, exclude_if=lambda count: count == 1)


class ImageReading(BaseModel):
    """Saved reading attempt, not proof that an image's meaning was verified."""

    image_index: int = Field(ge=0)
    status: Literal["not_selected", "failed", "invalid_response", "classified_only", "transcribed"]
    reason: (
        Literal[
            "not_attempted",
            "unsupported_source",
            "size_filter",
            "selection_limit",
            "vision_disabled",
            "client_unavailable",
            "api_down",
            "quota_exhausted",
            "crop_unavailable",
            "invalid_response",
            "content_not_transcribed",
            "low_confidence",
        ]
        | None
    ) = None
    attempted: bool = False
    kind: Literal["table", "equation", "chart", "other"] | None = None
    confidence: Literal["high", "low"] | None = None
    prompt_type: str | None = None
    prompt_version: str | None = None


class TableBlock(BaseModel):
    """A table with rows/cells intact. `native` = read directly from the
    file (DOCX, F1.2); `vision` = recovered from an embedded image by the
    multimodal model (F1.3). Vision tables the model itself was not sure
    about carry `low_confidence` — F6's hard checks must skip those and
    the report says the image could not be reliably read."""

    page: int | None = None
    paragraph: int | None = None
    bbox: Bbox | None = None
    rows: list[list[str]]
    source: Literal["native", "vision"] = "native"
    low_confidence: bool = False
    caption: str | None = None
    source_image_index: int | None = Field(default=None, ge=0)


class EquationBlock(BaseModel):
    """An equation/statistical expression recovered from an embedded image
    (F1.3). Retained transcription; F6 does not yet evaluate this representation."""

    page: int | None = None
    paragraph: int | None = None
    latex: str
    low_confidence: bool = False
    source_image_index: int | None = Field(default=None, ge=0)


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


class AnchoredValueOut(BaseModel):
    """A proposed value plus WHERE it came from (V-063 AC3) — the same
    evidence standard the rest of the product is held to."""

    value: str
    anchor: str


class TitlePageProposalOut(BaseModel):
    """V-063: a deterministic, evidence-anchored proposal for a
    manuscript's group/program — proposes only, never applies (AC2).
    `extraction_failed=True` means nothing usable was found at all
    (AC5); a partial proposal (e.g. a title with no members) is NOT a
    failure and is shown exactly as found, gaps included, never
    fabricated."""

    title: AnchoredValueOut | None
    short_name: AnchoredValueOut | None
    members: list[AnchoredValueOut] = Field(default_factory=list)
    program: AnchoredValueOut | None
    adviser: AnchoredValueOut | None
    extraction_failed: bool


class ExistingUploadOut(BaseModel):
    """A prior byte-identical upload owned by the authenticated caller."""

    manuscript_id: int
    created_at: datetime
    original_filename: str | None
    purged_at: datetime | None


class IngestSummary(BaseModel):
    """Response body of POST /manuscripts/ingest — what the upload screen
    (4c/4f) needs to show; the full extraction lives in the raw store."""

    manuscript_id: int
    # BUG-043: echoes back what was actually persisted, so a client that
    # sent group_label has a way to notice if it was silently dropped.
    group_label: str
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
    # V-063: the auto-proposed group/program, deterministically extracted
    # from the title page — the confirm dialog's own data source.
    group_proposal: TitlePageProposalOut
    # BUG-234: exact byte identity inside this instructor's account only.
    # This never reports a cross-account hash match and is not a semantic
    # similarity or manuscript-version judgment.
    existing_upload: ExistingUploadOut | None = None


class ConfirmGroupRequest(BaseModel):
    """PATCH /manuscripts/{id}/group body (V-063 AC2): every field is
    exactly what the instructor confirmed or edited — this endpoint never
    reads back into the extraction itself, only what's sent here."""

    group_name: str = Field(min_length=1)
    member_names: list[str] = Field(default_factory=list)
    program_id: DbInt | None = Field(default=None, gt=0, strict=True)
    # V-066: the full capstone title shown (read-only, not editable) in the
    # confirm dialog alongside group_name/members -- carried through here
    # exactly like program_id so it can be persisted, same "only set on a
    # NEWLY created group" rule as program_id (see
    # `app.ingest.service.confirm_manuscript_group`).
    title: str | None = None
    # Omission supports older clients; explicit NULL expects no current FK.
    expected_group_id: DbInt | None = Field(default=None, gt=0, strict=True)


class CurrentGroupOut(BaseModel):
    id: int
    name: str
    member_names: list[str]
    program_id: int | None
    program: str | None
    title: str | None


class ManuscriptGroupOut(BaseModel):
    manuscript_id: int
    group: CurrentGroupOut | None
    confirmation: Literal["recorded", "not_recorded"]


class ConfirmGroupResponse(BaseModel):
    group_id: int
    group_label: str
    program: str | None
    title: str | None = None
    # True = an existing group was matched (AC4); False = a new one was
    # created — lets the confirm dialog say which happened, honestly.
    matched: bool
    outcome: Literal["created", "matched", "unchanged"]


class ManuscriptListItem(BaseModel):
    """One row of a manuscript picker/list (screens 4e/4f)."""

    id: int
    purged_at: datetime | None = None
    group_label: str
    # BUG-022: group_label defaults to "Ungrouped" and can't distinguish
    # two manuscripts alone; None for rows ingested before this column
    # existed.
    original_filename: str | None = None
    # V-062 (AC5): the manuscript's group's program, if one has been set.
    # NULL/None = "Not set" -- never guessed, same convention as
    # `ingest_failure_reason`. Most rows will be None until an instructor
    # (or V-063's title-page inference) sets a program on the group.
    program: str | None = None
    ingest_status: str
    # None unless ingest_status is "failed" AND the reason was captured
    # (BUG-016) — NULL, never fabricated, for pre-existing failed rows.
    ingest_failure_reason: str | None = None
    # BUG-066: the specific vetted message the failing check computed (real
    # MB/page numbers), when one exists — None for the catch-all
    # `extraction_failed` reason (no safe specific text) and for rows
    # ingested before this column existed. The client falls back to a
    # generic per-reason sentence when this is None; never fabricates one.
    ingest_failure_detail: str | None = None
    created_at: datetime
    # None until a check has been run against this manuscript at least
    # once; lets the dashboard table (V-021) link "view progress"/"open
    # report" without a second request per row.
    latest_check_run_id: int | None = None
    latest_check_run_status: str | None = None
    # The latest DONE run specifically, which can differ from
    # latest_check_run_id if a newer re-run failed or is still in
    # progress — lets the UI always link to the last known-good report
    # instead of it going dark under a superseding run (backend review
    # finding on BUG-012, V-055: the two "latest run" definitions used to
    # diverge silently between this endpoint and the dashboard's KPI
    # aggregation).
    latest_done_check_run_id: int | None = None
    # V-038 / UX review finding: without this, the dashboard gave no way
    # to tell which manuscripts had already been decided (approved/
    # returned/rejected) without opening every single report — a real
    # cost at defense-season density (~20 manuscripts). Sourced from the
    # SAME latest-done run `latest_done_check_run_id` points at, never a
    # different one, so the two can't silently disagree.
    latest_decision: str | None = None
    # The system's named readiness band for the same latest completed run.
    # It is display context for the Review Desk, never the instructor's
    # decision and never a percentage headline.
    latest_readiness: str | None = None
    # V-041 / UX review finding (P1, live-reproduced): without this, a
    # bulk re-run UI has no signal to exclude a manuscript whose latest
    # done run was under a COMPLETELY UNRELATED rubric family -- it would
    # default such a manuscript to selected and could silently submit it
    # for grading against a rubric it was never checked against, burning
    # real quota (D-001) with no indication anywhere in the row. Sourced
    # from the SAME latest-done run as `latest_done_check_run_id`.
    latest_done_rubric_family_id: str | None = None
    # V-071 (AC1): how many of the latest done run's criteria are escalated
    # and awaiting the instructor's review -- lets the dashboard point
    # directly at the manuscript holding them instead of just a total count
    # with no row to follow.
    escalations_awaiting_review: int = 0

    model_config = {"from_attributes": True}


class PaginatedManuscripts(BaseModel):
    """Server-side pagination from day one (V-021 edge case: 100+
    manuscripts in defense season) — never a single unbounded list."""

    items: list[ManuscriptListItem]
    total: int
    page: int
    page_size: int


class DismissedManuscriptOut(BaseModel):
    """A failed upload retained outside the active Review Desk (V-071 AC4)."""

    manuscript_id: int
    dismissed_at: datetime


class PageReadingOrder(BaseModel):
    """Geometric policy outcome, not a measured semantic accuracy claim."""

    page: int = Field(gt=0)
    strategy: Literal["native_regions_v1"] = "native_regions_v1"
    status: Literal["ordered", "unresolved"]
    reason: str | None = None
    source_line_count: int = Field(ge=0)
    # None when a resource limit or invalid source prevented full inventory.
    source_inventory_sha256: str | None
    ordered_source_line_ids: list[str]
    table_order: NativeTableOrderWitness | None = None


class FigureReading(BaseModel):
    """Non-scoring visual observations; never numeric or criterion assessment."""

    image_index: int = Field(ge=0)
    status: Literal["not_selected", "unavailable", "needs_review", "available"]
    reason: str
    source_anchor: str | None = None
    source_binding: dict = Field(default_factory=dict)
    source_caption: str | None = None
    caption_anchor: str | None = None
    kind: str | None = None
    visible_content: str | None = None
    visible_labels: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    support_reason: str | None = None
    schema_version: Literal["v1"] = "v1"


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
    image_reading_version: Literal["v1"] | None = None
    image_readings: list[ImageReading] | None = None
    figure_readings: list[FigureReading] | None = None
    docx_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    # Retain malformed legacy envelopes for explicit unavailable projection;
    # consumers validate centrally rather than replacing them with zero.
    native_math_inventory: NativeMathInventory | dict | None = None
    # Transient expected manuscript identity supplied by an authorized consumer;
    # never written into the extraction or accepted as a stored source witness.
    analytical_source_sha256: str | None = Field(default=None, exclude=True)
    # Transient: pages an analysis copy leaves out because only their reading
    # order is unresolved (their text is absent from that copy). Never stored.
    unread_pages: list[int] | None = Field(default=None, exclude=True)
    docx_omitted_image_occurrences: int = Field(default=0, ge=0)
    tables: list[TableBlock] = Field(default_factory=list)
    equations: list[EquationBlock] = Field(default_factory=list)
    geometry: list[PageGeometry] = Field(default_factory=list)
    uri_links: list[UriLink] | None = None
    uri_links_complete: bool = False
    reading_order: list[PageReadingOrder] | None = None
    # Optional local layout recovery. Native text/order/coordinates above remain
    # canonical; a mapped table does not settle document-wide coverage.
    recovery: RecoveryEnvelope | None = None
    # F1.3 vision pass over embedded images: "none" = no images selected,
    # "done" = ran, "unavailable" = no LLM client configured (honest state
    # — ingestion continues, image content simply stays unread).
    vision_status: Literal["none", "done", "unavailable"] = "none"
