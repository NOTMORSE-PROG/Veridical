"""Milestone 2 Core Engine Stabilization Test Suite.

Verifies:
- Reference list ingestion with n.d. dates and author parsing before (n.d.)
- In-text citation extraction with n.d. dates and corporate authors with "and"
- Cross-matching with acronym/initialism aliases (WHO, IEEE, ACM, CHED, DOST, etc.)
- Numbered citations ([1], [1]-[3]) matching numbered references
- DataCite DOI resolution and Crossref fallback
- DOCX pipeline parity for table caption handling and format checking
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from app.checks.citations.cross_match import (
    cross_match,
    parse_numeric_citation_numbers,
)
from app.checks.citations.extract import (
    _split_authors,
    extract_in_text_citations,
)
from app.checks.citations.verify import _lookup_doi_chain
from app.checks.rules import RuleContext, get_rule
from app.checks.rules.tables import RULE_ID as TABLE_FORMAT_RULE_ID
from app.config import get_settings
from app.errors import ConflictError
from app.external import datacite as datacite_client
from app.external.schemas import ExternalWorkRecord, VerificationResult
from app.ingest.references import parse_reference
from app.ingest.schemas import SectionTree, TableBlock, TextBlock
from app.models.enums import CitationParseStatus, ResultOutcome
from app.models.manuscript import Manuscript
from app.report.service import manuscript_paragraphs_for


@dataclass
class FakeRef:
    order_index: int
    raw_text: str
    authors: list[str] | None
    year: int | str | None


@dataclass(frozen=True)
class FakeInText:
    surnames: list[str]
    year: str
    is_narrative: bool = False
    is_secondary: bool = False
    raw_text: str = ""
    anchor: str = "p. 1"
    page: int | None = 1
    paragraph: int | None = 1
    claim_sentence: str = ""
    claim_excerpt_cropped: bool = False


@dataclass(frozen=True)
class FakeCriterion:
    text: str
    evidence: str | None = None


def _block(text: str, paragraph: int = 1) -> TextBlock:
    return TextBlock(page=1, paragraph=paragraph, text=text, max_font_size=11, bold_ratio=0)


# --- 1. Reference List Ingestion with n.d. dates ---


def test_reference_ingest_nd_date_corporate_author():
    raw = "World Health Organization. (n.d.). Mental health in emergencies. WHO. https://who.int/mh"
    draft = parse_reference(raw, 0)
    assert draft.parse_status == CitationParseStatus.parsed
    assert draft.year == "n.d."
    assert draft.authors == ["World Health Organization"]
    assert draft.title == "Mental health in emergencies"
    assert draft.venue == "WHO"


def test_reference_ingest_nd_date_personal_author():
    raw = "Smith, J., & Doe, A. (n.d.). Machine learning algorithms. Academic Press."
    draft = parse_reference(raw, 1)
    assert draft.parse_status == CitationParseStatus.parsed
    assert draft.year == "n.d."
    assert draft.authors == ["Smith, J.", "Doe, A."]
    assert draft.title == "Machine learning algorithms"


# --- 2. In-Text Citation Extraction with n.d. dates ---


def test_in_text_parenthetical_and_narrative_nd_date():
    text = (
        "Guidelines exist (World Health Organization, n.d.). "
        "FAO (n.d.) provides recommendations."
    )
    cites = extract_in_text_citations([_block(text)])
    assert len(cites) == 2
    assert cites[0].year == "n.d."
    assert cites[0].surnames == ["World Health Organization"]
    assert cites[0].is_narrative is False

    assert cites[1].year == "n.d."
    assert cites[1].surnames == ["FAO"]
    assert cites[1].is_narrative is True


# --- 3. Corporate Author "and" Preservation ---


def test_corporate_author_names_with_and_not_split():
    assert _split_authors("Food and Agriculture Organization") == [
        "Food and Agriculture Organization"
    ]
    assert _split_authors("Department of Science and Technology") == [
        "Department of Science and Technology"
    ]
    assert _split_authors("National Economic and Development Authority") == [
        "National Economic and Development Authority"
    ]
    assert _split_authors("Commission on Higher Education") == [
        "Commission on Higher Education"
    ]
    # Personal authors should still split on and
    assert _split_authors("Smith and Jones") == ["Smith", "Jones"]
    assert _split_authors("Cruz, Santos, and Gomez") == ["Cruz", "Santos", "Gomez"]
    # Mixed author list: person and corporate author
    assert _split_authors("Santos, and Food and Agriculture Organization") == [
        "Santos",
        "Food and Agriculture Organization",
    ]


# --- 4. Acronym / Initialism Alias Matching in Cross-Match ---


def test_cross_match_acronym_who():
    ref = FakeRef(
        0,
        "World Health Organization. (2024). Guidelines.",
        ["World Health Organization"],
        2024,
    )
    cite = FakeInText(surnames=["WHO"], year="2024", raw_text="(WHO, 2024)")
    result = cross_match([cite], [ref])
    assert len(result.linked) == 1
    assert result.orphans == []
    assert result.uncited == []


def test_cross_match_acronym_reverse():
    ref = FakeRef(0, "WHO. (2024). Health report.", ["WHO"], 2024)
    cite = FakeInText(
        surnames=["World Health Organization"],
        year="2024",
        raw_text="(World Health Organization, 2024)",
    )
    result = cross_match([cite], [ref])
    assert len(result.linked) == 1
    assert result.orphans == []


def test_cross_match_acronym_ieee_acm_ched_dost():
    test_cases = [
        ("IEEE", "Institute of Electrical and Electronics Engineers"),
        ("ACM", "Association for Computing Machinery"),
        ("CHED", "Commission on Higher Education"),
        ("DOST", "Department of Science and Technology"),
        ("FAO", "Food and Agriculture Organization"),
    ]
    for idx, (acronym, full_name) in enumerate(test_cases):
        ref = FakeRef(idx, f"{full_name}. (2023). Publication.", [full_name], 2023)
        cite = FakeInText(surnames=[acronym], year="2023", raw_text=f"({acronym}, 2023)")
        result = cross_match([cite], [ref])
        assert len(result.linked) == 1, f"Failed linking {acronym} <-> {full_name}"


def test_cross_match_nd_date_linking():
    ref = FakeRef(
        0,
        "Commission on Higher Education. (n.d.). Policies.",
        ["Commission on Higher Education"],
        "n.d.",
    )
    cite = FakeInText(surnames=["CHED"], year="n.d.", raw_text="(CHED, n.d.)")
    result = cross_match([cite], [ref])
    assert len(result.linked) == 1
    assert result.orphans == []
    assert result.uncited == []


# --- 5. Numbered Citations Matching in Cross-Match ---


def test_parse_numeric_citation_numbers():
    cite1 = FakeInText(surnames=[], year="", raw_text="[1]")
    assert parse_numeric_citation_numbers(cite1) == [1]

    cite_range = FakeInText(surnames=[], year="", raw_text="[1]-[3]")
    assert parse_numeric_citation_numbers(cite_range) == [1, 2, 3]

    cite_list = FakeInText(surnames=[], year="", raw_text="[1, 3]")
    assert parse_numeric_citation_numbers(cite_list) == [1, 3]

    # Author-year citations must return None
    cite_author = FakeInText(surnames=["Smith"], year="2024", raw_text="(Smith, 2024)")
    assert parse_numeric_citation_numbers(cite_author) is None


def test_cross_match_numbered_single_and_range():
    ref1 = FakeRef(0, "[1] Smith, J. (2020). First study.", None, 2020)
    ref2 = FakeRef(1, "[2] Doe, A. (2021). Second study.", None, 2021)
    ref3 = FakeRef(2, "[3] Lee, C. (2022). Third study.", None, 2022)
    refs = [ref1, ref2, ref3]

    cite_range = FakeInText(surnames=["[1]-[3]"], year="", raw_text="[1]-[3]")
    result = cross_match([cite_range], refs)
    assert len(result.linked) == 1
    assert result.linked[0][1] == refs
    assert result.uncited == []
    assert result.orphans == []


def test_cross_match_numbered_orphan():
    ref1 = FakeRef(0, "[1] Smith, J. (2020). Title.", None, 2020)
    cite_missing = FakeInText(surnames=["[5]"], year="", raw_text="[5]")
    result = cross_match([cite_missing], [ref1])
    assert len(result.orphans) == 1
    assert result.linked == []
    assert result.uncited == [ref1]


# --- 6. DataCite DOI Resolution and Crossref Fallback ---


@pytest.mark.asyncio
async def test_datacite_lookup_doi_success():
    client = AsyncMock()
    settings = get_settings()
    mock_payload = {
        "data": {
            "id": "10.5281/zenodo.123456",
            "type": "dois",
            "attributes": {
                "doi": "10.5281/zenodo.123456",
                "titles": [{"title": "Open Source Capstone Dataset"}],
                "url": "https://zenodo.org/records/123456",
            },
        }
    }
    with patch("app.external.datacite.get_json", AsyncMock(return_value=mock_payload)):
        result = await datacite_client.lookup_doi(client, "10.5281/zenodo.123456", settings)
        assert isinstance(result, ExternalWorkRecord)
        assert result.found is True
        assert result.provider == "datacite"
        assert result.title == "Open Source Capstone Dataset"
        assert result.url == "https://zenodo.org/records/123456"
        assert result.content_checkable is False


@pytest.mark.asyncio
async def test_datacite_lookup_doi_not_found():
    client = AsyncMock()
    settings = get_settings()
    with patch("app.external.datacite.get_json", AsyncMock(return_value=None)):
        result = await datacite_client.lookup_doi(client, "10.5281/zenodo.999999", settings)
        assert result.found is False
        assert result.provider == "datacite"


@pytest.mark.asyncio
async def test_doi_verification_fallback_to_datacite():
    client = AsyncMock()
    settings = get_settings()

    crossref_404 = VerificationResult(found=False, provider="crossref")
    datacite_hit = VerificationResult(
        found=True,
        provider="datacite",
        title="Zenodo Research Artifact",
        url="https://zenodo.org/records/123456",
    )

    with (
        patch("app.external.crossref.lookup_doi", AsyncMock(return_value=crossref_404)),
        patch("app.external.datacite.lookup_doi", AsyncMock(return_value=datacite_hit)),
    ):
        result = await _lookup_doi_chain(client, "10.5281/zenodo.123456", settings)
        assert result.found is True
        assert result.provider == "datacite"
        assert result.title == "Zenodo Research Artifact"
        assert "crossref" in result.attempted_providers
        assert "datacite" in result.attempted_providers


# --- 7. DOCX Pipeline Parity: Table Caption Rule & Format Check ---


def test_docx_table_caption_rule_graceful_handling():
    spec = get_rule(TABLE_FORMAT_RULE_ID)
    criterion = FakeCriterion(text="Tables must be properly formatted with captions")
    # Native DOCX table with extracted caption
    tables = [
        TableBlock(
            paragraph=4,
            rows=[["header", "val"], ["1", "2"]],
            caption="Table 1: System Parameters",
        )
    ]
    ctx = RuleContext(
        manuscript_id=1,
        anchor_kind="paragraph",
        page_count=0,
        section_tree=SectionTree(source="styles", root=[]),
        geometry=[],
        tables=tables,
        vision_status="none",
        has_images=False,
        citations=[],
        margin_tolerance_pts=5.0,
        citation_style_min_ratio=0.8,
        table_caption_min_ratio=0.8,
    )
    outcome = spec.run(criterion, ctx)
    assert outcome.outcome == ResultOutcome.passed
    assert outcome.detail["captioned"] == 1
    assert outcome.detail["total_tables"] == 1

    # Uncaptioned table honestly fails the caption requirement
    uncaptioned_ctx = RuleContext(
        manuscript_id=1,
        anchor_kind="paragraph",
        page_count=0,
        section_tree=SectionTree(source="styles", root=[]),
        geometry=[],
        tables=[TableBlock(paragraph=4, rows=[["1", "2"]], caption=None)],
        vision_status="none",
        has_images=False,
        citations=[],
        margin_tolerance_pts=5.0,
        citation_style_min_ratio=0.8,
        table_caption_min_ratio=0.8,
    )
    fail_outcome = spec.run(criterion, uncaptioned_ctx)
    assert fail_outcome.outcome == ResultOutcome.failed
    reason = fail_outcome.detail["reason"]
    assert "Fewer than the expected share of tables carry a caption" in reason


def test_manuscript_file_format_property():
    m_docx = Manuscript(original_filename="thesis.docx", file_ref="uploads/test1.docx")
    assert m_docx.file_format == "docx"

    m_pdf = Manuscript(original_filename="thesis.pdf", file_ref="uploads/test2.pdf")
    assert m_pdf.file_format == "pdf"

    # Tricky filenames (reviewer_m2_2 challenge scenarios)
    m_tricky_pdf = Manuscript(original_filename="analysis.docx.pdf")
    assert m_tricky_pdf.file_format == "pdf"

    m_ext_free = Manuscript(original_filename="thesis", file_ref="uploads/draft.docx")
    assert m_ext_free.file_format == "docx"

    m_tree_docx = Manuscript(
        original_filename=None, file_ref=None, section_tree={"source": "styles"}
    )
    assert m_tree_docx.file_format == "docx"

    m_tree_pdf = Manuscript(
        original_filename=None, file_ref=None, section_tree={"source": "embedded_toc"}
    )
    assert m_tree_pdf.file_format == "pdf"


def test_manuscript_paragraphs_for_format_check():
    settings = get_settings()
    m_pdf = Manuscript(original_filename="thesis.pdf", file_ref="uploads/thesis.pdf")
    with pytest.raises(ConflictError, match="This manuscript's source file isn't a DOCX."):
        manuscript_paragraphs_for(m_pdf, settings)


# --- 8. Adversarial Challenger Edge Cases (Spaced n. d., Dotted Acronyms, DataCite Resilience) ---


def test_in_text_and_reference_spaced_nd_date():
    text = (
        "Guidelines are provided (World Health Organization, n. d.). "
        "FAO (n. d.) provides recommendations."
    )
    cites = extract_in_text_citations([_block(text)])
    assert len(cites) == 2
    assert cites[0].year == "n.d."
    assert cites[0].surnames == ["World Health Organization"]
    assert cites[1].year == "n.d."
    assert cites[1].surnames == ["FAO"]

    # Ingest reference with spaced n. d.
    ref = parse_reference(
        "World Health Organization. (n. d.). Pandemic preparedness. Geneva: WHO.",
        0,
    )
    assert ref.year == "n.d."
    assert ref.parse_status == CitationParseStatus.parsed

    # Cross-match linking
    matched = cross_match(cites[:1], [ref])
    assert len(matched.linked) == 1
    assert matched.linked[0][1][0].order_index == ref.order_index


def test_dotted_acronym_cross_match():
    # In-text cite uses dotted initialism
    text = "Standard protocols are published (W.H.O., 2020)."
    cites = extract_in_text_citations([_block(text)])
    assert len(cites) == 1

    ref = parse_reference(
        "World Health Organization. (2020). Health technical guidance. Geneva: WHO.",
        0,
    )
    matched = cross_match(cites, [ref])
    assert len(matched.linked) == 1
    assert matched.linked[0][1][0].order_index == ref.order_index


@pytest.mark.asyncio
async def test_datacite_malformed_json_resilience():
    from unittest.mock import AsyncMock, MagicMock

    from app.errors import ApiDownError
    from app.external.datacite import lookup_doi

    # Test 1: empty data
    client = MagicMock()
    with patch("app.external.datacite.get_json", new_callable=AsyncMock) as mock_get_json:
        mock_get_json.return_value = {"data": "not a dict"}
        with pytest.raises(ApiDownError):
            await lookup_doi(client, "10.5281/zenodo.12345")

        # Test 2: malformed attributes
        mock_get_json.return_value = {"data": {"type": "dois", "attributes": None}}
        with pytest.raises(ApiDownError):
            await lookup_doi(client, "10.5281/zenodo.12345")

        # Test 3: empty titles
        mock_get_json.return_value = {
            "data": {"type": "dois", "attributes": {"doi": "10.5281/zenodo.12345", "titles": []}}
        }
        with pytest.raises(ApiDownError):
            await lookup_doi(client, "10.5281/zenodo.12345")

