"""Gemini multimodal reading of embedded images (F1.3) — tables/equations
that live as screenshots in the manuscript.

Every call is priced before it is made (D-001): images are filtered
(decorative-size floor), prioritized (Results/Analysis-type chapters first
— that is where the numbers F6 needs live), and hard-capped
(INGEST_VISION_MAX_IMAGES). The model call goes through the LLMClient
interface only; in fake-LLM mode the fixture answers deterministically and
no network exists. The real client arrives with V-009 and slots in behind
the same interface.

The model's own uncertainty is preserved: `confidence: "low"` becomes
`low_confidence=True` on the merged block — F6 hard checks skip those and
the report says the image could not be reliably read (charter rule 3;
never present an unsure transcription as fact). Charts are classified and
skipped, never converted into invented tables.

BUG-160 deliberately left this path unguarded by `detect_injection_signal`:
the call here sends an IMAGE with a fixed prompt, not interpolated
manuscript text, so there is no text string for a regex to scan before the
call. This is a coupling assumption, not a structural guarantee (confirmed
2026-09-05: `TableBlock.rows`/`caption` are never fed into another LLM
call anywhere in the codebase today) — if a future change folds this
module's extracted table/caption text back into a semantic-grading or
integrity-check prompt, that new call site needs its own guard; this
docstring note doesn't grant one retroactively.
"""

import asyncio
from pathlib import Path
from typing import Any, Literal

import pymupdf
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.ingest.patterns import HeadingPatterns
from app.ingest.schemas import (
    EquationBlock,
    ExtractionResult,
    ImageBlock,
    SectionNode,
    TableBlock,
)
from app.llm.base import LLMClient

PROMPT_TYPE = "image_table_extraction"
PROMPT_VERSION = "v1"
_PROMPT_FILE = Path(__file__).parent / "prompts" / f"{PROMPT_TYPE}_{PROMPT_VERSION}.txt"


class VisionReading(BaseModel):
    """The response contract the prompt demands — anything that doesn't
    validate is treated as an unreadable image, not trusted partially."""

    kind: Literal["table", "equation", "chart", "other"]
    confidence: Literal["high", "low"]
    caption: str | None = None
    rows: list[list[str]] | None = None
    latex: str | None = None


async def read_images(
    result: ExtractionResult,
    file_path: Path,
    llm: LLMClient,
    patterns: HeadingPatterns,
    settings: Settings,
) -> int:
    """Run the vision pass, merging tables/equations into `result` in
    place. Returns the number of LLM calls made (quota accounting)."""
    selected = select_images(result, patterns, settings)
    if not selected:
        result.vision_status = "none"
        return 0

    prompt = _PROMPT_FILE.read_text(encoding="utf-8")
    loop = asyncio.get_running_loop()
    calls = 0
    for image in selected:
        # Rendering is CPU-bound — off the event loop (CODING.md §2).
        png = await loop.run_in_executor(None, _crop, file_path, image, settings)
        response = await llm.complete(
            PROMPT_TYPE,
            prompt,
            prompt_version=PROMPT_VERSION,
            images=[png],
        )
        calls += 1
        _merge(result, image, response)
    result.vision_status = "done"
    return calls


def select_images(
    result: ExtractionResult, patterns: HeadingPatterns, settings: Settings
) -> list[ImageBlock]:
    """Bounded, prioritized selection — the quota governor for F1.3.

    PDF-only for now: DOCX image blocks carry no croppable region or bytes
    yet (recorded V-007 gap; needs image-part extraction from the package),
    so an LLM call for them would spend quota on nothing."""
    sized = [
        img
        for img in result.images
        if img.bbox is not None and _area(img.bbox) >= settings.ingest_vision_min_area_pts
    ]
    priority_pages = _priority_page_ranges(result.section_tree.nodes, patterns)

    def is_priority(img: ImageBlock) -> bool:
        return img.page is not None and any(a <= img.page <= b for a, b in priority_pages)

    ordered = sorted(sized, key=lambda img: not is_priority(img))
    return ordered[: settings.ingest_vision_max_images]


def _area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _priority_page_ranges(
    nodes: list[SectionNode], patterns: HeadingPatterns
) -> list[tuple[int, int]]:
    """Page spans of level-1 sections whose titles mention a priority
    keyword (results/analysis/...). Span ends where the next level-1
    section starts; the last one runs to the end of the document."""
    top = [n for n in nodes if n.page is not None]
    ranges: list[tuple[int, int]] = []
    for i, node in enumerate(top):
        title = node.title.casefold()
        if any(k in title for k in patterns.vision_priority_sections):
            end = top[i + 1].page - 1 if i + 1 < len(top) else 10**9
            ranges.append((node.page, max(node.page, end)))
    return ranges


def _crop(file_path: Path, image: ImageBlock, settings: Settings) -> bytes:
    with pymupdf.open(str(file_path)) as doc:
        page = doc[image.page - 1]
        pix = page.get_pixmap(clip=pymupdf.Rect(*image.bbox), dpi=settings.ingest_vision_crop_dpi)
        return pix.tobytes("png")


def _merge(result: ExtractionResult, image: ImageBlock, response: dict[str, Any]) -> None:
    try:
        reading = VisionReading.model_validate(response)
    except ValidationError:
        # A malformed model answer is an unreadable image, nothing more.
        return
    low = reading.confidence == "low"
    if reading.kind == "table" and reading.rows:
        result.tables.append(
            TableBlock(
                page=image.page,
                paragraph=image.paragraph,
                rows=reading.rows,
                source="vision",
                low_confidence=low,
                caption=reading.caption,
            )
        )
    elif reading.kind == "equation" and reading.latex:
        result.equations.append(
            EquationBlock(
                page=image.page,
                paragraph=image.paragraph,
                latex=reading.latex,
                low_confidence=low,
            )
        )
    # charts/other: classified and left alone — no invented data.
