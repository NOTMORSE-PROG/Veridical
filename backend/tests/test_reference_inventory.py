"""F3: source authority precedes reference count and citation-style grades."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.checks.router import route_criterion
from app.checks.rules import context, get_rule
from app.checks.rules.reference_inventory import measure_reference_inventory
from app.checks.rules.references import CITATION_STYLE_RULE_ID, REFERENCE_COUNT_RULE_ID
from app.checks.structural import run_structural_check
from app.config import Settings
from app.ingest.patterns import load_patterns
from app.ingest.schemas import (
    ExtractionResult,
    ImageBlock,
    PageReadingOrder,
    SectionNode,
    SectionTree,
    TableBlock,
    TextBlock,
)
from app.models.enums import ResultOutcome
from tests.native_math_fixtures import record_empty_native_inventory
from tests.test_checks_structural_rules import FakeCitation, FakeCriterion, _ctx

REF_A = "Reyes, J. (2024). Example source. Journal of Examples."
REF_B = "Santos, A. (2022). Another source. Journal of Examples."


def source(entries=(), *, paragraph=True, heading=True, issue_page=None, legacy=False, eof=False):
    texts = ["A recovered body paragraph.", *(["References"] if heading else []), *entries]
    if not eof:
        texts.append("Appendix")
    blocks = [
        TextBlock(
            text=text,
            page=None if paragraph else (1 if index == 0 else 2),
            paragraph=index if paragraph else None,
            max_font_size=14 if text in {"References", "Appendix"} else 11,
            bold_ratio=1 if text in {"References", "Appendix"} else 0,
            source_line_ids=None if paragraph else [f"line-{index}"],
        )
        for index, text in enumerate(texts)
    ]
    nodes = [
        SectionNode(title=block.text, level=1, page=block.page, paragraph=block.paragraph)
        for block in blocks
        if block.text in {"References", "Appendix"}
    ]
    records = (
        None
        if paragraph or legacy
        else [
            PageReadingOrder(
                page=page,
                status="unresolved" if page == issue_page else "ordered",
                reason="table_regions_unresolved" if page == issue_page else None,
                source_inventory_sha256="a" * 64,
                source_line_count=sum(block.page == page for block in blocks),
                ordered_source_line_ids=[
                    identity
                    for block in blocks
                    if block.page == page
                    for identity in block.source_line_ids
                ],
            )
            for page in (1, 2)
        ]
    )
    return record_empty_native_inventory(
        ExtractionResult(
            page_count=0 if paragraph else 2,
            anchor_kind="paragraph" if paragraph else "page",
            image_only=False,
            text_chars=sum(len(block.text) for block in blocks),
            blocks=blocks,
            images=[],
            section_tree=SectionTree(source="heuristics", nodes=nodes),
            reading_order=records,
        )
    )


def rows(*raws):
    return [FakeCitation(index, raw) for index, raw in enumerate(raws)]


def measure(extraction, citations):
    before = extraction.model_dump_json()
    original_rows = [vars(row).copy() for row in citations]
    inventory = measure_reference_inventory(extraction, citations, load_patterns())
    assert extraction.model_dump_json() == before
    assert [vars(row) for row in citations] == original_rows
    return inventory


@pytest.mark.parametrize(
    "entries,stored",
    [
        ([REF_A], []),
        ([], [REF_A]),
        ([REF_A, REF_B], [REF_A]),
        ([REF_A], [REF_A, REF_B]),
        ([REF_A], [REF_B]),
    ],
)
def test_missing_partial_extra_or_changed_rows_have_no_measurement_authority(entries, stored):
    inventory = measure(source(entries), rows(*stored))
    assert inventory.status == "stale" and inventory.count is None


@pytest.mark.parametrize("indexes", [[1], [True], [0, 0], [1, 0]])
def test_order_indexes_are_exact_source_membership(indexes):
    stored = rows(*([REF_A, REF_B] if len(indexes) == 2 else [REF_A]))
    for row, index in zip(stored, indexes, strict=True):
        row.order_index = index
    inventory = measure(source([row.raw_text for row in stored]), stored)
    assert inventory.count is None and inventory.reasons == ("reference_inventory_order_mismatch",)


@pytest.mark.parametrize("paragraph", [True, False])
def test_known_zero_retains_real_minimum_and_maximum_arithmetic(paragraph):
    inventory = measure(source(paragraph=paragraph), [])
    assert inventory.status == "complete" and inventory.count == 0
    ctx = replace(_ctx(), reference_inventory=inventory)
    rule = get_rule(REFERENCE_COUNT_RULE_ID)
    minimum = rule.run(FakeCriterion("At least 1 reference"), ctx)
    maximum = rule.run(FakeCriterion("No more than 0 references"), ctx)
    assert minimum.outcome == ResultOutcome.failed and minimum.detail["actual_count"] == 0
    assert maximum.outcome == ResultOutcome.passed and maximum.detail["actual_count"] == 0
    assert (
        get_rule(CITATION_STYLE_RULE_ID).run(FakeCriterion("Use APA style"), ctx).outcome
        == ResultOutcome.not_applicable
    )


def test_unparsed_metadata_and_normalized_whitespace_do_not_remove_countable_entries():
    raw = "[1] An entry whose metadata cannot be parsed."
    stored = [FakeCitation(0, "  " + raw + "  ", "parse_failed")]
    inventory = measure(source([raw]), stored)
    assert inventory.status == "complete" and inventory.count == 1


@pytest.mark.parametrize("kind", ["missing_heading", "legacy", "unresolved", "ambiguous"])
def test_unknown_or_ambiguous_source_never_becomes_a_count(kind):
    entries = ["[1] " + REF_A, "[1] " + REF_B] if kind == "ambiguous" else [REF_A]
    extraction = source(
        entries,
        paragraph=False,
        heading=kind != "missing_heading",
        legacy=kind == "legacy",
        issue_page=2 if kind == "unresolved" else None,
    )
    assert measure(extraction, rows(*entries)).count is None


def test_unrelated_body_reading_issue_does_not_invalidate_bibliography_measurement():
    inventory = measure(source([REF_A], paragraph=False, issue_page=1), rows(REF_A))
    assert inventory.status == "complete" and inventory.count == 1


@pytest.mark.parametrize("kind", ["table", "image", "unlocated", "omitted"])
@pytest.mark.parametrize("entries", [[], [REF_A]])
def test_rich_content_can_obstruct_zero_or_nonzero_measurements(kind, entries):
    extraction = source(entries, eof=True)
    position = len(extraction.blocks)
    if kind == "table":
        extraction.tables = [TableBlock(paragraph=position, rows=[[REF_B]])]
    elif kind in {"image", "unlocated"}:
        extraction.images = [ImageBlock(paragraph=None if kind == "unlocated" else position)]
    else:
        extraction.docx_omitted_image_occurrences = 1
    inventory = measure(extraction, rows(*entries))
    assert inventory.status == "partial" and inventory.count is None


@pytest.mark.parametrize("position", [0, 3, 4])
def test_located_rich_content_proven_outside_empty_docx_scope_is_not_a_global_veto(position):
    extraction = source()
    extraction.blocks[-1].paragraph = 3
    extraction.section_tree.nodes[-1].paragraph = 3
    extraction.images = [ImageBlock(paragraph=position)]
    extraction.tables = [TableBlock(paragraph=position, rows=[[REF_B]])]
    assert measure(extraction, []).count == 0


@pytest.mark.parametrize(
    "text", ["At least 1 reference", "No more than 10 references", "Use APA style"]
)
async def test_actual_context_route_and_structural_result_withhold_unmeasured_grade(
    monkeypatch, text
):
    class Rows:
        def scalars(self):
            return self

        def all(self):
            return []

    session = SimpleNamespace(
        execute=AsyncMock(return_value=Rows()), add=lambda row: None, commit=AsyncMock()
    )
    extraction = source([REF_A])
    monkeypatch.setattr(context, "load_raw_store_async", AsyncMock(return_value=extraction))
    ctx = await context.build_rule_context(
        session,
        SimpleNamespace(id=1, content_hash=extraction.docx_source_sha256),
        Settings(_env_file=None),
    )
    criterion = FakeCriterion(text)
    decision = route_criterion(criterion, criterion_id=1, raw_type="structural")
    result = await run_structural_check(session, 1, criterion, 1, decision, ctx)
    assert result.outcome == ResultOutcome.unverifiable and result.score is None
    assert "actual_count" not in result.detail
    assert result.detail["reference_inventory"]["count"] is None
    assert result.detail["reason"]


def test_direct_caller_without_inventory_does_not_gain_authority_from_nonempty_rows():
    ctx = replace(_ctx(citations=rows(REF_A)), reference_inventory=None)
    for rule_id, text in [
        (REFERENCE_COUNT_RULE_ID, "At least 1 reference"),
        (CITATION_STYLE_RULE_ID, "Use APA style"),
    ]:
        assert get_rule(rule_id).run(FakeCriterion(text), ctx).outcome == ResultOutcome.unverifiable
