"""V-016 unit tests: the structural rule library (app/checks/rules/*) and
the Tier-0 shadow signal layer (app/checks/signals.py). Pure functions,
no DB, no LLM, no network — matches every rule's own contract.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.checks.rules import RuleContext, get_rule, registered_rules
from app.checks.rules.bounds import extract_bound, to_points
from app.checks.rules.geometry import RULE_ID as MARGINS_RULE_ID
from app.checks.rules.pages import RULE_ID as PAGE_LIMIT_RULE_ID
from app.checks.rules.references import CITATION_STYLE_RULE_ID, REFERENCE_COUNT_RULE_ID
from app.checks.rules.sections import REQUIRED_SECTION_RULE_ID, SECTION_ORDER_RULE_ID
from app.checks.rules.tables import RULE_ID as TABLE_FORMAT_RULE_ID
from app.checks.signals import compute_shadow_signals
from app.config import get_settings
from app.ingest.schemas import PageGeometry, SectionNode, SectionTree, TableBlock
from app.models.enums import ResultOutcome

DEMO_PDF = Path(__file__).resolve().parents[2] / "VERIDICAL-DOCUMENTATION.pdf"


@dataclass
class FakeCriterion:
    text: str
    evidence: str | None = None


@dataclass
class FakeCitation:
    order_index: int
    raw_text: str
    parse_status: str = "parsed"


def _ctx(
    *,
    anchor_kind: str = "page",
    page_count: int = 10,
    section_tree: SectionTree | None = None,
    geometry: list[PageGeometry] | None = None,
    tables: list[TableBlock] | None = None,
    citations: list | None = None,
) -> RuleContext:
    settings = get_settings()
    return RuleContext(
        manuscript_id=1,
        anchor_kind=anchor_kind,
        page_count=page_count,
        section_tree=section_tree or SectionTree(source="heuristics", nodes=[]),
        geometry=geometry or [],
        tables=tables or [],
        citations=citations or [],
        margin_tolerance_pts=settings.structural_margin_tolerance_pts,
        citation_style_min_ratio=settings.structural_citation_style_min_ratio,
        table_caption_min_ratio=settings.structural_table_caption_min_ratio,
    )


# --- registry shape -----------------------------------------------------------


def test_all_seven_rule_families_are_registered():
    ids = {spec.rule_id for spec in registered_rules()}
    assert ids == {
        MARGINS_RULE_ID,
        PAGE_LIMIT_RULE_ID,
        REFERENCE_COUNT_RULE_ID,
        CITATION_STYLE_RULE_ID,
        TABLE_FORMAT_RULE_ID,
        SECTION_ORDER_RULE_ID,
        REQUIRED_SECTION_RULE_ID,
    }


def test_required_section_present_is_the_last_registered_rule():
    """Precedence matters (V-015's find_matching_rule returns the FIRST
    match): the most generic rule must never shadow the specific ones."""
    assert registered_rules()[-1].rule_id == REQUIRED_SECTION_RULE_ID


# --- bounds.py -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind", "low", "high", "unit"),
    [
        ("at least 15 references", "min", 15.0, None, "references"),
        ("must not exceed 100 pages", "max", 100.0, None, "pages"),
        ("between 10 and 20 sources", "range", 10.0, 20.0, "sources"),
        ("exactly 5 tables", "exact", 5.0, None, "tables"),
        ("1 inch margins", "exact", 1.0, None, "inch"),
    ],
)
def test_extract_bound_recognizes_common_phrasings(text, kind, low, high, unit):
    bound = extract_bound(text)
    assert bound is not None
    assert (bound.kind, bound.low, bound.high, bound.unit) == (kind, low, high, unit)


def test_extract_bound_returns_none_when_no_number_present():
    assert extract_bound("The argument must be well developed") is None


def test_to_points_converts_recognized_length_units_only():
    assert to_points(1, "inch") == pytest.approx(72.0)
    assert to_points(1, "cm") == pytest.approx(28.3465)
    assert to_points(5, "references") is None


# --- sections.py: required_section_present -------------------------------------


def _tree(*nodes: SectionNode) -> SectionTree:
    return SectionTree(source="heuristics", nodes=list(nodes))


def test_required_section_present_matches_and_finds_a_synonym_section():
    spec = get_rule(REQUIRED_SECTION_RULE_ID)
    criterion = FakeCriterion(text="The manuscript must include an Abstract")
    assert spec.matches(criterion)
    ctx = _ctx(section_tree=_tree(SectionNode(title="ABSTRACT", level=1, page=2)))
    outcome = spec.run(criterion, ctx)
    assert outcome.outcome == ResultOutcome.passed
    assert outcome.anchor == "page 2"


def test_required_section_present_matches_and_finds_a_numbered_chapter():
    spec = get_rule(REQUIRED_SECTION_RULE_ID)
    criterion = FakeCriterion(text="Chapter 1 must be present")
    ctx = _ctx(
        section_tree=_tree(
            SectionNode(title="CHAPTER 1 INTRODUCTION", level=1, page=5, numbering="1")
        )
    )
    outcome = spec.run(criterion, ctx)
    assert outcome.outcome == ResultOutcome.passed
    assert outcome.anchor == "page 5"


def test_required_section_present_fails_honestly_when_absent():
    spec = get_rule(REQUIRED_SECTION_RULE_ID)
    criterion = FakeCriterion(text="The manuscript must include a Glossary")
    ctx = _ctx(section_tree=_tree(SectionNode(title="ABSTRACT", level=1, page=2)))
    outcome = spec.run(criterion, ctx)
    assert outcome.outcome == ResultOutcome.failed
    assert "glossary" in outcome.detail["target"]


def test_required_section_present_does_not_match_unrelated_criteria():
    spec = get_rule(REQUIRED_SECTION_RULE_ID)
    assert not spec.matches(FakeCriterion(text="The argument is well developed"))


# --- sections.py: section_order -------------------------------------------------


def test_section_order_passes_for_increasing_chapter_numbers():
    spec = get_rule(SECTION_ORDER_RULE_ID)
    criterion = FakeCriterion(text="Chapters must appear in order")
    ctx = _ctx(
        section_tree=_tree(
            SectionNode(title="CH1", level=1, numbering="1"),
            SectionNode(title="CH2", level=1, numbering="2"),
            SectionNode(title="CH3", level=1, numbering="3"),
        )
    )
    outcome = spec.run(criterion, ctx)
    assert outcome.outcome == ResultOutcome.passed


def test_section_order_fails_for_an_out_of_order_chapter():
    spec = get_rule(SECTION_ORDER_RULE_ID)
    criterion = FakeCriterion(text="Chapters must appear in order")
    ctx = _ctx(
        section_tree=_tree(
            SectionNode(title="CH1", level=1, numbering="1"),
            SectionNode(title="CH3", level=1, numbering="3", page=9),
            SectionNode(title="CH2", level=1, numbering="2", page=20),
        )
    )
    outcome = spec.run(criterion, ctx)
    assert outcome.outcome == ResultOutcome.failed
    assert outcome.anchor == "page 20"


def test_section_order_is_not_applicable_without_enough_numbered_sections():
    spec = get_rule(SECTION_ORDER_RULE_ID)
    criterion = FakeCriterion(text="Chapters must appear in order")
    ctx = _ctx(section_tree=_tree(SectionNode(title="CH1", level=1, numbering="1")))
    outcome = spec.run(criterion, ctx)
    assert outcome.outcome == ResultOutcome.not_applicable


# --- references.py: reference_count_min_max -------------------------------------


@pytest.mark.parametrize(
    ("text", "n_citations", "expected"),
    [
        ("at least 15 references", 17, ResultOutcome.passed),
        ("at least 15 references", 10, ResultOutcome.failed),
        ("no more than 10 references", 17, ResultOutcome.failed),
        ("no more than 10 references", 5, ResultOutcome.passed),
        ("exactly 3 references", 3, ResultOutcome.passed),
        ("between 10 and 20 references", 15, ResultOutcome.passed),
        ("between 10 and 20 references", 25, ResultOutcome.failed),
    ],
)
def test_reference_count_min_max(text, n_citations, expected):
    spec = get_rule(REFERENCE_COUNT_RULE_ID)
    criterion = FakeCriterion(text=text)
    assert spec.matches(criterion)
    citations = [
        FakeCitation(order_index=i, raw_text=f"Ref {i} (2020).") for i in range(n_citations)
    ]
    outcome = spec.run(criterion, _ctx(citations=citations))
    assert outcome.outcome == expected
    assert outcome.detail["actual_count"] == n_citations


def test_reference_count_does_not_match_when_no_number_present():
    spec = get_rule(REFERENCE_COUNT_RULE_ID)
    assert not spec.matches(FakeCriterion(text="References must use APA style"))


# --- references.py: citation_style_sniff -----------------------------------------


def test_citation_style_sniff_passes_on_apa_like_references():
    spec = get_rule(CITATION_STYLE_RULE_ID)
    criterion = FakeCriterion(text="References must use APA style")
    assert spec.matches(criterion)
    citations = [
        FakeCitation(order_index=i, raw_text=f"Smith, J. ({2000 + i}). A study of things.")
        for i in range(5)
    ]
    outcome = spec.run(criterion, _ctx(citations=citations))
    assert outcome.outcome == ResultOutcome.passed


def test_citation_style_sniff_fails_when_years_are_not_parenthesized():
    spec = get_rule(CITATION_STYLE_RULE_ID)
    criterion = FakeCriterion(text="References must use APA style")
    citations = [
        FakeCitation(order_index=i, raw_text=f"Smith J, {2000 + i}, A study of things, Journal.")
        for i in range(5)
    ]
    outcome = spec.run(criterion, _ctx(citations=citations))
    assert outcome.outcome == ResultOutcome.failed


def test_citation_style_sniff_is_unverifiable_for_unsupported_styles():
    spec = get_rule(CITATION_STYLE_RULE_ID)
    criterion = FakeCriterion(text="References must use MLA style")
    citations = [FakeCitation(order_index=0, raw_text="Smith, J. (2020). A study.")]
    outcome = spec.run(criterion, _ctx(citations=citations))
    assert outcome.outcome == ResultOutcome.unverifiable


def test_citation_style_sniff_not_applicable_with_no_citations():
    spec = get_rule(CITATION_STYLE_RULE_ID)
    outcome = spec.run(FakeCriterion(text="References must use APA style"), _ctx(citations=[]))
    assert outcome.outcome == ResultOutcome.not_applicable


# --- pages.py: page_limit --------------------------------------------------------


def test_page_limit_not_applicable_for_docx():
    spec = get_rule(PAGE_LIMIT_RULE_ID)
    criterion = FakeCriterion(text="The manuscript must not exceed 60 pages")
    outcome = spec.run(criterion, _ctx(anchor_kind="paragraph", page_count=0))
    assert outcome.outcome == ResultOutcome.not_applicable


@pytest.mark.parametrize(
    ("text", "page_count", "expected"),
    [
        ("must not exceed 60 pages", 47, ResultOutcome.passed),
        ("must not exceed 60 pages", 80, ResultOutcome.failed),
        ("at least 40 pages", 47, ResultOutcome.passed),
    ],
)
def test_page_limit_pdf(text, page_count, expected):
    spec = get_rule(PAGE_LIMIT_RULE_ID)
    outcome = spec.run(FakeCriterion(text=text), _ctx(anchor_kind="page", page_count=page_count))
    assert outcome.outcome == expected


# --- geometry.py: margins_spacing ------------------------------------------------


def test_margins_not_applicable_without_geometry():
    spec = get_rule(MARGINS_RULE_ID)
    criterion = FakeCriterion(text="Margins must be exactly 1 inch")
    assert spec.matches(criterion)
    outcome = spec.run(criterion, _ctx(geometry=[]))
    assert outcome.outcome == ResultOutcome.not_applicable


def test_margins_pass_within_tolerance():
    spec = get_rule(MARGINS_RULE_ID)
    criterion = FakeCriterion(text="Margins must be exactly 1 inch")
    geo = [PageGeometry(page=1, width=612, height=792, margins=(72.0, 73.0, 71.0, 74.0))]
    outcome = spec.run(criterion, _ctx(geometry=geo))
    assert outcome.outcome == ResultOutcome.passed


def test_margins_fail_outside_tolerance():
    spec = get_rule(MARGINS_RULE_ID)
    criterion = FakeCriterion(text="Margins must be exactly 1 inch")
    geo = [PageGeometry(page=1, width=612, height=792, margins=(72.0, 40.0, 71.0, 74.0))]
    outcome = spec.run(criterion, _ctx(geometry=geo))
    assert outcome.outcome == ResultOutcome.failed
    assert outcome.anchor == "page 1"


def test_margins_min_bound():
    spec = get_rule(MARGINS_RULE_ID)
    criterion = FakeCriterion(text="Margins must be at least 1 inch")
    geo_ok = [PageGeometry(page=1, width=612, height=792, margins=(80.0, 80.0, 80.0, 80.0))]
    geo_bad = [PageGeometry(page=1, width=612, height=792, margins=(40.0, 80.0, 80.0, 80.0))]
    assert spec.run(criterion, _ctx(geometry=geo_ok)).outcome == ResultOutcome.passed
    assert spec.run(criterion, _ctx(geometry=geo_bad)).outcome == ResultOutcome.failed


# --- tables.py: table_formatting_presence ---------------------------------------


def test_table_formatting_fails_when_no_tables_present():
    spec = get_rule(TABLE_FORMAT_RULE_ID)
    criterion = FakeCriterion(text="Tables must be properly formatted with captions")
    assert spec.matches(criterion)
    outcome = spec.run(criterion, _ctx(tables=[]))
    assert outcome.outcome == ResultOutcome.failed


def test_table_formatting_passes_when_most_tables_are_captioned():
    spec = get_rule(TABLE_FORMAT_RULE_ID)
    criterion = FakeCriterion(text="Tables must be properly formatted with captions")
    tables = [
        TableBlock(page=1, rows=[["a"]], caption="Table 1. Results"),
        TableBlock(page=2, rows=[["b"]], caption="Table 2. More results"),
        TableBlock(page=3, rows=[["c"]], caption="Table 3. Even more results"),
        TableBlock(page=4, rows=[["d"]], caption="Table 4. Final results"),
        TableBlock(page=5, rows=[["e"]], caption=None),
    ]
    outcome = spec.run(criterion, _ctx(tables=tables))  # 4/5 = 0.8, meets the default threshold
    assert outcome.outcome == ResultOutcome.passed


def test_table_formatting_fails_when_most_tables_lack_captions():
    spec = get_rule(TABLE_FORMAT_RULE_ID)
    criterion = FakeCriterion(text="Tables must be properly formatted with captions")
    tables = [
        TableBlock(page=1, rows=[["a"]], caption=None),
        TableBlock(page=2, rows=[["b"]], caption=None),
        TableBlock(page=3, rows=[["c"]], caption="Table 3."),
    ]
    outcome = spec.run(criterion, _ctx(tables=tables))
    assert outcome.outcome == ResultOutcome.failed
    assert outcome.anchor == "page 1"


# --- signals.py: Tier-0 shadow signal layer --------------------------------------


def test_shadow_signals_are_deterministic_pure_function():
    text = "This is a simple sentence. It has two sentences total."
    a = compute_shadow_signals(text)
    b = compute_shadow_signals(text)
    assert a == b
    assert a.basis == "heuristic"


def test_shadow_signals_inconclusive_on_empty_text():
    signals = compute_shadow_signals("")
    assert signals.shadow_verdict == "inconclusive"
    assert signals.word_count == 0


def test_shadow_signals_citation_density_counts_parenthetical_years():
    text = "As shown by prior work (Smith, 2020) and (Jones, 2021), the effect holds."
    signals = compute_shadow_signals(text)
    assert signals.citation_density_per_1000_words > 0


# --- the owner's real proposal PDF (local-only; CI/other machines skip) ---------

demo_pdf_only = pytest.mark.skipif(
    not DEMO_PDF.exists(),
    reason="owner's proposal PDF is local-only (D-007); run this suite locally",
)


@demo_pdf_only
def test_real_proposal_pdf_section_presence_and_reference_count_hand_checked():
    """AC: 'Owner's proposal PDF: section-presence + reference-count rules
    produce verifiably correct results (hand-checked)'. Ground truth (hand-
    verified against the actual document, also asserted in
    test_ingest_pdf.py's `_assert_demo_tree_correct`): Chapters 1/2/3 exist
    at pages 5/10/24 titled INTRODUCTION / REVIEW OF RELATED LITERATURE /
    RESEARCH METHODOLOGY, a REFERENCES section exists, and V-006's real
    extraction on this same file structures exactly 17 references."""
    from app.ingest.patterns import load_patterns
    from app.ingest.pdf import extract_document
    from app.ingest.references import extract_references

    settings = get_settings()
    result = extract_document(str(DEMO_PDF), settings)
    citations = extract_references(result, load_patterns())
    assert len(citations) == 17  # matches V0's recorded evidence exactly

    ctx = _ctx(
        anchor_kind=result.anchor_kind,
        page_count=result.page_count,
        section_tree=result.section_tree,
        citations=citations,
    )

    section_spec = get_rule(REQUIRED_SECTION_RULE_ID)
    for chapter_num, expected_page in ((1, 5), (2, 10), (3, 24)):
        criterion = FakeCriterion(text=f"Chapter {chapter_num} must be present")
        outcome = section_spec.run(criterion, ctx)
        assert outcome.outcome == ResultOutcome.passed
        assert outcome.anchor == f"page {expected_page}"

    references_outcome = section_spec.run(
        FakeCriterion(text="The manuscript must include a References section"), ctx
    )
    assert references_outcome.outcome == ResultOutcome.passed

    count_spec = get_rule(REFERENCE_COUNT_RULE_ID)
    at_least_15 = count_spec.run(FakeCriterion(text="At least 15 references are required"), ctx)
    assert at_least_15.outcome == ResultOutcome.passed
    at_most_10 = count_spec.run(FakeCriterion(text="No more than 10 references allowed"), ctx)
    assert at_most_10.outcome == ResultOutcome.failed
