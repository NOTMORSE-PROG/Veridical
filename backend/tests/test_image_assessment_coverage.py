"""V-081/F1,F6: image processing completion is not whole-content assessment."""

import asyncio

import pytest

from app.checks.forensics.service import _compute_forensics_findings
from app.config import get_settings
from app.errors import QuotaExhaustedError
from app.ingest import vision
from app.ingest.patterns import load_patterns
from app.ingest.schemas import ExtractionResult, ImageBlock, SectionTree, TextBlock
from tests.test_ingest_vision import SpyLLM, _pdf_with_images, _run


@pytest.mark.parametrize(
    "response,status,reason",
    [
        ({"unexpected": True}, "invalid_response", "invalid_response"),
        (
            {"kind": "table", "confidence": "high", "rows": [[]]},
            "invalid_response",
            "invalid_response",
        ),
        ({"kind": "chart", "confidence": "high"}, "classified_only", "content_not_transcribed"),
        ({"kind": "other", "confidence": "high"}, "classified_only", "content_not_transcribed"),
        ({"kind": "equation", "confidence": "high", "latex": "x=1"}, "transcribed", None),
        (
            {"kind": "table", "confidence": "low", "rows": [["n", "M"], ["10", "?"]]},
            "transcribed",
            "low_confidence",
        ),
    ],
)
def test_every_response_has_a_saved_image_reading(tmp_path, response, status, reason):
    path = _pdf_with_images(tmp_path, n_results_images=1)
    result, calls = _run(path, SpyLLM([response]))
    assert calls == 1
    [reading] = result.image_readings
    assert (reading.image_index, reading.status, reading.reason) == (0, status, reason)
    assert reading.attempted is True
    assert reading.prompt_type == vision.PROMPT_TYPE
    assert reading.prompt_version == vision.PROMPT_VERSION
    assert ExtractionResult.model_validate_json(result.model_dump_json()) == result


def _extraction(*, images=None, text="Qualitative description."):
    return ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=len(text),
        section_tree=SectionTree(source="none"),
        blocks=[
            TextBlock(page=1, text=text, max_font_size=11, bold_ratio=0),
            # A table caption just below the test images, so they are read.
            TextBlock(
                page=1,
                bbox=(0, 104, 100, 116),
                text="Table 1. Scores",
                max_font_size=10,
                bold_ratio=0,
            ),
        ],
        images=images or [],
    )


@pytest.mark.parametrize("text", ["Qualitative description.", "t(28) = 2.45, p = .900."])
def test_unknown_image_preserves_native_findings_but_not_a_clean_applicability_claim(text):
    baseline = _compute_forensics_findings(_extraction(text=text))
    with_image = _compute_forensics_findings(
        _extraction(text=text, images=[ImageBlock(page=1, bbox=(0, 0, 100, 100))])
    )
    assert len(with_image.flag_drafts) == len(baseline.flag_drafts)
    for actual, expected in zip(with_image.flag_drafts, baseline.flag_drafts, strict=True):
        assert (actual.severity, actual.evidence_excerpt, actual.page_anchor) == (
            expected.severity,
            expected.evidence_excerpt,
            expected.page_anchor,
        )
        assert {key: actual.detail[key] for key in expected.detail if key != "reason"} == {
            key: value for key, value in expected.detail.items() if key != "reason"
        }
        # The robust error survives. Missing source also requires disclosure
        # that either supported test-tail interpretation remains inconsistent.
        assert actual.detail["reason"].startswith(expected.detail["reason"])
        assert actual.detail["tail_context_complete"] is False
        assert actual.detail["one_tailed_computed_p"] > 0
    assert with_image.image_coverage["n_images_unassessed"] == 1
    assert with_image.image_coverage["coverage_reason"] == "image_content_unassessed"
    assert with_image.outcome.value == "escalated"


