"""Pages whose reading order is unresolved are left out of rubric grading,
named on every graded row, and never joined into a quotation."""

import json

import pytest

from app.checks.consistency import run_semantic_checks_with_consistency
from app.checks.semantic import _verify_quotes, build_semantic_batches
from app.checks.source_quality import unread_page_scope
from app.config import get_settings
from app.ingest.schemas import ExtractionResult, PageReadingOrder, SectionTree, TextBlock
from app.models.enums import ResultOutcome
from app.report.source_coverage import project_source_coverage
from tests.test_checks_consistency import FakeCriterion, FakeSession, ScriptedLLM

PAGES = {
    1: "The study designs a booking system for small dental clinics.",
    2: "Overlapping table cells whose order is unknown.",
    3: "The system was tested with twelve clinic staff over two weeks.",
}
TESTED = PAGES[3]


def _source(unresolved=(2,), *, corrupt_page=None) -> ExtractionResult:
    blocks = [
        TextBlock(
            page=page,
            text=text,
            bbox=(50, 100, 500, 120),
            max_font_size=11,
            bold_ratio=0.0,
            source_line_ids=[f"{page}:0:0"],
        )
        for page, text in PAGES.items()
    ]
    return ExtractionResult(
        page_count=len(PAGES),
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(text) for text in PAGES.values()),
        blocks=blocks,
        images=[],
        section_tree=SectionTree(source="none", nodes=[]),
        reading_order=[
            PageReadingOrder(
                page=page,
                status="unresolved" if page in unresolved else "ordered",
                reason="table_regions_unresolved" if page in unresolved else None,
                source_inventory_sha256="a" * 64,
                source_line_count=1,
                ordered_source_line_ids=["other"] if page == corrupt_page else [f"{page}:0:0"],
            )
            for page in PAGES
        ],
    )


class RecordingLLM(ScriptedLLM):
    def __init__(self, responses):
        super().__init__(responses)
        self.grading_prompts: list[str] = []

    async def complete(self, prompt_type, prompt, **kwargs):
        if prompt_type != "semantic_support":
            self.grading_prompts.append(prompt)
        return await super().complete(prompt_type, prompt, **kwargs)


def _verdict(quote: str) -> dict:
    return {
        "verdicts": [
            {
                "index": 0,
                "verdict": "pass",
                "reasoning": "Testing is reported.",
                "evidence_quotes": [quote],
            }
        ]
    }


@pytest.fixture
def ratio(monkeypatch):
    def set_ratio(value: float) -> None:
        monkeypatch.setenv("ANALYSIS_UNREAD_PAGES_MAX_RATIO", str(value))
        get_settings.cache_clear()

    yield set_ratio
    get_settings.cache_clear()


def _batch():
    batches, _missing = build_semantic_batches(
        [FakeCriterion(id=1, text="Testing")], _source(), unread_pages=[2]
    )
    return batches[0][0]


def test_only_the_unresolved_pages_are_set_aside():
    assert unread_page_scope(_source(), 0.5) == [2]
    assert unread_page_scope(_source(unresolved=()), 1.0) is None


def test_too_many_unresolved_pages_are_not_set_aside():
    assert unread_page_scope(_source(unresolved=(1, 2)), 0.5) is None


def test_a_provenance_defect_still_blocks_the_whole_document():
    assert unread_page_scope(_source(corrupt_page=3), 1.0) is None


def test_graded_text_leaves_out_and_names_the_unread_pages():
    batch = _batch()
    envelope = json.loads(batch.context_text)
    assert envelope["source_coverage"]["unread_pages"] == [2]
    assert envelope["contiguous_source_scopes"] == [
        [{"page": 1, "text": PAGES[1]}],
        [{"page": 3, "text": PAGES[3]}],
    ]
    assert PAGES[2] not in batch.context_text
    assert batch.source_coverage["unread_pages"] == [2]


def test_a_quotation_cannot_join_text_across_an_unread_page():
    batch = _batch()
    assert _verify_quotes([TESTED], batch.blocks, "page", scopes=batch.source_scopes) == ["page 3"]
    joined = "small dental clinics. The system was tested"
    assert _verify_quotes([joined], batch.blocks, "page", scopes=batch.source_scopes) is None


