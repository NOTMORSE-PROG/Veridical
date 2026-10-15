"""V-027 tests: in-text citation extraction + cross-match.

Synthetic APA fixtures, same convention as V-006's
`test_ingest_references.py::WELL_FORMED` — generic shapes, not real papers.
The owner's real proposal PDF hand-verification (ticket AC #1) is recorded
separately in the ticket file (local-only per D-007; the PDF and its
extracted text aren't committed).
"""

from dataclasses import dataclass
from time import monotonic

import pytest

from app.checks.citations.extract import (
    ORPHAN_WORDING,
    UNCITED_WORDING,
    InTextCitation,
    cross_match,
    extract_in_text_citations,
    merge_wrapped_lines,
    numbered_style_coverage,
    orphan_flags,
    uncited_flags,
)
from app.checks.citations.numbered import analyze_numbered_citations
from app.checks.citations.verify import _numeric_link_evidence
from app.config import get_settings
from app.findings.identity import FINDING_GROUP_FIELD
from app.ingest.schemas import TextBlock
from app.models.enums import FlagSeverity


def _block(text: str, *, page: int | None = 1, paragraph: int | None = None) -> TextBlock:
    return TextBlock(text=text, page=page, paragraph=paragraph, max_font_size=11.0, bold_ratio=0.0)


@dataclass
class FakeRef:
    order_index: int
    raw_text: str
    authors: list[str] | None
    year: int | None


def test_parenthetical_single_citation():
    blocks = [_block("Rubric quality varies across institutions (Reyes, 2023).")]
    out = extract_in_text_citations(blocks)
    assert len(out) == 1
    c = out[0]
    assert c.surnames == ["Reyes"]
    assert c.year == "2023"
    assert not c.is_narrative
    assert not c.is_secondary
    assert c.anchor == "p. 1"
    assert "Rubric quality" in c.claim_sentence


def test_parenthetical_multi_cite_group():
    blocks = [_block("Several studies agree (Damoco et al., 2024; Serquiña, 2025).")]
    out = extract_in_text_citations(blocks)
    assert {tuple(c.surnames) for c in out} == {("Damoco",), ("Serquiña",)}
    years = {c.year for c in out}
    assert years == {"2024", "2025"}


def test_parenthetical_two_authors_ampersand():
    blocks = [_block("This was measured directly (Smith & Jones, 2020).")]
    out = extract_in_text_citations(blocks)
    assert len(out) == 1
    assert out[0].surnames == ["Smith", "Jones"]


def test_narrative_citation_with_et_al():
    blocks = [_block("Damoco et al. (2024) found a similar pattern in state universities.")]
    out = extract_in_text_citations(blocks)
    assert len(out) == 1
    c = out[0]
    assert c.is_narrative
    assert c.surnames == ["Damoco"]
    assert c.year == "2024"


def test_narrative_two_authors_and():
    blocks = [_block("Reyes and Cruz (2023) proposed a rubric scoring model.")]
    out = extract_in_text_citations(blocks)
    assert len(out) == 1
    assert out[0].surnames == ["Reyes", "Cruz"]


def test_narrative_scan_is_bounded_on_capitalized_glossary_without_a_year():
    # BUG-324: a real 105-page ARM thesis stalled in F5 on an acronym list.
    # The old unanchored nested author regex took seconds on much shorter
    # capitalized no-year prefixes and effectively never finished this block.
    glossary = "\n".join(["Memory Protection Secure Attribution"] * 30)
    start = monotonic()
    assert extract_in_text_citations([_block(glossary, page=7)]) == []
    assert monotonic() - start < 1.0


def test_narrative_year_first_does_not_absorb_glossary_or_previous_line():
    text = "\n".join(["Memory Protection Secure Attribution"] * 30)
    citations = extract_in_text_citations(
        [_block(text + "\nHaan (2025) describes the limitation.", page=7)]
    )
    assert len(citations) == 1
    assert citations[0].surnames == ["Haan"]
    assert citations[0].raw_text == "Haan (2025)"


def test_narrative_pdf_line_wrap_between_author_and_year():
    citations = extract_in_text_citations(
        [_block("Prior results differ. Haan\n(2025) explains why.")]
    )
    assert len(citations) == 1
    assert citations[0].surnames == ["Haan"]
    assert citations[0].year == "2025"
    assert citations[0].raw_text == "Haan\n(2025)"


def test_narrative_et_al_line_wraps_and_lost_period_remain_linkable():
    for source in ("Smith\net al. (2020)", "Smith et\nal. (2020)", "Smith et al (2020)"):
        citations = extract_in_text_citations([_block(source)])
        assert len(citations) == 1
        assert citations[0].surnames == ["Smith"]
        assert citations[0].year == "2020"


def test_narrative_two_authors_keep_both_names_across_line_wrap():
    for connector in ("and", "&"):
        citations = extract_in_text_citations([_block(f"Smith\n{connector} Jones (2020)")])
        assert len(citations) == 1
        assert citations[0].surnames == ["Smith", "Jones"]


def test_wrapped_corporate_author_links_after_whitespace_normalization():
    citations = extract_in_text_citations([_block("Commission on\nHigher Education (2020)")])
    assert len(citations) == 1
    references = [
        FakeRef(
            0, "Commission on Higher Education (2020).", ["Commission on Higher Education"], 2020
        )
    ]
    result = cross_match(citations, references)
    assert len(result.linked) == 1
    assert result.orphans == []
    assert result.uncited == []


