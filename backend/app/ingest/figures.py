"""Bounded visual readings, isolated from grading and statistical transcriptions."""

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.errors import ApiDownError, GoneError, InvalidApiKeyError, QuotaExhaustedError
from app.ingest.docx_source import read_docx_image
from app.ingest.schemas import FigureReading
from app.ingest.vision import _area, _crop, _priority_page_ranges
from app.llm.base import LLMNotConfiguredError

_PROMPTS = Path(__file__).parent / "prompts"


class VisualObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["chart", "diagram", "implementation_screenshot", "table", "equation", "other"]
    confidence: Literal["high", "low"]
    visible_content: str = Field(min_length=1)
    visible_labels: list[str]
    limitations: list[str] = Field(min_length=1)


class VisualSupport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["supported", "not_supported", "uncertain"]
    reason: str = Field(min_length=1)


def _caption(result, image, patterns, settings):
    if image.paragraph is not None:
        if sum(item.paragraph == image.paragraph for item in result.images) != 1:
            return None, None
        next_image = min(
            (
                item.paragraph
                for item in result.images
                if item.paragraph is not None and item.paragraph > image.paragraph
            ),
            default=None,
        )
        blocks = [
            b
            for b in result.blocks
            if b.paragraph is not None
            and (next_image is None or b.paragraph < next_image)
            and image.paragraph
            <= b.paragraph
            <= image.paragraph + settings.ingest_figure_caption_lookahead
        ]
    else:
        if sum(item.page == image.page for item in result.images) != 1:
            return None, None
        blocks = [b for b in result.blocks if b.page == image.page]
    captions = [b for b in blocks if patterns.caption.match(b.text.strip())]
    # Page proximity alone cannot attach one of several captions to a crop.
    if len(captions) != 1:
        return None, None
    block = captions[0]
    anchor = f"paragraph {block.paragraph}" if block.paragraph is not None else f"page {block.page}"
    return block.text, anchor


def prepare_figures(result, patterns, settings, *, enabled=True):
    eligible = []
    priority = _priority_page_ranges(result.section_tree.nodes, patterns)
    for index, image in enumerate(result.images):
        caption, anchor = _caption(result, image, patterns, settings)
        supported = image.docx_source is not None or (
            image.page is not None
            and image.bbox is not None
            and _area(image.bbox) >= settings.ingest_vision_min_area_pts
        )
        useful = caption is not None or (
            image.page is not None and any(a <= image.page <= b for a, b in priority)
        )
        if enabled and settings.ingest_figure_reading_enabled and supported and useful:
            eligible.append(index)
    selected = eligible[: settings.ingest_figure_max_images]
    result.figure_readings = []
    for index, image in enumerate(result.images):
        caption, anchor = _caption(result, image, patterns, settings)
        result.figure_readings.append(
            FigureReading(
                image_index=index,
                source_anchor=f"paragraph {image.paragraph}"
                if image.paragraph is not None
                else f"page {image.page}"
                if image.page is not None
                else None,
                status="not_selected",
                reason="not_attempted"
                if index in selected
                else "selection_limit"
                if index in eligible
                else "not_requested"
                if not enabled or not settings.ingest_figure_reading_enabled
                else "source_or_relevance_unavailable",
                source_caption=caption,
                caption_anchor=anchor,
            )
        )
    return selected


def _raster(path, result, index, source_hash, settings):
    if not isinstance(source_hash, str) or not source_hash:
        raise GoneError("The original document hash was not recorded.")
    image = result.images[index]
    if image.docx_source:
        content, media_type = read_docx_image(path, result, index, source_hash, settings)
        # The existing transport accepts PNG. Decode verified raster locally;
        # it neither fetches external image links nor substitutes another part.
        if media_type != "image/png":
            import pymupdf

            with pymupdf.open(stream=content, filetype="jpeg") as doc:
                content = doc[0].get_pixmap().tobytes("png")
        locator = {"paragraph": image.paragraph, "docx_source": image.docx_source.model_dump()}
    else:
        content = _crop(path, image, settings)
        locator = {"page": image.page, "bbox": image.bbox, "dpi": settings.ingest_vision_crop_dpi}
    with path.open("rb") as original:
        if hashlib.file_digest(original, "sha256").hexdigest() != source_hash:
            raise GoneError("The figure source changed during reading.")
    return content, {
        "document_sha256": source_hash,
        "image_sha256": hashlib.sha256(content).hexdigest(),
        "image_index": index,
        "locator": locator,
        "schema_version": "v1",
    }


