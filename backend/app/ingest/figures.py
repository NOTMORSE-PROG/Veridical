"""Bounded visual readings, isolated from grading and statistical transcriptions."""

import asyncio
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.errors import ApiDownError, GoneError, InvalidApiKeyError, QuotaExhaustedError
from app.ingest.image_source import _raster as _native_raster
from app.ingest.image_source import prepare_figures
from app.ingest.schemas import FigureSource
from app.llm.base import LLMNotConfiguredError

_PROMPTS = Path(__file__).parent / "prompts"


def _raster(path, result, index, source_hash, settings):
    if settings.native_compute_url:
        from app.ingest.compute_client import remote_raster

        return remote_raster(path, result.images[index], settings, source_hash=source_hash)
    return _native_raster(path, result, index, source_hash, settings)


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


async def read_figures(
    result, path, llm, patterns, settings, *, source_hash, enabled=True, preparation_lease=None
):
    selected = prepare_figures(result, patterns, settings, enabled=enabled)
    calls = 0
    for index in selected:
        entry = result.figure_readings[index]
        try:
            png, binding = (
                await preparation_lease.run(_raster, path, result, index, source_hash, settings)
                if preparation_lease is not None
                else await asyncio.to_thread(_raster, path, result, index, source_hash, settings)
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


def figure_source(result, extraction_sha256=None) -> FigureSource:
    """Keep only what saved figure readings are checked against."""
    images = {
        entry.image_index: result.images[entry.image_index]
        for entry in result.figure_readings or []
        if entry.status == "available" and entry.image_index < len(result.images)
    }
    return FigureSource(
        extraction_sha256=extraction_sha256,
        figure_readings=result.figure_readings,
        images=images,
    )


def saved_figure_readings(result, source_hash):
    """Fail closed on stale occurrence bindings without hiding readable text.

    Takes a full extraction or its FigureSource; both give the same readings.
    """
    source = result if isinstance(result, FigureSource) else figure_source(result)
    if source.figure_readings is None:
        return None
    saved = []
    for entry in source.figure_readings:
        copy = entry.model_copy(deep=True)
        if entry.status == "available":
            image = source.images.get(entry.image_index)
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