def test_ambiguous_multi_line_corporate_author_abstains():
    for source in (
        "Commission\non Higher\nEducation (2020)",
        "Commission\nHigher\nEducation (2020)",
        "Commission on\nHigher\nEducation (2020)",
    ):
        gaps: set[str] = set()
        citations = extract_in_text_citations([_block(source)], coverage_gaps=gaps)
        assert citations == []
        assert gaps & {"author_line_wrap_ambiguous", "author_line_wrap_limit"}


def test_single_word_heading_before_narrative_author_does_not_veto_check():
    gaps: set[str] = set()
    citations = extract_in_text_citations(
        [_block("Methods\nHaan (2020) describes the approach.")], coverage_gaps=gaps
    )
    assert len(citations) == 1
    assert citations[0].surnames == ["Haan"]
    assert gaps == set()


def test_cropped_claim_keeps_linkage_complete():
    gaps: set[str] = set()
    text = "A" * 620 + " (Reyes, 2023)."
    citations = extract_in_text_citations([_block(text)], coverage_gaps=gaps)
    assert len(citations) == 1
    assert citations[0].surnames == ["Reyes"]
    assert citations[0].claim_excerpt_cropped is True
    assert len(citations[0].claim_sentence) <= get_settings().citation_claim_excerpt_max_chars
    assert gaps == set()


def test_overlong_capitalized_author_candidate_abstains_instead_of_orphan():
    gaps: set[str] = set()
    text = " ".join(["Memory Protection Secure Attribution"] * 100) + " (2025)"
    start = monotonic()
    citations = extract_in_text_citations([_block(text)], coverage_gaps=gaps)
    assert citations == []
    assert "author_limit" in gaps
    assert monotonic() - start < 1.0


def test_short_long_token_glossary_abstains_instead_of_false_citation():
    gaps: set[str] = set()
    text = " ".join(["Memory Protection Secure Attribution"] * 2) + " (2025)"
    assert len(text) < get_settings().citation_author_max_chars + 7
    assert extract_in_text_citations([_block(text)], coverage_gaps=gaps) == []
    assert "author_limit" in gaps


def test_oversized_parenthetical_author_abstains_without_regex_stall():
    gaps: set[str] = set()
    text = "(" + "Alpha Beta " * 30 + ", 2025)"
    start = monotonic()
    citations = extract_in_text_citations([_block(text)], coverage_gaps=gaps)
    assert citations == []
    assert "author_limit" in gaps or "parenthetical_entry_limit" in gaps
    assert monotonic() - start < 1.0


def test_unclosed_secondary_tails_and_many_years_use_bounded_work():
    gaps: set[str] = set()
    malformed = "(2020, as cited in " * 6400
    start = monotonic()
    assert extract_in_text_citations([_block(malformed)], coverage_gaps=gaps) == []
    assert "narrative_tail_unreadable" in gaps
    assert monotonic() - start < 1.0

    settings = get_settings().model_copy(update={"citation_in_text_max_matches": 100})
    gaps.clear()
    repeated = "Smith (2020) " * 1600
    start = monotonic()
    citations = extract_in_text_citations([_block(repeated)], settings=settings, coverage_gaps=gaps)
    assert len(citations) == 100
    assert all(
        len(citation.claim_sentence) <= settings.citation_claim_excerpt_max_chars
        for citation in citations
    )
    assert "citation_count_limit" in gaps
    assert monotonic() - start < 1.0


def test_year_letter_suffix_disambiguation():
    blocks = [_block("The first study (Cruz, 2024a) contradicts the second (Cruz, 2024b).")]
    out = extract_in_text_citations(blocks)
    assert {c.year for c in out} == {"2024a", "2024b"}
    refs = [
        FakeRef(0, "Cruz, M. (2024a). First title. Journal A.", ["Cruz, M."], 2024),
        FakeRef(1, "Cruz, M. (2024b). Second title. Journal B.", ["Cruz, M."], 2024),
    ]
    result = cross_match(out, refs)
    assert len(result.linked) == 2
    linked_by_year = {c.year: [r.order_index for r in refs_] for c, refs_ in result.linked}
    assert linked_by_year["2024a"] == [0]
    assert linked_by_year["2024b"] == [1]
    assert result.orphans == []
    assert result.uncited == []


def test_missing_year_letter_does_not_assign_claim_to_two_sources():
    cites = extract_in_text_citations([_block("An effect was observed (Cruz, 2024).")])
    refs = [
        FakeRef(0, "Cruz, M. (2024a). First title.", ["Cruz, M."], 2024),
        FakeRef(1, "Cruz, M. (2024b). Second title.", ["Cruz, M."], 2024),
    ]
    result = cross_match(cites, refs)
    assert result.linked == []
    assert result.orphans == []
    assert result.ambiguous == cites
    assert len(result.uncited) == 2  # F5's coverage gap suppresses absence flags


def test_secondary_citation_parenthetical_is_excluded_from_matching():
    blocks = [_block("An earlier claim (Rabinowitz, 1989, as cited in Colman, 2019) still holds.")]
    out = extract_in_text_citations(blocks)
    inner = [cite for cite in out if cite.is_secondary]
    outer = [cite for cite in out if not cite.is_secondary]
    # The inner source is never resolved (edge case, ticket): it is not
    # expected in this manuscript's reference list.
    assert [cite.surnames for cite in inner] == [["Rabinowitz"]]
    # The work it was read in IS cited (APA 7 8.6) and must be listed;
    # leaving it out made a listed Colman 2019 look unused.
    assert [(cite.surnames, cite.year) for cite in outer] == [(["Colman"], "2019")]
    result = cross_match(out, [])
    assert [cite.surnames for cite in result.orphans] == [["Colman"]]
    assert result.linked == []


