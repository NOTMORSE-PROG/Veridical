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
from app.models.enums import CheckKind, ResultOutcome

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


def test_bug_048_reference_count_ignores_a_bound_about_a_different_unit():
    """BUG-048 root cause 2: a bound whose captured unit isn't a reference
    noun (here "internet", from "<= 2 internet sites") describes a
    DIFFERENT quantity than the total reference count and must never be
    silently applied to it -- that produced a confident, wrong FAIL on a
    real 17-reference document. Correct behavior is `unverifiable`, not a
    guessed pass or fail (charter rule 9)."""
    spec = get_rule(REFERENCE_COUNT_RULE_ID)
    criterion = FakeCriterion(
        text=(
            "The bibliography includes the required number of major references and "
            "contains no more than two internet sites."
        ),
        evidence=(
            "Beginner (1) for 3 major references and <= 2 internet sites, Exemplary (4) "
            "for more than 5 major references and <= 2 internet sites."
        ),
    )
    assert spec.matches(criterion)  # a reference noun AND a bound are present
    citations = [FakeCitation(order_index=i, raw_text=f"Ref {i} (2020).") for i in range(17)]
    outcome = spec.run(criterion, _ctx(citations=citations))
    assert outcome.outcome == ResultOutcome.unverifiable
    assert outcome.detail["found_unit"] == "internet"


def test_reference_count_applies_a_bound_with_no_captured_unit():
    """A bare bound with no unit word right after the number (e.g. cut
    off by punctuation) is ambiguous but assumed to be about references,
    since the rule only matched because a reference noun is already
    present elsewhere in the text -- this must keep working, not
    regress into `unverifiable` for every bound."""
    spec = get_rule(REFERENCE_COUNT_RULE_ID)
    criterion = FakeCriterion(text="References: at least 15, no exceptions.")
    citations = [FakeCitation(order_index=i, raw_text=f"Ref {i} (2020).") for i in range(17)]
    outcome = spec.run(criterion, _ctx(citations=citations))
    assert outcome.outcome == ResultOutcome.passed
    assert outcome.detail["actual_count"] == 17


def test_bug_048_reference_count_recognizes_an_adjective_between_number_and_noun():
    """backend-critic finding (BUG-048 review): `bound.unit` only ever
    captures the single word immediately adjacent to the number, so
    ordinary "at least 5 major references" phrasing captures unit="major"
    (an adjective, not the noun) -- treating that as "not about
    references" would be a real regression versus the pre-fix behavior,
    which never checked unit at all and got this ordinary case right."""
    spec = get_rule(REFERENCE_COUNT_RULE_ID)
    criterion = FakeCriterion(text="The bibliography must include at least 5 major references.")
    citations = [FakeCitation(order_index=i, raw_text=f"Ref {i} (2020).") for i in range(17)]
    outcome = spec.run(criterion, _ctx(citations=citations))
    assert outcome.outcome == ResultOutcome.passed
    assert outcome.detail["actual_count"] == 17


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


def test_bug_048_citation_style_does_not_collide_with_apa_via_substring():
    """backend-critic finding (BUG-048 review): `_named_style` had root
    cause 1's exact bug ("apa" is a substring of "apart") one function
    below where `_matches_citation_style` was already fixed. A criterion
    genuinely naming IEEE style, whose wording separately contains a
    word like "apart", must not be silently reclassified as an APA-style
    check -- that would run the year-in-parentheses heuristic against
    IEEE-numbered citations and produce a confident, wrong FAIL instead
    of the honest "not yet checked automatically" `unverifiable`."""
    spec = get_rule(CITATION_STYLE_RULE_ID)
    criterion = FakeCriterion(
        text="References must be cited consistently, apart from figures, following IEEE style."
    )
    assert spec.matches(criterion)
    citations = [FakeCitation(order_index=0, raw_text="[1] J. Smith, A study of things, 2020.")]
    outcome = spec.run(criterion, _ctx(citations=citations))
    assert outcome.outcome == ResultOutcome.unverifiable
    assert "ieee" in outcome.detail["reason"]


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


@pytest.mark.parametrize(
    "text",
    [
        "must not exceed 100 pages",  # max bound: 0 satisfies it trivially
        "must be at least 60 pages",  # min bound: 0 trivially fails it
    ],
)
def test_page_limit_zero_pages_is_not_applicable_not_a_verdict(text):
    """BUG-174: a zero-page extraction (production: ~19% of the local raw
    store, ids 6/7/13/23/24 -- `page_count=0, blocks=0, text_chars=0`)
    used to PASS "must not exceed 100 pages" outright, because zero
    satisfies every maximum -- feeding a fabricated pass into the
    composite score (ground rule 9: "N/A is not passed"). The symmetric
    min-bound case ("must be at least 60 pages" -> failed at 0) is
    EQUALLY dishonest even though it happens to look like a correct
    verdict: zero pages here doesn't mean the manuscript genuinely has no
    pages, it means the extraction produced nothing, and that is an
    unknown, not a fact -- the same unconditional-guard convention the
    sibling `anchor_kind != "page"` check directly above already uses in
    this function."""
    spec = get_rule(PAGE_LIMIT_RULE_ID)
    outcome = spec.run(FakeCriterion(text=text), _ctx(anchor_kind="page", page_count=0))
    assert outcome.outcome == ResultOutcome.not_applicable


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


