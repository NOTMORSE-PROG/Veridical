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

from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.errors import ApiDownError, QuotaExhaustedError
from app.ingest.admission import PreparationLease
from app.ingest.image_source import _crop as _native_crop
from app.ingest.image_source import _selected_indices, readable_size
from app.ingest.patterns import HeadingPatterns
from app.ingest.schemas import (
    EquationBlock,
    ExtractionResult,
    ImageBlock,
    ImageReading,
    TableBlock,
)
from app.llm import LLMNotConfiguredError
from app.llm.base import LLMClient

PROMPT_TYPE = "image_table_extraction"
PROMPT_VERSION = "v1"
_PROMPT_FILE = Path(__file__).parent / "prompts" / f"{PROMPT_TYPE}_{PROMPT_VERSION}.txt"


def _crop(file_path: Path, image: ImageBlock, settings: Settings) -> bytes:
    if settings.native_compute_url:
        from app.ingest.compute_client import remote_raster

        return remote_raster(file_path, image, settings)[0]
    return _native_crop(file_path, image, settings)


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
    *,
    preparation_lease: PreparationLease | None = None,
) -> int:
    """Run the vision pass, merging tables/equations into `result` in
    place. Returns the number of LLM calls made (quota accounting)."""
    selected = prepare_image_readings(result, patterns, settings)
    if not selected:
        result.vision_status = "none"
        return 0

    prompt = _PROMPT_FILE.read_text(encoding="utf-8")
    loop = asyncio.get_running_loop()
    calls = 0
    for index in selected:
        image = result.images[index]
        reading = result.image_readings[index]
        # Rendering is CPU-bound — off the event loop (CODING.md §2).
        try:
            png = (
                await preparation_lease.run(_crop, file_path, image, settings)
                if preparation_lease is not None
                else await loop.run_in_executor(None, _crop, file_path, image, settings)
            )
        except (ValueError, RuntimeError, IndexError):
            reading.status, reading.reason = "failed", "crop_unavailable"
            continue
        reading.attempted = True
        reading.prompt_type, reading.prompt_version = PROMPT_TYPE, PROMPT_VERSION
        calls += 1
        try:
            response = await llm.complete(
                PROMPT_TYPE,
                prompt,
                prompt_version=PROMPT_VERSION,
                images=[png],
            )
        except (ApiDownError, QuotaExhaustedError, LLMNotConfiguredError) as exc:
            reason = (
                "client_unavailable"
                if isinstance(exc, LLMNotConfiguredError)
                else "quota_exhausted"
                if isinstance(exc, QuotaExhaustedError)
                else "api_down"
            )
            reading.status, reading.reason = "failed", reason
            mark_pending_unavailable(result, reason)
            result.vision_status = "unavailable"
            raise
        _merge(result, image, response, reading)
    result.vision_status = "done"
    return calls


def select_images(
    result: ExtractionResult, patterns: HeadingPatterns, settings: Settings
) -> list[ImageBlock]:
    """Bounded, prioritized selection — the quota governor for F1.3.

    PDF-only: native DOCX display does not opt its images into this crop path."""
    return [result.images[index] for index in _selected_indices(result, patterns, settings)]


def prepare_image_readings(
    result: ExtractionResult,
    patterns: HeadingPatterns,
    settings: Settings,
    *,
    enabled: bool = True,
) -> list[int]:
    selected = _selected_indices(result, patterns, settings) if enabled else []
    result.image_reading_version = "v1"
    result.image_readings = []
    for index, image in enumerate(result.images):
        if not enabled:
            reason = "vision_disabled"
        elif result.anchor_kind != "page" or image.page is None or image.bbox is None:
            reason = "unsupported_source"
        elif not readable_size(image, settings):
            reason = "size_filter"
        else:
            reason = "not_attempted" if index in selected else "selection_limit"
        result.image_readings.append(
            ImageReading(image_index=index, status="not_selected", reason=reason)
        )
    return selected


def mark_pending_unavailable(result: ExtractionResult, reason: str) -> None:
    for reading in result.image_readings or []:
        if reading.status == "not_selected" and reading.reason == "not_attempted":
            reading.reason = reason


def _merge(
    result: ExtractionResult,
    image: ImageBlock,
    response: dict[str, Any],
    entry: ImageReading,
) -> None:
    try:
        reading = VisionReading.model_validate(response)
        if reading.kind == "table" and (
            not reading.rows
            or not any(cell.strip() for row in reading.rows for cell in row)
            or reading.latex
        ):
            raise ValueError("Missing or incompatible table content")
        if reading.kind == "equation" and (not (reading.latex or "").strip() or reading.rows):
            raise ValueError("Missing or incompatible equation content")
        if reading.kind in {"chart", "other"} and (reading.rows or reading.latex):
            raise ValueError("Classification carries incompatible content")
    except (ValidationError, ValueError):
        # A malformed model answer is an unreadable image, nothing more.
        entry.status, entry.reason = "invalid_response", "invalid_response"
        return
    entry.kind, entry.confidence = reading.kind, reading.confidence
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
                source_image_index=entry.image_index,
            )
        )
    elif reading.kind == "equation" and reading.latex:
        result.equations.append(
            EquationBlock(
                page=image.page,
                paragraph=image.paragraph,
                latex=reading.latex,
                low_confidence=low,
                source_image_index=entry.image_index,
            )
        )
    if reading.kind in {"table", "equation"}:
        entry.status = "transcribed"
        entry.reason = "low_confidence" if low else None
    else:
        entry.status, entry.reason = "classified_only", "content_not_transcribed"
    # Charts/other remain unassessed: the label alone cannot establish relevance.