def test_a_previous_sentences_secondary_citation_does_not_taint_the_next_one():
    """The look-back for "As cited in Jones (2020)" stops at the sentence end."""
    blocks = [
        _block(
            "Early systems used paper logs (Moreno, 1998, as cited in Iommi, 2025). "
            "Salvador (2024) found that reminders reduced missed deadlines."
        )
    ]
    out = extract_in_text_citations(blocks)
    salvador = [cite for cite in out if cite.surnames == ["Salvador"]]
    assert len(salvador) == 1 and not salvador[0].is_secondary


def test_secondary_citation_narrative_prefix_is_excluded_from_matching():
    blocks = [_block("As cited in Jones (2020), the original finding was inconclusive.")]
    out = extract_in_text_citations(blocks)
    assert len(out) == 1
    assert out[0].is_secondary


def test_corporate_author():
    blocks = [_block("Policy requires this (Commission on Higher Education, 2019).")]
    out = extract_in_text_citations(blocks)
    assert len(out) == 1
    assert out[0].surnames == ["Commission on Higher Education"]


def test_orphan_in_text_citation_flagged_low_severity():
    blocks = [_block("This claim cites a ghost source (Nakamura, 2021).")]
    out = extract_in_text_citations(blocks)
    result = cross_match(out, [])
    assert len(result.orphans) == 1
    flags = orphan_flags(result.orphans)
    assert len(flags) == 1
    assert flags[0].severity == FlagSeverity.low
    assert "Nakamura, 2021" in flags[0].detail["reason"]
    assert "fabricat" not in flags[0].detail["reason"].lower()
    assert "fake" not in flags[0].detail["reason"].lower()
    assert flags[0].detail["reason"] == ORPHAN_WORDING.format(authors="Nakamura", year="2021")


def test_uncited_reference_flagged_low_severity():
    blocks = [_block("No citations appear in this body text at all.")]
    out = extract_in_text_citations(blocks)
    refs = [FakeRef(0, "Reyes, J. (2023). A title. Journal.", ["Reyes, J."], 2023)]
    result = cross_match(out, refs)
    assert result.uncited == refs
    flags = uncited_flags(result.uncited)
    assert len(flags) == 1
    assert flags[0].severity == FlagSeverity.low
    assert flags[0].detail["reason"] == UNCITED_WORDING.format(first_author="Reyes, J.", year=2023)
    assert flags[0].page_anchor == "reference #1"


def test_uncited_reference_anchor_uses_printed_number_not_row_position():
    ref = FakeRef(2, "4. Van Lieshout, M. (2016). A title.", ["Van Lieshout"], 2016)
    flag = uncited_flags([ref])[0]
    assert flag.page_anchor == "reference #4"
    assert flag.evidence_excerpt == ref.raw_text


def test_reference_cited_multiple_times_is_not_uncited():
    blocks = [
        _block("Reyes (2023) first raised this."),
        _block("Later confirmed again (Reyes, 2023)."),
    ]
    out = extract_in_text_citations(blocks)
    refs = [FakeRef(0, "Reyes, J. (2023). A title. Journal.", ["Reyes, J."], 2023)]
    result = cross_match(out, refs)
    assert len(result.linked) == 2
    assert result.uncited == []
    assert result.orphans == []


def test_paragraph_anchor_used_when_no_page():
    blocks = [_block("Docx text has no pages (Reyes, 2023).", page=None, paragraph=5)]
    out = extract_in_text_citations(blocks)
    assert out[0].anchor == "¶5"


def test_reference_span_excluded_from_scan():
    """Guards the wiring, not the regex: a caller must pass
    `non_reference_blocks` output, not raw `result.blocks` — a bibliography
    entry ("Reyes, J. P., & Cruz, M. A. (2023). ...") would otherwise be
    misread as an in-text citation."""
    ref_entry = _block(
        "Reyes, J. P., & Cruz, M. A. (2023). Assessing capstone readiness. "
        "Philippine Journal of Education, 12(3), 45-67."
    )
    out = extract_in_text_citations([ref_entry])
    # The parser doesn't know this is a bibliography entry — it correctly
    # finds a citation-shaped span inside it. Exclusion is the caller's job
    # (non_reference_blocks), verified in test_ingest_references.py instead.
    assert len(out) == 1


def test_numbered_style_detector_does_not_link_array_indexes_to_unnumbered_references():
    blocks = [_block("The array element a[1] is updated.")]
    refs = [FakeRef(0, "Reyes, J. (2023). A title.", ["Reyes, J."], 2023)]

    coverage = numbered_style_coverage(blocks, refs)

    assert coverage.n_numbered_reference_entries == 0
    assert not coverage.linkage_unassessed(n_references=1, n_author_year_uncited=0)
    assert coverage.n_numeric_body_candidates == 0


def test_unmatched_numbered_body_style_without_parsed_references_is_unassessed():
    coverage = numbered_style_coverage([_block("Prior work [1] supports this.")], [])

    assert coverage.n_numbered_reference_entries == 0
    assert coverage.n_numeric_body_candidates == 1
    assert coverage.linkage_unassessed(n_references=0, n_author_year_uncited=0)


