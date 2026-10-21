"""Selective non-scoring image observations with supported/adverse controls."""

from copy import deepcopy

import pytest

from app.errors import ApiDownError, InvalidApiKeyError, QuotaExhaustedError
from app.ingest.figures import prepare_figures, read_figures, saved_figure_readings
from app.ingest.patterns import load_patterns
from app.ingest.schemas import ExtractionResult
from app.report.service import manuscript_paragraphs_for
from tests.test_docx_media_read import _save
from tests.test_docx_media_read import saved as saved

OBSERVATION = {
    "kind": "diagram",
    "confidence": "high",
    "visible_content": "A visible box is labeled A.",
    "visible_labels": ["A"],
    "limitations": ["This does not establish implementation."],
}
SUPPORT = {"status": "supported", "reason": "The proposed box and label are visible."}


class Scripted:
    def __init__(self, observation=None, support=None, error=None):
        self.observation = deepcopy(OBSERVATION if observation is None else observation)
        self.support = deepcopy(SUPPORT if support is None else support)
        self.error, self.calls = error, []

    async def complete(self, kind, prompt, **context):
        self.calls.append((kind, prompt, context))
        if self.error:
            raise self.error("controlled failure")
        return self.observation if kind == "figure_reading" else self.support


async def read(saved, llm, **settings_updates):
    settings, manuscript, extraction, path = saved
    settings = settings.model_copy(update=settings_updates)
    before = (
        [b.model_dump() for b in extraction.blocks],
        [t.model_dump() for t in extraction.tables],
        extraction.image_readings,
    )
    calls = await read_figures(
        extraction,
        path,
        llm,
        load_patterns(settings.ingest_patterns_file),
        settings,
        source_hash=manuscript.content_hash,
    )
    assert before == (
        [b.model_dump() for b in extraction.blocks],
        [t.model_dump() for t in extraction.tables],
        extraction.image_readings,
    )
    return calls


async def test_available_reading_preserves_original_caption_identity_and_numeric_coverage(saved):
    llm = Scripted()
    assert await read(saved, llm) == 2
    _, manuscript, result, _ = saved
    entry = result.figure_readings[0]
    assert entry.status == "available" and entry.visible_labels == ["A"]
    assert entry.source_caption == "Figure 1. Original caption."
    assert entry.caption_anchor == "paragraph 2" and entry.source_anchor == "paragraph 1"
    assert entry.source_binding["document_sha256"] == manuscript.content_hash
    assert (
        entry.source_binding["locator"]["docx_source"] == result.images[0].docx_source.model_dump()
    )
    assert "Original caption" not in llm.calls[0][1]
    assert all(call[2]["images"][0] for call in llm.calls)
    assert result.figure_readings[1].status == "not_selected"
    _save(saved)
    restored = ExtractionResult.model_validate_json(result.model_dump_json())
    assert saved_figure_readings(restored, manuscript.content_hash)[0].status == "available"
    projection = manuscript_paragraphs_for(manuscript, saved[0])
    assert projection.images[0].figure_reading.status == "available"


@pytest.mark.parametrize(
    "change,expected,calls",
    [
        ({"confidence": "low"}, "needs_review", 1),
        ({"kind": "other"}, "needs_review", 1),
        ({"visible_labels": []}, "needs_review", 1),
        ({"visible_labels": [" "]}, "needs_review", 1),
        ({"visible_content": " "}, "unavailable", 1),
        ({"limitations": []}, "unavailable", 1),
        ({"criterion_verdict": "pass"}, "unavailable", 1),
    ],
)
async def test_unclear_empty_or_incompatible_observation_is_not_assessed(
    saved, change, expected, calls
):
    llm = Scripted(observation=OBSERVATION | change)
    assert await read(saved, llm) == calls
    assert saved[2].figure_readings[0].status == expected
    assert saved[2].figure_readings[0].visible_content is None


@pytest.mark.parametrize(
    "support",
    [
        {"status": "uncertain", "reason": "The label is too small."},
        {"status": "not_supported", "reason": "The alleged box is absent."},
        {"status": "supported", "reason": " "},
        {"status": True, "reason": "invalid"},
        {},
    ],
)
async def test_rejected_or_malformed_support_cannot_accept_fluent_reading(saved, support):
    assert await read(saved, Scripted(support=support)) == 2
    assert saved[2].figure_readings[0].status != "available"


@pytest.mark.parametrize("failure", [ApiDownError, QuotaExhaustedError, InvalidApiKeyError])
async def test_provider_failure_is_unassessed_without_losing_text(saved, failure):
    assert await read(saved, Scripted(error=failure)) == 1
    assert saved[2].figure_readings[0].status == "unavailable"


async def test_missing_or_changed_source_never_spends_calls(saved):
    saved[1].content_hash = "f" * 64
    llm = Scripted()
    assert await read(saved, llm) == 0
    assert not llm.calls and saved[2].figure_readings[0].reason == "source_mismatch"


async def test_cap_and_library_opt_out_do_not_spend_calls(saved):
    llm = Scripted()
    assert await read(saved, llm, ingest_figure_max_images=0) == 0
    assert not llm.calls
    settings, _, extraction, _ = saved
    assert (
        prepare_figures(
            extraction, load_patterns(settings.ingest_patterns_file), settings, enabled=False
        )
        == []
    )
    assert all(item.reason == "not_requested" for item in extraction.figure_readings)


@pytest.mark.parametrize(
    "mutation", ["document", "occurrence", "part", "empty_content", "empty_hash", "locator"]
)
async def test_stale_or_incomplete_saved_reading_does_not_survive_projection(saved, mutation):
    await read(saved, Scripted())
    _, manuscript, result, _ = saved
    entry = result.figure_readings[0]
    if mutation == "document":
        entry.source_binding["document_sha256"] = "f" * 64
    elif mutation == "occurrence":
        entry.source_binding["image_index"] = 1
    elif mutation == "part":
        entry.source_binding["locator"]["docx_source"]["part_name"] = "word/media/other.png"
    elif mutation == "empty_hash":
        entry.source_binding["image_sha256"] = ""
    elif mutation == "locator":
        entry.source_binding["locator"] = None
    else:
        entry.visible_content = None
    checked = saved_figure_readings(result, manuscript.content_hash)[0]
    assert checked.status == "unavailable" and checked.visible_content is None
    assert result.blocks