def test_docx_inventory_does_not_start_a_vision_call(tmp_path):
    result = _extraction(images=[ImageBlock(paragraph=4)])
    result.anchor_kind = "paragraph"
    settings = get_settings()
    spy = SpyLLM()
    calls = asyncio.run(
        vision.read_images(
            result,
            tmp_path / "not-opened.docx",
            spy,
            load_patterns(settings.ingest_patterns_file),
            settings,
        )
    )
    assert calls == 0 and spy.calls == []
    assert result.image_readings[0].reason == "unsupported_source"


def test_selection_distinguishes_identical_occurrences_and_size_and_cap():
    settings = get_settings().model_copy(update={"ingest_vision_max_images": 1})
    result = _extraction(
        images=[
            ImageBlock(page=1, bbox=(0, 0, 100, 100)),
            ImageBlock(page=1, bbox=(0, 0, 100, 100)),
            ImageBlock(page=1, bbox=(0, 0, 1, 1)),
        ]
    )
    indices = vision.prepare_image_readings(
        result, load_patterns(settings.ingest_patterns_file), settings
    )
    assert indices == [0]
    assert [(row.image_index, row.reason) for row in result.image_readings] == [
        (0, "not_attempted"),
        (1, "selection_limit"),
        (2, "size_filter"),
    ]


def test_later_quota_error_retains_success_and_distinguishes_unattempted_images(tmp_path):
    path = _pdf_with_images(tmp_path, n_results_images=3)
    settings = get_settings()
    from app.ingest.pdf import extract_document

    result = extract_document(str(path), settings)

    class LaterQuota(SpyLLM):
        async def complete(self, *args, **kwargs):
            if self.calls:
                raise QuotaExhaustedError("fixture quota")
            return await super().complete(*args, **kwargs)

    spy = LaterQuota([{"kind": "table", "confidence": "high", "rows": [["n"], ["10"]]}])
    with pytest.raises(QuotaExhaustedError):
        asyncio.run(
            vision.read_images(
                result,
                path,
                spy,
                load_patterns(settings.ingest_patterns_file),
                settings,
            )
        )
    assert result.tables[0].source_image_index == 0
    assert [(row.status, row.reason, row.attempted) for row in result.image_readings] == [
        ("transcribed", None, True),
        ("failed", "quota_exhausted", True),
        ("not_selected", "quota_exhausted", False),
    ]


def test_high_confidence_source_linked_table_can_feed_existing_checks(tmp_path):
    path = _pdf_with_images(tmp_path, n_results_images=1)
    result, _ = _run(
        path,
        SpyLLM(
            [
                {
                    "kind": "table",
                    "confidence": "high",
                    "rows": [["Group", "n", "M"], ["A", "10", "3.33"]],
                }
            ]
        ),
    )
    computation = _compute_forensics_findings(result)
    assert computation.outcome.value == "escalated"
    assert len(computation.flag_drafts) == 1
    assert computation.image_coverage["n_images_unassessed"] == 0
    assert computation.image_coverage["n_image_tables_with_stats"] == 1
    reconstruction = computation.reconstruction
    assert reconstruction["considered_rows"] == reconstruction["unassessed_rows"] == 1
    assert reconstruction["attempted_rows"] == reconstruction["compatible_rows"] == 0
    assert reconstruction["single_item_excluded_tables"] == []
    assert reconstruction["omitted_receipt_rows"] == 0
    [row] = reconstruction["rows"]
    assert (row["table_index"], row["row_index"]) == (0, 1)
    assert row["state"] == "unassessed" and row["reason"] == "native_table_required"