def test_numbered_style_detector_records_source_shape_without_guessing_linkage():
    blocks = [_block("The method uses [1], [2-3], and [4, 5].")]
    refs = [
        FakeRef(0, "[1] First source.", None, None),
        FakeRef(1, "[2] Second source.", None, None),
    ]

    coverage = numbered_style_coverage(blocks, refs)

    assert coverage.n_numbered_reference_entries == 2
    assert coverage.n_numeric_body_candidates == 3
    assert coverage.linkage_unassessed(n_references=2, n_author_year_uncited=2)


def test_numeric_interval_does_not_interrupt_complete_author_year_linkage():
    body = [_block("Values lie in [0, 1] and [1, 2]. Prior work agrees (Reyes, 2023).")]
    refs = [FakeRef(0, "Reyes, J. (2023). A title.", ["Reyes, J."], 2023)]
    coverage = numbered_style_coverage(body, refs)
    matched = cross_match(extract_in_text_citations(body), refs)

    assert coverage.n_numeric_body_candidates == 1
    assert matched.uncited == []
    assert not coverage.linkage_unassessed(
        n_references=len(refs), n_author_year_uncited=len(matched.uncited)
    )


def test_numbered_reference_marker_does_not_require_space_after_bracket():
    body = [_block("Prior work [1] supports this.")]
    refs = [
        FakeRef(0, "[1]Smith, J. (2020). A title.", None, None),
        FakeRef(1, "2. Jones, K. (2021). Another title.", None, None),
    ]

    assert numbered_style_coverage(body, refs).n_numbered_reference_entries == 2


def _numbered_refs(count: int) -> list[FakeRef]:
    return [
        FakeRef(index, f"[{index + 1}] Author, A. (2020). Source {index + 1}.", ["Author"], 2020)
        for index in range(count)
    ]


def test_numeric_links_use_printed_labels_not_reference_row_position():
    refs = _numbered_refs(7)
    refs[6].order_index = 99
    assessed = analyze_numbered_citations(
        [_block("Prior work [7] supplies this baseline.")], refs, max_range_span=20
    )

    assert assessed.inventory_complete
    assert len(assessed.linked) == 1
    assert assessed.linked[0][1] == [refs[6]]
    assert assessed.linked[0][0].raw_text == "[7]"
    assert assessed.linked[0][0].anchor == "p. 1"
    assert assessed.linked[0][0].start == 11
    assert not assessed.linkage_unassessed


def test_period_label_inventory_maps_bracketed_body_marks_with_exact_label_evidence():
    refs = [
        FakeRef(99, "1. Alpha A. 2020; Source one.", None, None),
        FakeRef(17, "2. Beta B. 2021; Source two.", None, None),
    ]
    body = _block("Earlier work [1] established this. Prior studies [1,2] agree.", page=7)

    assessed = analyze_numbered_citations([body], refs, max_range_span=20)

    assert assessed.inventory_complete
    assert [
        (mark.raw_text, [ref.order_index for ref in matched]) for mark, matched in assessed.linked
    ] == [
        ("[1]", [99]),
        ("[1,2]", [99, 17]),
    ]
    assert [mark.raw_text for mark in assessed.unresolved_marks] == ["[1]"]
    assert assessed.orphan_marks == [] and assessed.uncited == []
    evidence = _numeric_link_evidence(assessed)
    assert [(item["reference_order_index"], item["source_number"]) for item in evidence] == [
        (99, "1."),
        (17, "2."),
    ]
    assert all(item["source_block_index"] == 0 and item["page"] == 7 for item in evidence)
    assert all(item["source_offset"] == body.text.index("[1,2]") for item in evidence)
    assert all(item["raw_mark"] == "[1,2]" and item["strong_citation_context"] for item in evidence)


def test_period_label_gap_duplicate_and_merged_entry_disable_mapping():
    first = FakeRef(0, "1. Alpha A. 2020; Source one.", None, None)
    variants = [
        [first, FakeRef(1, "3. Gamma C. 2022; Source three.", None, None)],
        [first, FakeRef(1, "1. Duplicate D. 2023; Source four.", None, None)],
        [FakeRef(0, "1. Alpha A. 2020; Source one. 2. Beta B. 2021; Source two.", None, None)],
        [
            FakeRef(
                0, "1. Alpha A. 2020; Source one. 2. van Lieshout B. 2021; Source two.", None, None
            ),
            FakeRef(1, "2. Gamma C. 2022; Source three.", None, None),
        ],
    ]
    for refs in variants:
        assessed = analyze_numbered_citations(
            [_block("Prior work [1] is relevant.")], refs, max_range_span=20
        )
        assert not assessed.inventory_complete
        assert assessed.linked == [] and assessed.uncited == []
        assert assessed.linkage_unassessed


def test_period_label_inventory_does_not_link_statistical_interval_as_citation():
    assessed = analyze_numbered_citations(
        [_block("The 95% interval is [1,2]. Prior work [1] supports this.")],
        [
            FakeRef(0, "1. Alpha A. 2020; Source one.", None, None),
            FakeRef(1, "2. Beta B. 2021; Source two.", None, None),
        ],
        max_range_span=20,
    )

    assert assessed.inventory_complete
    assert [mark.raw_text for mark, _ in assessed.linked] == ["[1]"]
    assert assessed.orphan_marks == []