async def test_grading_uses_the_readable_pages_and_discloses_the_rest(ratio):
    ratio(0.5)
    llm = RecordingLLM([_verdict(TESTED), _verdict(TESTED)])
    [result] = await run_semantic_checks_with_consistency(
        FakeSession(), 1, [FakeCriterion(id=1, text="Testing")], _source(), llm
    )
    assert result.outcome == ResultOutcome.passed
    assert result.detail["source_coverage"]["unread_pages"] == [2]
    assert llm.grading_prompts and all(PAGES[2] not in p for p in llm.grading_prompts)
    assert project_source_coverage(result.detail["source_coverage"]).unread_pages == [2]


async def test_more_unread_pages_than_allowed_grades_nothing(ratio):
    ratio(0.2)
    llm = RecordingLLM([])
    [result] = await run_semantic_checks_with_consistency(
        FakeSession(), 1, [FakeCriterion(id=1, text="Testing")], _source(), llm
    )
    assert result.outcome == ResultOutcome.escalated
    assert result.detail["basis"] == "source-quality"
    assert llm.grading_prompts == []


def test_printed_report_names_the_unread_pages():
    from app.report.export import _criterion_source_flowables, _register_fonts, _styles
    from tests.test_native_source_notes_export import _data

    row = _data().report.results[0]
    row.kind = row.type = "semantic"
    row.source_coverage = project_source_coverage(
        {
            "native_math_coverage_version": "v1",
            "native_math_inventory_state": "not_applicable",
            "n_native_equations_unassessed": 0,
            "unread_pages": [45, 46, 47, 53],
        }
    )
    whole = " ".join(
        part.getPlainText() for part in _criterion_source_flowables(row, _styles(_register_fonts()))
    )
    assert "Pages 45-47, 53 could not be read in a reliable order" in whole


def test_printed_report_says_images_were_not_read_by_the_grade():
    from app.report.export import _criterion_source_flowables, _register_fonts, _styles
    from tests.test_native_source_notes_export import _data

    row = _data().report.results[0]
    row.kind = row.type = "semantic"
    row.source_coverage = project_source_coverage(
        {
            "native_math_coverage_version": "v1",
            "native_math_inventory_state": "not_applicable",
            "n_native_equations_unassessed": 0,
            "images_not_assessed": True,
        }
    )
    whole = " ".join(
        part.getPlainText() for part in _criterion_source_flowables(row, _styles(_register_fonts()))
    )
    assert "Image content not assessed. This grade is based on the manuscript's text only" in whole


def test_only_criteria_the_grader_assessed_say_images_were_not_read():
    from app.report.service import _images_not_assessed, _with_image_coverage
    from tests.test_native_source_notes_export import _data

    base = (
        _data()
        .report.results[0]
        .model_copy(
            update={
                "kind": "semantic",
                "type": "semantic",
                "basis": "llm",
                "resolution": None,
                "source_coverage": project_source_coverage(None),
            }
        )
    )
    rows = [
        base,
        base.model_copy(update={"basis": "source-quality"}),
        base.model_copy(update={"basis": "structural-alignment"}),
        base.model_copy(update={"kind": "structural"}),
        base.model_copy(update={"resolution": object()}),
    ]
    flags = [row.source_coverage.images_not_assessed for row in _with_image_coverage(rows, True)]
    assert flags == [True, False, False, False, False]
    assert [r.source_coverage.images_not_assessed for r in _with_image_coverage(rows, False)] == [
        False
    ] * 5

    # Any image counts: grading reads none, even one the statistical check read.
    for detail in (
        {"n_image_occurrences": 2},
        {"n_images_unassessed": 1},
        {"n_image_occurrences_omitted": 3},
        {"image_coverage_items": [{"image_index": 0}]},
    ):
        assert _images_not_assessed([None, detail])
    assert not _images_not_assessed([{"n_image_occurrences": 0}, None])
    assert not _images_not_assessed([])