@pytest.mark.parametrize(
    "rows",
    [
        [["Group", "n", "M"], ["A", "10", "?"]],
        [["Group", "n", "M"], ["A", "10", "3.33"], ["B", "10", "?"]],
        [["Group", "n", "M"], ["A", "10", ""]],
        [["Group", "n", "M"], ["A", "10"]],
        [["Group", "n", "M", "M"], ["A", "10", "3.30", "?"]],
    ],
)
def test_partial_statistical_cells_do_not_clear_the_whole_image(tmp_path, rows):
    path = _pdf_with_images(tmp_path, n_results_images=1)
    result, _ = _run(path, SpyLLM([{"kind": "table", "confidence": "high", "rows": rows}]))
    baseline = result.model_copy(deep=True)
    baseline.images = []
    baseline.image_readings = []
    expected = _compute_forensics_findings(baseline)
    actual = _compute_forensics_findings(result)
    assert actual.flag_drafts == expected.flag_drafts
    assert actual.n_descriptive == expected.n_descriptive
    assert actual.outcome.value == "escalated"
    assert actual.image_coverage["n_images_unassessed"] == 1
    assert actual.image_coverage["image_coverage_items"][0]["reason"] == "table_partly_parsed"


@pytest.mark.parametrize("n,mean", [("NaN", "3.30"), ("inf", "3.30"), ("10", "NaN")])
def test_nonfinite_image_values_keep_independently_supported_findings(tmp_path, n, mean):
    path = _pdf_with_images(tmp_path, n_results_images=1)
    result, _ = _run(
        path,
        SpyLLM([{"kind": "table", "confidence": "high", "rows": [["n", "M"], [n, mean]]}]),
    )
    result.blocks = _extraction(text="t(28) = 2.45, p = .900.").blocks
    actual = _compute_forensics_findings(result)
    assert actual.outcome.value == "escalated"
    assert len(actual.flag_drafts) == 1
    assert actual.image_coverage["n_images_unassessed"] == 1


@pytest.mark.parametrize("kind", ["equation", "chart", "other"])
def test_classification_or_saved_equation_is_not_a_statistical_clearance(tmp_path, kind):
    path = _pdf_with_images(tmp_path, n_results_images=1)
    response = {"kind": kind, "confidence": "high"}
    if kind == "equation":
        response["latex"] = "t(28)=2.45, p=.900"
    result, _ = _run(path, SpyLLM([response]))
    computation = _compute_forensics_findings(result)
    assert computation.outcome.value == "escalated"
    assert computation.flag_drafts == []
    assert computation.image_coverage["analysis_coverage"] == "unsupported"
    if kind == "equation":
        assert computation.image_coverage["n_equations_unassessed"] == 1


def test_cropped_image_failure_does_not_call_provider_or_erase_native_source(tmp_path, monkeypatch):
    path = _pdf_with_images(tmp_path, n_results_images=1)
    monkeypatch.setattr(vision, "_crop", lambda *_: (_ for _ in ()).throw(ValueError("bad crop")))
    spy = SpyLLM()
    result, calls = _run(path, spy)
    assert calls == 0 and spy.calls == []
    assert result.blocks
    assert result.image_readings[0].status == "failed"
    assert result.image_readings[0].reason == "crop_unavailable"
    assert result.image_readings[0].attempted is False


def test_disabled_image_reading_and_omitted_drawings_remain_disclosed():
    settings = get_settings()
    result = _extraction(images=[ImageBlock(paragraph=4)])
    result.docx_omitted_image_occurrences = 2
    assert (
        vision.prepare_image_readings(
            result,
            load_patterns(settings.ingest_patterns_file),
            settings,
            enabled=False,
        )
        == []
    )
    assert result.image_readings[0].reason == "vision_disabled"
    computation = _compute_forensics_findings(result)
    assert computation.outcome.value == "escalated"
    assert computation.image_coverage["n_image_occurrences_omitted"] == 2


def test_no_image_qualitative_and_correct_stat_controls_are_unchanged():
    qualitative = _compute_forensics_findings(_extraction())
    correct = _compute_forensics_findings(_extraction(text="t(28) = 2.45, p = .021."))
    assert qualitative.outcome.value == "not_applicable"
    assert correct.outcome.value == "passed"
    assert correct.flag_drafts == []
    assert "coverage_reason" not in correct.image_coverage