def test_period_label_inventory_does_not_treat_page_range_tail_as_merged_reference():
    refs = [
        FakeRef(0, "1. Alpha A. Journal 2020; 12: 119–125. PMID: 12345", None, None),
        FakeRef(1, "2. Beta B. Journal 2021; 8: 17–18. PMID: 67890", None, None),
    ]
    assessed = analyze_numbered_citations(
        [_block("Prior work [1] is relevant.")], refs, max_range_span=20
    )

    assert assessed.inventory_complete
    assert len(assessed.linked) == 1


def test_numeric_adjacent_list_and_range_marks_link_exact_references():
    refs = _numbered_refs(5)
    assessed = analyze_numbered_citations(
        [
            _block(
                "Prior studies [1][2] support this. Other studies [3, 4] agree. "
                "Related research [4-5] supports the design. "
                "Related research [1\u20132] supports the analysis."
            )
        ],
        refs,
        max_range_span=20,
    )

    assert [[r.raw_text[:3] for r in linked] for _, linked in assessed.linked] == [
        ["[1]"],
        ["[2]"],
        ["[3]", "[4]"],
        ["[4]", "[5]"],
        ["[1]", "[2]"],
    ]
    assert assessed.uncited == []
    assert assessed.orphan_marks == []


def test_numeric_out_of_range_mark_retains_source_evidence():
    body = _block("Prior work [1] supports this claim. See also [9].", page=4)
    assessed = analyze_numbered_citations(
        [body],
        _numbered_refs(2),
        max_range_span=20,
        source_block_index_by_id={id(body): 7},
    )

    assert [mark.raw_text for mark in assessed.orphan_marks] == ["[9]"]
    assert assessed.orphan_marks[0].anchor == "p. 4"
    assert assessed.orphan_marks[0].block_index == 7
    assert "[9]" in assessed.orphan_marks[0].claim_sentence


def test_numeric_long_unpunctuated_block_keeps_bounded_exact_mark_excerpt():
    body = _block(("word " * 4000) + "Prior work [9] is cited.")
    assessed = analyze_numbered_citations([body], _numbered_refs(1), max_range_span=20)

    assert len(assessed.orphan_marks) == 1
    assert "[9]" in assessed.orphan_marks[0].claim_sentence
    assert len(assessed.orphan_marks[0].claim_sentence) <= 240
    assert not assessed.orphan_marks[0].claim_sentence.startswith("…")
    assert assessed.orphan_marks[0].excerpt_cropped


def test_numeric_math_index_and_oversized_range_are_not_invented_links():
    assessed = analyze_numbered_citations(
        [_block("The array a[1] has values that lie in [1,2]. Prior studies [1-200] differ.")],
        _numbered_refs(2),
        max_range_span=20,
    )

    assert assessed.linked == []
    assert assessed.orphan_marks == []
    assert [mark.raw_text for mark in assessed.unresolved_marks] == ["[1-200]"]
    assert assessed.linkage_unassessed


def test_numeric_non_citation_brackets_do_not_create_orphan_or_hide_unused_reference():
    assessed = analyze_numbered_citations(
        [
            _block(
                "The valid range is [3, 4]. Figure [3] shows the output. "
                "The output interval is [1, 2]. Prior research [1] supports this."
            )
        ],
        _numbered_refs(2),
        max_range_span=20,
    )

    assert [mark.raw_text for mark, _ in assessed.linked] == ["[1]"]
    assert assessed.orphan_marks == []
    assert assessed.uncited == [_numbered_refs(2)[1]]
    assert assessed.unresolved_marks == []


def test_numeric_missing_duplicate_and_merged_reference_labels_disable_absence_claims():
    body = [_block("Prior work [1] is relevant.")]
    variants = [
        [_numbered_refs(1)[0], FakeRef(1, "[3] Missing two.", None, None)],
        [_numbered_refs(1)[0], FakeRef(1, "[1] Duplicate.", None, None)],
        [FakeRef(0, "[1] First source. [2] Merged source.", None, None)],
        [FakeRef(0, "[1] First source.[2] Merged source.", None, None)],
    ]
    for refs in variants:
        assessed = analyze_numbered_citations(body, refs, max_range_span=20)
        assert not assessed.inventory_complete
        assert assessed.linked == []
        assert assessed.uncited == []
        assert assessed.linkage_unassessed


def test_numeric_extreme_user_supplied_number_is_unassessed_not_a_check_crash():
    assessed = analyze_numbered_citations(
        [_block("Prior work [" + "9" * 5000 + "] is cited.")],
        _numbered_refs(1),
        max_range_span=20,
    )

    assert assessed.linked == []
    assert assessed.orphan_marks == []
    assert len(assessed.unresolved_marks) == 1
    assert assessed.linkage_unassessed


def test_numeric_oversized_comma_list_does_not_create_an_orphan_flag():
    huge_mark = "[" + ",".join(["9"] * 1000) + "]"
    assessed = analyze_numbered_citations(
        [_block(f"Prior work {huge_mark} is cited.")],
        _numbered_refs(1),
        max_range_span=20,
    )

    assert assessed.orphan_marks == []
    assert len(assessed.unresolved_marks) == 1


def test_numeric_scan_caps_many_marks_without_partial_absence_claims():
    assessed = analyze_numbered_citations(
        [_block("Prior work [1] " * 10000)],
        _numbered_refs(1),
        max_range_span=20,
    )

    assert assessed.scan_limited
    assert assessed.linkage_unassessed
    assert len(assessed.marks) == 512
    assert assessed.orphan_marks == []
    assert assessed.uncited == []
    assert assessed.n_linkage_unassessed == 1


