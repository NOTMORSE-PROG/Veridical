"""BUG-517: citation findings sharing one sentence name the work they are about."""

import pytest

from app.flags.schemas import FlagOut
from app.report.cited_work import cited_work
from app.report.schemas import FlagSummaryOut


def test_an_orphan_citation_names_the_citation_as_written():
    detail = {
        "kind": "orphan_in_text_citation",
        "raw_text": "(Biggs & Tang, 2007)",
        "surnames": ["Biggs", "Tang"],
        "year": "2007",
    }
    assert cited_work(detail, "A whole sentence citing several works.") == {
        "cited_text": "(Biggs & Tang, 2007)",
        "cited_authors": ["Biggs", "Tang"],
        "cited_year": "2007",
    }


def test_an_uncited_reference_names_its_entry():
    detail = {"kind": "uncited_reference", "authors": ["Reyes, J."], "year": 2024}
    entry = "Reyes, J. (2024). Example source. Journal of Examples."
    assert cited_work(detail, entry) == {
        "cited_text": entry,
        "cited_authors": ["Reyes, J."],
        "cited_year": "2024",
    }


@pytest.mark.parametrize(
    "detail",
    [
        {"kind": "grim_inconsistent"},
        {},
        {"kind": "orphan_in_text_citation", "raw_text": " ", "surnames": "Biggs", "year": True},
    ],
)
def test_other_findings_and_damaged_rows_name_nothing(detail):
    assert cited_work(detail, "excerpt") == {
        "cited_text": None,
        "cited_authors": None,
        "cited_year": None,
    }


def test_older_saved_uncited_rows_still_name_their_entry():
    assert cited_work({"kind": "uncited_reference"}, "Entry text.")["cited_text"] == "Entry text."


def test_both_flag_outputs_carry_the_fields():
    for schema in (FlagSummaryOut, FlagOut):
        assert {"cited_text", "cited_authors", "cited_year"} <= set(schema.model_fields)