async def read_figures(result, path, llm, patterns, settings, *, source_hash, enabled=True):
    selected = prepare_figures(result, patterns, settings, enabled=enabled)
    calls = 0
    for index in selected:
        entry = result.figure_readings[index]
        try:
            png, binding = await asyncio.to_thread(
                _raster, path, result, index, source_hash, settings
            )
        except (GoneError, ValueError, OSError, RuntimeError, IndexError):
            entry.status, entry.reason = "unavailable", "source_mismatch"
            continue
        entry.source_binding = binding
        try:
            calls += 1
            raw = await llm.complete(
                "figure_reading",
                (_PROMPTS / "figure_reading_v1.txt").read_text(encoding="utf-8"),
                prompt_version="v1",
                images=[png],
                source_binding=binding,
            )
            observation = VisualObservation.model_validate(raw)
            if not observation.visible_content.strip() or not all(
                s.strip() for s in observation.limitations
            ):
                raise ValueError("Empty visual observation or limitations")
            entry.kind = observation.kind
            if observation.kind == "other":
                entry.status, entry.reason = "needs_review", "no_assessable_figure"
                continue
            if (
                observation.confidence != "high"
                or not observation.visible_labels
                or not all(s.strip() for s in observation.visible_labels)
            ):
                entry.status, entry.reason = "needs_review", "uncertain_reading"
                continue
            calls += 1
            support = VisualSupport.model_validate(
                await llm.complete(
                    "figure_support",
                    (_PROMPTS / "figure_support_v1.txt").read_text(encoding="utf-8")
                    + json.dumps(observation.model_dump(), ensure_ascii=False),
                    prompt_version="v1",
                    images=[png],
                    source_binding=binding,
                )
            )
            if not support.reason.strip():
                raise ValueError("Empty support basis")
            entry.support_reason = support.reason
            if support.status != "supported":
                entry.status, entry.reason = "needs_review", "support_unconfirmed"
                continue
            entry.visible_content, entry.visible_labels = (
                observation.visible_content,
                observation.visible_labels,
            )
            entry.limitations = observation.limitations
            entry.status, entry.reason = "available", "visual_reading_only"
        except (ValidationError, ValueError):
            entry.status, entry.reason = "unavailable", "invalid_response"
        except (
            ApiDownError,
            QuotaExhaustedError,
            LLMNotConfiguredError,
            InvalidApiKeyError,
        ) as exc:
            entry.status, entry.reason = (
                "unavailable",
                "quota_exhausted"
                if isinstance(exc, QuotaExhaustedError)
                else "service_unavailable",
            )
            for remaining in selected:
                if result.figure_readings[remaining].reason == "not_attempted":
                    (
                        result.figure_readings[remaining].status,
                        result.figure_readings[remaining].reason,
                    ) = "unavailable", entry.reason
            break
    return calls


def saved_figure_readings(result, source_hash):
    """Fail closed on stale occurrence bindings without hiding readable text."""
    if result.figure_readings is None:
        return None
    saved = []
    for entry in result.figure_readings:
        copy = entry.model_copy(deep=True)
        if entry.status == "available":
            image = (
                result.images[entry.image_index] if entry.image_index < len(result.images) else None
            )
            binding = entry.source_binding
            locator = binding.get("locator")
            matched = (
                image is not None
                and isinstance(locator, dict)
                and binding.get("document_sha256") == source_hash
                and binding.get("image_index") == entry.image_index
                and binding.get("schema_version") == entry.schema_version
                and isinstance(binding.get("image_sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", binding["image_sha256"]) is not None
                and bool(entry.visible_content and entry.visible_content.strip())
                and bool(entry.visible_labels)
                and all(label.strip() for label in entry.visible_labels)
                and bool(entry.limitations)
                and bool(entry.support_reason)
            )
            if matched and image.docx_source:
                matched = (
                    locator.get("docx_source") == image.docx_source.model_dump()
                    and locator.get("paragraph") == image.paragraph
                )
            elif matched:
                matched = (
                    locator.get("page") == image.page
                    and isinstance(locator.get("bbox"), (list, tuple))
                    and tuple(locator["bbox"]) == image.bbox
                )
            if not matched:
                copy.status, copy.reason = "unavailable", "source_mismatch"
                copy.visible_content, copy.visible_labels, copy.limitations = None, [], []
        saved.append(copy)
    return saved