def test_numeric_scan_caps_body_length_before_materializing_later_marks():
    assessed = analyze_numbered_citations(
        [_block("A" * 1_000_001 + " Prior work [1].")],
        _numbered_refs(1),
        max_range_span=20,
    )

    assert assessed.scan_limited
    assert assessed.marks == []
    assert assessed.orphan_marks == []
    assert assessed.uncited == []
    assert assessed.linkage_unassessed


def test_numeric_interval_in_apa_body_does_not_hide_genuinely_unused_reference():
    body = [_block("Values lie in [1,2]. Prior work agrees (Reyes, 2023).")]
    assessed = analyze_numbered_citations(
        body,
        [FakeRef(0, "Reyes, J. (2023). A title.", ["Reyes"], 2023)],
        max_range_span=20,
    )

    assert assessed.n_numeric_body_candidates == 1
    assert assessed.n_credible_body_marks == 0
    assert assessed.unresolved_marks == []


def test_numeric_ambiguous_body_mark_shields_possible_reference():
    assessed = analyze_numbered_citations(
        [_block("[2] may denote the second item. Prior work [1] is relevant.")],
        _numbered_refs(2),
        max_range_span=20,
    )

    assert [mark.raw_text for mark in assessed.unresolved_marks] == ["[2]"]
    assert assessed.uncited == []
    assert assessed.n_linkage_unassessed == 1


def test_one_orphan_citation_cited_twice_is_one_canonical_finding():
    """BUG-237 / BUG-251 AC5, citation arm.

    Measured on the real 47-page run: `citation_integrity` produced 27 flag
    rows for 14 distinct facts, so an instructor saw 13 duplicate verdicts.
    One in-text citation with no reference entry is ONE missing reference,
    however many times the manuscript cites it. Every occurrence stays
    flagged; only the verdict count collapses.
    """
    occurrences = [
        InTextCitation(
            surnames=["Chen"],
            year="2024",
            is_narrative=False,
            is_secondary=False,
            raw_text="(Chen, 2024)",
            anchor=anchor,
            page=page,
            paragraph=None,
            claim_sentence=sentence,
        )
        for anchor, page, sentence in (
            ("p. 5", 5, "Prior work reports a gain (Chen, 2024)."),
            ("p. 9", 9, "A different sentence entirely (Chen, 2024)."),
        )
    ]

    flags = orphan_flags(occurrences)

    assert len(flags) == 2, "both occurrences must still be flagged"
    assert len({f.detail[FINDING_GROUP_FIELD] for f in flags}) == 1
    # Keyed on the cited work, not on the sentence around it.
    assert flags[0].evidence_excerpt != flags[1].evidence_excerpt


def test_a_different_orphan_citation_is_not_grouped_with_it():
    """Grouping must never merge two genuinely missing references."""
    flags = orphan_flags(
        [
            InTextCitation(
                surnames=surnames,
                year=year,
                is_narrative=False,
                is_secondary=False,
                raw_text=raw,
                anchor="p. 5",
                page=5,
                paragraph=None,
                claim_sentence="A sentence.",
            )
            for surnames, year, raw in (
                (["Chen"], "2024", "(Chen, 2024)"),
                (["Chen"], "2025", "(Chen, 2025)"),
                (["Santos"], "2024", "(Santos, 2024)"),
            )
        ]
    )

    assert len({f.detail[FINDING_GROUP_FIELD] for f in flags}) == 3


# --- BUG-240: an author fragment is not a claim --------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Chen et al. (2024) reported a 12 point gain on the held-out split.",
        "Korthals et al. (2023) found no such effect in Philippine samples.",
        "Tan et al. (2026) propose a neuro-symbolic solver for word problems.",
        "Li et al. (2022) argue the opposite for low-resource languages.",
        "Chung et al. (2021) measured statistical literacy across four cohorts.",
    ],
)
def test_an_et_al_citation_yields_the_whole_claim_not_the_author_fragment(sentence):
    """The exact fragments the real 47-page run sent to claim-support grading.

    `_SENTENCE_BOUNDARY` split after "al." because "(" is in its lookahead
    class, so the graded "claim" was `Chen et al.` -- not a proposition an
    abstract can support or contradict. A schema-valid model answer to that
    is meaningless and the quota is spent for nothing.
    """
    cites = extract_in_text_citations([_block(sentence)])

    assert cites, "precondition: the citation must still be detected"
    claim = cites[0].claim_sentence
    assert claim == sentence, f"claim was truncated to {claim!r}"
    assert not claim.rstrip().endswith("et al."), "the graded claim is an author fragment"


def test_an_author_initial_does_not_end_the_claim():
    """An initial is not a sentence end: Smith, J. (2020) stays one claim."""
    sentence = "Smith, J. (2020) reported that the intervention raised scores."
    cites = extract_in_text_citations([_block(sentence)])

    assert cites
    assert cites[0].claim_sentence == sentence


def test_a_real_sentence_boundary_still_splits():
    """The control: the fix must not glue genuinely separate sentences together."""
    first = "Chen et al. (2024) reported a gain."
    second = "Dela Cruz (2025) found no such effect."
    cites = extract_in_text_citations([_block(f"{first} {second}")])

    claims = {c.claim_sentence for c in cites}
    assert first in claims and second in claims, claims


