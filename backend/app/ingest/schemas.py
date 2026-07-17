"""Ingestion output models (F1.1/F1.4).

`ExtractionResult` is the raw-store payload (everything later checks need to
navigate and cite the document); `SectionTree` alone is persisted to
`manuscript.section_tree` (JSONB) — this module owns that shape
(schema_version guards future evolution).
"""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

Bbox = tuple[float, float, float, float]


@dataclass
class Line:
    """One text line as laid out on the page — internal unit for heading
    detection and furniture stripping; not persisted."""

    page: int  # 1-based, like every page anchor in the system
    block_no: int
    bbox: Bbox
    text: str  # normalized (see normalize.py)
    max_size: float
    bold_ratio: float  # fraction of characters in bold spans
    is_furniture: bool = field(default=False)


class TextBlock(BaseModel):
    """A citable text unit; every excerpt anchor points at (page, bbox)."""

    page: int
    bbox: Bbox
    text: str
    max_font_size: float
    bold_ratio: float
    # Repeating page furniture (running headers/footers, bare page numbers)
    # is kept but flagged, so checks can skip it without us silently
    # deleting document content.
    is_furniture: bool = False


class ImageBlock(BaseModel):
    """Inventory entry for an embedded image — V-007's work queue."""

    page: int
    bbox: Bbox


class PageGeometry(BaseModel):
    """Per-page layout facts for the structural checks (F3.2)."""

    page: int
    width: float
    height: float
    rotation: int
    # Extent of ALL text on the page (furniture included — the margin
    # checker decides what counts); None on textless pages.
    text_bbox: Bbox | None = None
    # (left, top, right, bottom) distances from text_bbox to the page edge,
    # in points. None when there is no text.
    margins: tuple[float, float, float, float] | None = None


class SectionNode(BaseModel):
    """One heading in the chapter tree. `level` 1 = chapter."""

    title: str
    level: int
    page: int
    numbering: str | None = None  # e.g. "2" or "2.1.3"; None for unnumbered
    children: list["SectionNode"] = Field(default_factory=list)


class SectionTree(BaseModel):
    schema_version: int = 1
    # Honest provenance: which strategy produced the tree.
    source: Literal["embedded_toc", "heuristics", "none"]
    nodes: list[SectionNode] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    page_count: int
    # True = little/no selectable text (likely a scan): NOT an error state;
    # the pipeline continues with limited checks (F1.7).
    image_only: bool
    text_chars: int  # selectable non-furniture characters, drives image_only
    section_tree: SectionTree
    blocks: list[TextBlock]
    images: list[ImageBlock]
    geometry: list[PageGeometry]