def test_bug_048_acceptable_does_not_collide_with_table_via_substring():
    """BUG-048 root cause 1, the canary: `contains_any` used to be a bare
    substring test, and "table" is a substring of "acceptable" -- exactly
    the level-name word a levelled rubric's evidence quotes verbatim
    ("Acceptable (2) for some errors"). A criterion whose evidence merely
    describes an "Acceptable" performance level must not be mistaken for
    one asking about a literal document table."""
    spec = get_rule(TABLE_FORMAT_RULE_ID)
    criterion = FakeCriterion(
        text="The bibliography is formatted correctly with minimal to no errors.",
        evidence=(
            "Check the bibliography formatting against the required style guide using a "
            "1-4 scale: Beginner (1) for correct format with many errors, Acceptable (2) "
            "for some errors, Proficient (3) for few errors, and Exemplary (4) for no errors."
        ),
    )
    assert not spec.matches(criterion)


def test_bug_048_table_format_rule_still_matches_a_real_table_criterion():
    """The word-boundary fix must not overcorrect into false negatives --
    a criterion that genuinely talks about tables still matches."""
    spec = get_rule(TABLE_FORMAT_RULE_ID)
    criterion = FakeCriterion(text="Tables must be properly formatted with captions")
    assert spec.matches(criterion)


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

GOLDEN_RUBRIC_TIP = Path(__file__).resolve().parents[2] / "context" / "golden" / "rubric_tip.json"

golden_rubric_tip_only = pytest.mark.skipif(
    not GOLDEN_RUBRIC_TIP.exists(),
    reason="golden rubric decomposition is local-only (context/ gitignored); run locally",
)


@demo_pdf_only
@golden_rubric_tip_only
def test_bug_048_all_eleven_real_tip_criteria_route_and_execute_with_no_false_structural_verdict():
    """BUG-048's own regression test, run for real: routes and executes
    ALL 11 criteria of the owner's real TIP rubric decomposition
    (context/golden/rubric_tip.json) against the owner's real manuscript
    extraction (VERIDICAL-DOCUMENTATION.pdf, 17 real references) -- the
    exact reproduction the audit used. Before the fix this produced
    "zero correct": a substring collision ("acceptable" contains "table")
    mis-routed the bibliography-formatting criterion into
    table_formatting_presence (a confident, wrong FAIL: "No tables were
    found in the manuscript"), and the reference-count rule silently
    applied a "<= 2 internet sites" sub-bound to the TOTAL reference
    count (a confident, wrong FAIL on a real 17-reference document). A
    synthetic 4-criterion fixture is what let this ship; this is the
    real, current, real-Gemini-produced 11-criterion decomposition, end
    to end."""
    import json

    from app.checks.router import route_criterion

    data = json.loads(GOLDEN_RUBRIC_TIP.read_text(encoding="utf-8"))
    criteria = data["criteria"]
    assert len(criteria) == 11  # golden evidence hasn't silently drifted
    structural_source_count = sum(1 for c in criteria if c["type"] == "structural")
    assert structural_source_count == 4  # matches the golden file's own structural_count

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

    decisions = [
        route_criterion(
            FakeCriterion(text=c["text"], evidence=c["evidence_needed"]),
            criterion_id=i,
            raw_type=c["type"],
        )
        for i, c in enumerate(criteria)
    ]
    # Coverage invariant BUG-048 itself calls out: every criterion gets
    # exactly one decision, structural or semantic, never dropped.
    assert len(decisions) == 11
    assert all(d.kind in (CheckKind.structural, CheckKind.semantic) for d in decisions)

    # The two criteria that were ALREADY correctly falling back to AI (no
    # keyword collision involved) stay that way -- "purpose in a single
    # sentence" and "writing free of mechanical errors" have no
    # purpose-built rule in this registry.
    assert decisions[1].kind == CheckKind.semantic and decisions[1].degraded
    assert decisions[5].kind == CheckKind.semantic and decisions[5].degraded

    # BUG-048 root cause 1: "bibliography...formatted correctly...
    # Acceptable(2)" must NOT match table_formatting_presence via the
    # "acceptable" contains "table" substring collision -- that produced
    # a confident, wrong FAIL ("No tables were found in the manuscript").
    # With the collision gone, the criterion's own mention of
    # "bibliography" legitimately matches `required_section_present`
    # instead (the generic, load-last "does a named section exist" rule)
    # -- and, via the reference_titles synonym fix above, correctly finds
    # the document's real "REFERENCES" section rather than falsely
    # failing on a bibliography/references naming mismatch.
    bibliography_format_decision = decisions[9]
    assert bibliography_format_decision.kind == CheckKind.structural
    assert bibliography_format_decision.rule_id == REQUIRED_SECTION_RULE_ID
    bibliography_outcome = get_rule(REQUIRED_SECTION_RULE_ID).run(
        FakeCriterion(text=criteria[9]["text"], evidence=criteria[9]["evidence_needed"]), ctx
    )
    assert bibliography_outcome.outcome == ResultOutcome.passed
    assert bibliography_outcome.detail["matched_title"] == "REFERENCES"

    # BUG-048 root cause 2: routes to reference_count_min_max (a real
    # reference noun AND a real bound ARE present in the text), but the
    # bound `extract_bound` actually finds ("<= 2 internet sites", a
    # per-level sub-constraint) is not about the total reference count --
    # it must resolve to `unverifiable`, never a false FAIL against the
    # document's real 17 references.
    reference_count_decision = decisions[10]
    assert reference_count_decision.kind == CheckKind.structural
    assert reference_count_decision.rule_id == REFERENCE_COUNT_RULE_ID
    spec = get_rule(REFERENCE_COUNT_RULE_ID)
    criterion = FakeCriterion(text=criteria[10]["text"], evidence=criteria[10]["evidence_needed"])
    outcome = spec.run(criterion, ctx)
    assert outcome.outcome == ResultOutcome.unverifiable
    assert outcome.detail["found_unit"] == "internet"


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