def test_a_parenthetical_citation_keeps_its_surrounding_proposition():
    sentence = "Statistical literacy predicts thesis quality (Chen et al., 2024)."
    cites = extract_in_text_citations([_block(sentence)])

    assert cites
    assert cites[0].claim_sentence == sentence


@pytest.mark.parametrize("abbrev", ["e.g.", "i.e.", "cf.", "Fig.", "vol.", "pp."])
def test_common_scholarly_abbreviations_do_not_end_a_claim(abbrev):
    sentence = f"Several models ({abbrev} BERT) were compared by Chen et al. (2024)."
    cites = extract_in_text_citations([_block(sentence)])

    assert cites
    assert cites[0].claim_sentence == sentence


# --- BUG-239 AC1: a shared coauthor plus a year is not a link -----------------


def _ref(order_index: int, authors: list[str], year: int, raw_text: str = "") -> FakeRef:
    return FakeRef(
        order_index=order_index,
        raw_text=raw_text or f"{authors[0]} ({year}). A Title.",
        authors=authors,
        year=year,
    )


def _cite(surnames: list[str], year: str, raw_text: str) -> InTextCitation:
    return InTextCitation(
        surnames=surnames,
        year=year,
        is_narrative=False,
        is_secondary=False,
        raw_text=raw_text,
        anchor="p. 4",
        page=4,
        paragraph=None,
        claim_sentence="The reported effect was large.",
    )


def test_a_coauthor_only_match_is_ambiguous_not_a_confident_link():
    """The residual BUG-239 defect: one candidate, so the old guard never fired.

    `(Schneider, 2024)` resolved to a Vladika-first reference, marked it
    cited, and handed its claim to support grading.
    """
    refs = [_ref(17, ["Vladika, M.", "Schneider, P."], 2024, "Vladika, M., Schneider, P. (2024).")]

    result = cross_match([_cite(["Schneider"], "2024", "(Schneider, 2024)")], refs)

    assert result.linked == [], "a coauthor-only match must not link"
    assert len(result.ambiguous) == 1, "it is the instructor's call, not an orphan"
    assert result.uncited == refs, "the reference must NOT be marked cited"


def test_the_reported_chen_collision_resolves_to_the_first_author_reference():
    """BUG-239's first reported collision, from the real bibliography.

    The ticket's complaint was that this linked **both** #1 (Chen first
    author) and #6 (Chen only a coauthor). The first-author rule resolves it
    to #1 alone, which is what the citation actually means -- so this case
    gets *less* ambiguous, not more. #6 must not be marked cited.
    """
    chen_first = _ref(1, ["Chen, H.", "Han, Z.", "Yan, Y."], 2026)
    chen_coauthor = _ref(6, ["Ferrer, R.", "Turgut, D.", "Chen, Z."], 2026)

    cite = _cite(["Chen"], "2026", "Chen et al. (2026)")
    result = cross_match([cite], [chen_first, chen_coauthor])

    assert len(result.linked) == 1
    assert [r.order_index for r in result.linked[0][1]] == [1]
    assert result.ambiguous == []
    assert chen_coauthor in result.uncited, "the coauthor reference must not be marked cited"


def test_a_narrative_multi_author_form_naming_no_first_author_is_ambiguous():
    """A narrative form naming three authors, none of whom is first."""
    refs = [_ref(6, ["Ferrer, R.", "Turgut, D.", "Chen, Z.", "Sonkar, S."], 2026)]

    result = cross_match(
        [_cite(["Turgut", "Chen", "Sonkar"], "2026", "Turgut, Chen, and Sonkar (2026)")], refs
    )

    assert result.linked == []
    assert len(result.ambiguous) == 1


def test_a_correctly_formed_et_al_citation_still_links():
    """The control that matters: the rule must be a no-op for good citations."""
    refs = [_ref(1, ["Chen, H.", "Han, Z.", "Yan, Y."], 2026)]

    result = cross_match([_cite(["Chen"], "2026", "Chen et al. (2026)")], refs)

    assert len(result.linked) == 1
    assert [r.order_index for r in result.linked[0][1]] == [1]
    assert result.ambiguous == []


def test_a_filipino_surname_with_a_particle_still_links():
    """A particle surname must fold to "dela cruz", not to the initial."""
    refs = [_ref(3, ["Dela Cruz, J.", "Santos, M."], 2025)]

    result = cross_match([_cite(["Dela Cruz"], "2025", "Dela Cruz et al. (2025)")], refs)

    assert len(result.linked) == 1, "the comma split must keep the particle"
    assert result.ambiguous == []


def test_an_organisational_author_without_a_comma_still_links():
    refs = [_ref(9, ["World Health Organization"], 2024)]

    result = cross_match([_cite(["World Health Organization"], "2024", "(WHO, 2024)")], refs)

    assert len(result.linked) == 1
    assert result.ambiguous == []


def test_a_genuinely_absent_reference_is_still_an_orphan_not_ambiguous():
    """A coauthor-only match is ambiguous; no match at all stays an orphan."""
    refs = [_ref(1, ["Chen, H."], 2026)]

    result = cross_match([_cite(["Nobody"], "2026", "(Nobody, 2026)")], refs)

    assert len(result.orphans) == 1
    assert result.ambiguous == []


