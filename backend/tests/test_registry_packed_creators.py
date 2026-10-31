"""BUG-601: a registry that packs every author into one creator string.

DataCite's record for 10.5281/zenodo.13879960 (Celestin, 2024, cited on
LensMatch p. 157) has a single creator whose familyName is the whole author
line with footnote asterisks. Comparing that string with the cited surname
called a correct reference a possible metadata mismatch. The registry did not
say who the first author's surname is, so a mismatch is never claimed from a
packed line: it agrees when the cited surname is in the line's first name,
and is otherwise left unassessed.
"""

import json

import pytest

from app.checks.citations.metadata import compare_metadata, metadata_mismatches
from app.external.schemas import VerificationResult
from app.models.citation import Citation
from app.models.enums import CitationParseStatus

DOI = "10.5281/zenodo.13879960"
PACKED = "Mbonigaba Celestin*, S. Sujatha**, A. Dinesh Kumar*** & M. Vasuki****"
TITLE = (
    "EXPLORING THE IMPACT OF AR AND VR ON ENHANCING CUSTOMER EXPERIENCES AND DRIVING SALES "
    "IN RETAIL"
)


def cited(authors=("Celestin, M.",)):
    return Citation(
        manuscript_id=1,
        order_index=2,
        raw_text=(
            "Celestin, M. (2024). Exploring the impact of AR and VR on enhancing customer "
            "experiences and driving sales in retail. *Zenodo, 9*(2). https://doi.org/" + DOI
        ),
        authors=list(authors),
        year=2024,
        title=(
            "Exploring the impact of AR and VR on enhancing customer experiences and driving "
            "sales in retail"
        ),
        doi=DOI,
        parse_status=CitationParseStatus.parsed,
    )


def datacite(creators):
    """The shape DataCite's REST API returned for this DOI on 2026-10-31."""
    return VerificationResult(
        found=True,
        provider="datacite",
        title=TITLE,
        raw={
            "data": {
                "attributes": {
                    "doi": DOI,
                    "publicationYear": 2024,
                    "titles": [{"title": TITLE}],
                    "creators": creators,
                }
            }
        },
    )


def packed(line=PACKED):
    return [
        {
            "name": line,
            "nameType": "Personal",
            "familyName": line,
            "nameIdentifiers": [],
            "affiliation": [],
        }
    ]


def test_live_celestin_record_is_not_a_mismatch():
    comparison = compare_metadata(cited(), datacite(packed()))
    assert comparison.differing_fields == ()
    assert comparison.author_status == "agrees"
    assert comparison.registered["first_author"] == "Mbonigaba Celestin"


@pytest.mark.parametrize(
    "line",
    [
        "Celestin, M.; Sujatha, S.; Kumar, A. D.",
        "Mbonigaba Celestin and S. Sujatha",
        "Mbonigaba Celestin†, S. Sujatha‡",
        "Mbonigaba Celestin1, S. Sujatha2 & M. Vasuki3",
        "MBONIGABA CELESTIN AND S. SUJATHA",
        "Mbonigaba Celestin\u00b9, S. Sujatha\u00b2",
        "Celestin et al.",
    ],
)
def test_other_packed_spellings_agree_on_the_lead_name(line):
    comparison = compare_metadata(cited(), datacite(packed(line)))
    assert comparison.differing_fields == ()
    assert comparison.author_status == "agrees"


def test_cited_surname_later_in_a_packed_line_is_left_unassessed():
    comparison = compare_metadata(cited(("Sujatha, S.",)), datacite(packed()))
    assert "first author" not in comparison.differing_fields
    assert comparison.author_status == "unassessed"
    assert comparison.author_unassessed_reason == "registry_author_name_not_separated"


def test_cited_surname_absent_from_a_packed_line_is_left_unassessed():
    comparison = compare_metadata(cited(("Reyes, J.",)), datacite(packed()))
    assert comparison.differing_fields == ()
    assert comparison.author_status == "unassessed"


