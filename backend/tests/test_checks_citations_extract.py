"""V-027 tests: in-text citation extraction + cross-match.

Synthetic APA fixtures, same convention as V-006's
`test_ingest_references.py::WELL_FORMED` — generic shapes, not real papers.
The owner's real proposal PDF hand-verification (ticket AC #1) is recorded
separately in the ticket file (local-only per D-007; the PDF and its
extracted text aren't committed).
"""

from dataclasses import dataclass

from app.checks.citations.extract import (
    ORPHAN_WORDING,
    UNCITED_WORDING,
    cross_match,
    extract_in_text_citations,
    orphan_flags,
    uncited_flags,
)
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


def test_secondary_citation_parenthetical_is_excluded_from_matching():
    blocks = [_block("An earlier claim (Rabinowitz, 1989, as cited in Colman, 2019) still holds.")]
    out = extract_in_text_citations(blocks)
    assert len(out) == 1
    assert out[0].is_secondary
    # A reference list containing NEITHER name still produces zero orphans,
    # because secondary citations are never resolved (edge case, ticket).
    result = cross_match(out, [])
    assert result.orphans == []
    assert result.linked == []


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