def test_every_link_is_unique_so_claim_support_cannot_run_on_an_ambiguous_source():
    """BUG-239 AC4, locked as an invariant rather than left implicit.

    `verify.py` builds its `ClaimSupportInput` pairs from `cross.linked` only.
    That is safe exactly as long as `linked` never carries more than one
    reference per citation; the moment it does, a claim would be graded
    against a source the instructor never confirmed.
    """
    refs = [
        _ref(1, ["Chen, H.", "Han, Z."], 2026),
        _ref(6, ["Ferrer, R.", "Chen, Z."], 2026),
        _ref(3, ["Dela Cruz, J."], 2025),
        _ref(9, ["World Health Organization"], 2024),
    ]
    cites = [
        _cite(["Chen"], "2026", "Chen et al. (2026)"),
        _cite(["Schneider"], "2026", "(Schneider, 2026)"),
        _cite(["Dela Cruz"], "2025", "Dela Cruz (2025)"),
        _cite(["World Health Organization"], "2024", "(WHO, 2024)"),
        _cite(["Nobody"], "2030", "(Nobody, 2030)"),
    ]

    result = cross_match(cites, refs)

    for cite, matched in result.linked:
        assert len(matched) == 1, (
            f"{cite.raw_text!r} linked {len(matched)} references; claim support "
            "would grade its claim against a source nobody confirmed"
        )
    linked_cites = {id(c) for c, _ in result.linked}
    assert not any(id(c) in linked_cites for c in result.ambiguous)


# --- BUG-240 AC1: a claim cut at a PDF line break ----------------------------


def test_a_claim_wrapped_across_two_pdf_lines_is_reconstructed():
    """The ticket's sixth fragment, `Haan (2025), with`, is a half-line.

    A PDF text block is a physical line, not a sentence, so scanning blocks
    one at a time truncated the proposition at the line break and sent the
    remainder to claim-support grading.
    """
    blocks = [
        _block("Recent work reported a gain of twelve points by"),
        _block("Haan (2025), with no loss of precision."),
    ]

    cites = extract_in_text_citations(blocks)

    assert cites, "the citation must still be found after merging"
    assert cites[0].claim_sentence == (
        "Recent work reported a gain of twelve points by Haan (2025), with no loss of precision."
    )


def test_two_finished_sentences_are_never_glued_together():
    """Control: merging must not invent a proposition that was not written."""
    merged = merge_wrapped_lines([_block("A full sentence."), _block("Another one.")])

    assert [b.text for b in merged] == ["A full sentence.", "Another one."]


def test_a_line_break_across_pages_is_not_merged():
    """Anchor safety: a merged block must have one unambiguous location."""
    merged = merge_wrapped_lines([_block("cut here by", page=1), _block("Haan (2025).", page=2)])

    assert len(merged) == 2
    assert [b.page for b in merged] == [1, 2]


def test_a_line_break_across_paragraphs_is_not_merged():
    merged = merge_wrapped_lines(
        [
            _block("cut here by", page=None, paragraph=3),
            _block("Haan (2025).", page=None, paragraph=4),
        ]
    )

    assert len(merged) == 2


def test_a_line_ending_in_an_abbreviation_continues():
    """`et al.` is not a terminator, so the wrap still merges (AC1 + AC2)."""
    merged = merge_wrapped_lines(
        [_block("as reported by Chen et al."), _block("(2024) in a later study.")]
    )

    assert [b.text for b in merged] == ["as reported by Chen et al. (2024) in a later study."]


def test_merging_preserves_the_anchor_of_the_line_the_claim_starts_on():
    blocks = [_block("Recent work reported a gain by", page=7), _block("Haan (2025).", page=7)]

    cites = extract_in_text_citations(blocks)

    assert cites
    assert cites[0].page == 7
    assert cites[0].anchor == "p. 7"


def test_a_word_hyphenated_across_lines_rejoins_without_a_space():
    """BUG-240 AC4, hyphenated wrap.

    Justification breaks a word at a hyphen. Joining with a space put one
    inside the word, so the graded claim read "A neuro- symbolic solver" and
    the instructor's excerpt was unfindable in the PDF.
    """
    merged = merge_wrapped_lines(
        [_block("A neuro-"), _block("symbolic solver was proposed by Tan (2026).")]
    )

    assert len(merged) == 1
    assert merged[0].text == "A neuro-symbolic solver was proposed by Tan (2026)."


def test_a_typographic_hyphen_break_keeps_its_hyphen():
    """ "experi-" + "mental" is really "experimental", and we keep the hyphen.

    The excerpt has to be findable in the source, where the hyphen is what is
    printed; dissolving it would invent a spelling that appears nowhere on
    the page. Documented as a readability wart, not a correctness claim.
    """
    merged = merge_wrapped_lines(
        [_block("The experi-"), _block("mental group improved per Chen (2024).")]
    )

    assert merged[0].text == "The experi-mental group improved per Chen (2024)."


def test_a_dash_used_as_punctuation_still_joins_with_a_space():
    """Control: only a hyphen between two letters is a word break."""
    merged = merge_wrapped_lines([_block("the result -"), _block("was unclear per Chen (2024).")])

    assert merged[0].text == "the result - was unclear per Chen (2024)."


def test_a_hyphenated_claim_reaches_grading_as_one_word():
    """End to end: the reconstructed claim carries the rejoined compound."""
    cites = extract_in_text_citations(
        [_block("A neuro-"), _block("symbolic solver was proposed by Tan (2026).")]
    )

    assert cites
    assert "neuro-symbolic" in cites[0].claim_sentence
    assert "neuro- symbolic" not in cites[0].claim_sentence
