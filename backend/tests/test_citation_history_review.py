"""F5/F8 historical guidance never repairs or certifies old evidence in place."""

import copy
import subprocess
import sys

import pytest

from app.checks.citations.history_review import citation_metadata_review_warning
from app.checks.citations.projection import project_quoted_metadata
from app.ingest.patterns import load_patterns
from tests.test_quoted_reference_projection import RAW, old_rows, source


def detail():
    return {
        "kind": "possible_metadata_mismatch",
        "metadata_comparison": {
            "differing_fields": ["title"],
            "cited": {"title": "A source title"},
        },
    }


def test_missing_projection_is_current_limitation_without_mutating_saved_record():
    saved = detail()
    before = copy.deepcopy(saved)
    assert (
        citation_metadata_review_warning("citation_integrity", RAW, saved)
        == "quoted_title_projection_unverified"
    )
    assert saved == before


def projected_detail():
    _, records = project_quoted_metadata(old_rows(source()), source(), load_patterns())
    return {**detail(), "source_projection": records[0]}


def test_valid_recorded_projection_does_not_receive_legacy_warning():
    assert citation_metadata_review_warning("citation_integrity", RAW, projected_detail()) is None


@pytest.mark.parametrize(
    "field,bad",
    [
        ("schema_version", "future-unrecognized"),
        ("status", []),
        ("status", "unassessed"),
        ("raw_text_sha256", "0" * 64),
        ("parser_config_sha256", False),
        ("source_inventory_sha256", "not-a-hash"),
        ("title_start", True),
        ("title_end", 99999),
        ("original_fields", []),
        ("effective_fields", {}),
        ("parser_version", ""),
    ],
)
def test_malformed_or_unbound_proof_cannot_remove_warning(field, bad):
    saved = projected_detail()
    saved["source_projection"][field] = bad
    assert (
        citation_metadata_review_warning("citation_integrity", RAW, saved)
        == "quoted_title_projection_unverified"
    )


def test_projection_for_different_saved_comparison_cannot_remove_warning():
    saved = projected_detail()
    saved["metadata_comparison"]["cited"]["title"] = "A different title"
    assert citation_metadata_review_warning("citation_integrity", RAW, saved)


def test_offsets_to_venue_text_cannot_certify_a_quoted_title():
    saved = projected_detail()
    wrong = "Proceedings of Examples"
    proof = saved["source_projection"]
    proof.update(title_start=RAW.index(wrong), title_end=RAW.index(wrong) + len(wrong))
    proof["effective_fields"]["title"] = wrong
    saved["metadata_comparison"]["cited"]["title"] = wrong
    assert citation_metadata_review_warning("citation_integrity", RAW, saved)


@pytest.mark.parametrize("field", ["authors", "year", "doi", "isbn", "url"])
@pytest.mark.parametrize("damage", ["missing", "malformed"])
def test_both_sides_of_malformed_proof_cannot_certify_by_matching(field, damage):
    saved = projected_detail()
    for name in ("original_fields", "effective_fields"):
        if damage == "missing":
            del saved["source_projection"][name][field]
        else:
            saved["source_projection"][name][field] = {"malformed": True}
    assert citation_metadata_review_warning("citation_integrity", RAW, saved)


@pytest.mark.parametrize(
    "raw",
    [
        'Smith, J. (2024). A study of "quoted text". Journal.',
        'Smith, J. (2024). A study of, "quoted text". Journal.',
        'Smith, J. (2024). A study (phase 1) "quoted text". Journal.',
        "O’Connor, A. (2024). An unquoted title. Journal.",
        "Smith, J. (2024). An ordinary title. Journal.",
    ],
)
def test_ordinary_or_inline_quotation_is_outside_leading_title_defect(raw):
    assert citation_metadata_review_warning("citation_integrity", raw, detail()) is None


def test_year_or_author_difference_is_not_labelled_a_title_failure():
    saved = detail()
    saved["metadata_comparison"]["differing_fields"] = ["year", "first author"]
    assert citation_metadata_review_warning("citation_integrity", RAW, saved) is None


@pytest.mark.parametrize("fields", [[], [None], [{}], ["typo"]])
def test_unknown_field_list_cannot_establish_that_title_was_excluded(fields):
    saved = detail()
    saved["metadata_comparison"]["differing_fields"] = fields
    assert citation_metadata_review_warning("citation_integrity", RAW, saved)


def test_conflicting_inner_and_outer_lists_do_not_hide_title_uncertainty():
    saved = detail()
    saved["metadata_comparison"]["differing_fields"] = ["year"]
    saved["differing_fields"] = ["title"]
    assert citation_metadata_review_warning("citation_integrity", RAW, saved)


def test_historical_quote_without_snapshot_also_has_parsing_evidence_limit():
    assert citation_metadata_review_warning(
        "citation_integrity", RAW, {"kind": "possible_metadata_mismatch"}
    )


def test_unrelated_check_never_receives_citation_warning():
    assert citation_metadata_review_warning("internal_agreement", RAW, detail()) is None


@pytest.mark.parametrize(
    "prefix",
    [
        "J. Smith",
        "J. Smith and A. Cruz",
        "J. Smith et al.",
        "J. Smith, A. Cruz, and B. Doe",
        "J. O\u2019Connor",
        "\u00c1. Smith",
        "Ma. Cruz",
        "J.-P. Smith",
    ],
)
def test_supported_ieee_author_prefix_receives_missing_proof_warning(prefix):
    assert citation_metadata_review_warning(
        "citation_integrity", f'{prefix}, "A source title," Journal, 2024.', detail()
    )


def test_malformed_initials_do_not_stall_saved_report_reads():
    # Run in a child so a renewed exponential regex cannot hang the suite.
    program = """
from app.checks.citations.history_review import citation_metadata_review_warning
detail = {'kind': 'possible_metadata_mismatch'}
for count in (32, 2730, 10922):
    raw = 'J. ' * count + '! "A source title," Journal, 2024.'
    assert citation_metadata_review_warning('citation_integrity', raw, detail) is None
raw = 'J.' + ' ' * 32768 + 'Smith, "A source title," Journal, 2024.'
assert citation_metadata_review_warning('citation_integrity', raw, detail)
"""
    subprocess.run([sys.executable, "-c", program], check=True, timeout=8, capture_output=True)