def test_footnote_marker_on_a_separate_family_name_is_ignored():
    creators = [{"familyName": "Celestin1", "givenName": "Mbonigaba"}]
    comparison = compare_metadata(cited(), datacite(creators))
    assert comparison.author_status == "agrees"
    assert comparison.registered["first_author"] == "Celestin"


def test_footnote_marker_on_a_name_that_contains_and_still_agrees():
    comparison = compare_metadata(
        cited(("Ruiz and Sons",)), datacite([{"familyName": "Ruiz and Sons1"}])
    )
    assert comparison.author_status == "agrees"
    assert comparison.registered["first_author"] == "Ruiz and Sons"


@pytest.mark.parametrize("name", ["Web3 Foundation", "G20 Research Group", "Studio71"])
def test_name_with_digits_that_matches_the_registry_exactly_still_agrees(name):
    comparison = compare_metadata(cited((name,)), datacite([{"familyName": name}]))
    assert comparison.author_status == "agrees"
    assert comparison.registered["first_author"] == name


@pytest.mark.parametrize(
    "name",
    ["Santos\u00b9", "Santos\u1d43", "Santos/Reyes", "Santos\uff0c Reyes", "Santos et al."],
)
def test_registry_string_that_is_not_one_plain_name_is_never_a_mismatch(name):
    comparison = compare_metadata(cited(), datacite([{"familyName": name}]))
    assert comparison.differing_fields == ()
    assert comparison.author_status == "unassessed"


def test_every_word_of_the_cited_surname_must_be_in_the_lead_name():
    line = "Juan Cruz, S. Sujatha & M. Vasuki"
    comparison = compare_metadata(cited(("Dela Cruz, J.",)), datacite(packed(line)))
    assert comparison.author_status == "unassessed"
    assert compare_metadata(cited(("Cruz, J.",)), datacite(packed(line))).author_status == "agrees"


def test_a_cited_author_with_no_surname_never_agrees_with_a_packed_line():
    comparison = compare_metadata(cited((", M.",)), datacite(packed()))
    assert comparison.author_status == "unassessed"


def test_a_real_different_first_author_is_still_reported():
    creators = [{"familyName": "Jones", "givenName": "Ann"}, {"familyName": "Celestin"}]
    comparison = compare_metadata(cited(), datacite(creators))
    assert comparison.differing_fields == ("first author",)
    assert comparison.author_status == "differs"


def test_corporate_name_containing_and_still_agrees_exactly():
    name = "Department of Trade and Industry"
    comparison = compare_metadata(cited((name,)), datacite([{"name": name, "familyName": name}]))
    assert comparison.author_status == "agrees"
    assert comparison.registered["first_author"] == name


def test_unrelated_name_with_a_separator_is_not_called_a_mismatch():
    # "Smith, Jr." or "Ruiz and Sons" cannot be cut into names reliably.
    for name in ("Reyes, Jr.", "Ruiz and Sons"):
        comparison = compare_metadata(cited(), datacite([{"familyName": name}]))
        assert comparison.differing_fields == ()
        assert comparison.author_status == "unassessed"


def test_crossref_packed_family_is_read_the_same_way():
    result = VerificationResult(
        found=True,
        provider="crossref",
        title=TITLE,
        raw={
            "DOI": DOI,
            "published": {"date-parts": [[2024]]},
            "author": [{"family": PACKED}],
        },
    )
    assert metadata_mismatches(cited(), result) == []


@pytest.mark.parametrize(
    "override",
    [
        {"registry_author_marker_pattern": "[a-z]"},
        {"registry_author_marker_pattern": "\\b"},
        {"registry_author_list_pattern": "\\s+"},
        {"registry_author_list_pattern": "x^"},
    ],
)
def test_registry_author_patterns_that_fail_their_known_answers_are_refused(tmp_path, override):
    from app.ingest.reference_metadata_patterns import load_reference_metadata_patterns

    path = tmp_path / "patterns.json"
    path.write_text(json.dumps(override), encoding="utf-8")
    with pytest.raises(ValueError):
        load_reference_metadata_patterns(path)
