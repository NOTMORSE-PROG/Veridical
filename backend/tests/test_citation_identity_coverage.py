"""F5: unsupported dates/styles cannot establish that a source is unused."""

import pytest

from app.checks.citations.extract import cross_match, extract_in_text_citations
from app.checks.citations.linkage_guard import guarded_absence_candidates
from app.config import Settings
from app.ingest.references import parse_reference
from app.ingest.schemas import TextBlock
from app.models.enums import CitationParseStatus


def block(text):
    return TextBlock(page=1, text=text, max_font_size=11, bold_ratio=0)


@pytest.mark.parametrize(
    "text",
    ["The package is useful (Met Office, n.d.).", "Met Office (n.d.) describes its package."],
)
def test_explicit_no_date_corporate_reference_links_without_inventing_year(text):
    reference = parse_reference(
        "Met Office. (n.d.). Cartographic tools. https://example.org/tool", 0
    )
    marks = extract_in_text_citations([block(text)])
    matched = cross_match(marks, [reference])
    assert reference.year in (None, "n.d.")
    assert reference.parse_status == CitationParseStatus.parsed
    assert reference.authors == ["Met Office"]
    assert len(marks) == 1 and len(matched.linked) == 1
    assert matched.confidently_uncited == []


def test_explicit_no_date_personal_et_al_reference_links():
    reference = parse_reference("May, J., & Lee, T. (n.d.). Weather software. Publisher.", 0)
    marks = extract_in_text_citations([block("May et al. (n.d.) describe the tool.")])
    assert len(cross_match(marks, [reference]).linked) == 1


def test_two_same_author_undated_sources_remain_ambiguous():
    references = [
        parse_reference(f"Met Office. (n.d.). {title}. Publisher.", i)
        for i, title in enumerate(("First tool", "Second tool"))
    ]
    matched = cross_match(
        extract_in_text_citations([block("Met Office (n.d.) describes a tool.")]), references
    )
    assert not matched.linked
    assert len(matched.possibly_cited) == 2
    assert not matched.confidently_uncited


def test_unknown_date_and_unparsed_reference_do_not_create_unused_warning():
    known = parse_reference("Smith, J. (2020). Known work. Publisher.", 0)
    unknown = parse_reference("An unreadable citation without a reliable date", 1)
    cross = cross_match(
        extract_in_text_citations([block("Smith (2020) supplies the method.")]), [known, unknown]
    )
    candidates, unassessed, leads = guarded_absence_candidates(
        cross.confidently_uncited, [known, unknown], [], Settings()
    )
    assert candidates == [] and unassessed == {1} and leads == []
    assert unknown.raw_text == "An unreadable citation without a reliable date"


@pytest.mark.parametrize(
    ("mention", "withheld"),
    [
        ("ISO/IEC 25010", True),
        ("ISO/IEC 250100", False),
        ("ISO/IEC 25010-2", False),
        ("ISO/IEC 25010:2011", True),
        ("ISO/IEC 25010:2023", False),
        ("ISO/IEC 25010:2023-2", False),
        ("ISO/IEC 25010:20231", False),
        ("ISO/IEC 25010:2023a", False),
        ("IEC 25010", False),
    ],
)
def test_standard_identity_is_only_a_bounded_possible_link(mention, withheld):
    standard = parse_reference("ISO/IEC. (2011). ISO/IEC 25010 quality model. Standards body.", 0)
    unused = parse_reference("Smith, J. (2020). An unrelated unused work. Publisher.", 1)
    candidates, unassessed, leads = guarded_absence_candidates(
        [standard, unused], [standard, unused], [block(mention)], Settings()
    )
    assert (standard not in candidates) is withheld
    assert unused in candidates
    assert (0 in unassessed) is withheld
    if withheld:
        assert leads[0]["status"] == "possible_identifier_link"
        assert leads[0]["page"] == 1


def test_identifier_only_in_reference_list_is_not_a_body_match():
    reference = parse_reference("ISO/IEC. (2011). ISO/IEC 25010 quality model. Standards body.", 0)
    candidates, unassessed, leads = guarded_absence_candidates(
        [reference], [reference], [block("No standard is named here.")], Settings()
    )
    assert candidates == [reference] and not unassessed and not leads


def test_supported_genuinely_unused_reference_remains_actionable():
    reference = parse_reference("Smith, J. (2020). Supported reference identity. Publisher.", 0)
    candidates, unassessed, _ = guarded_absence_candidates([reference], [reference], [], Settings())
    assert candidates == [reference] and not unassessed
