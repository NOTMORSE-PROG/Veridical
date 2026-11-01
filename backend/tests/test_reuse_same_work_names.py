"""BUG-662: the name rule that tells another upload of the same work apart
from a different work, and the wording of the lead it produces."""

import json

import pytest

from app.checks.reuse.same_work import comparable_name, load_name_rules
from app.config import get_settings
from app.findings.policy import reuse_reason


@pytest.fixture()
def rules():
    return load_name_rules(get_settings())


@pytest.mark.parametrize(
    "first,second",
    [
        ("VERIDICAL-DOCUMENTATION.pdf", "VERIDICAL-DOCUMENTATION (1).pdf"),
        ("VERIDICAL-DOCUMENTATION.pdf", "veridical_documentation final v2.PDF"),
        ("Clinic Queue System.docx", "clinic-queue-system-revised.pdf"),
        ("C:\\Users\\team\\Clinic Queue.pdf", "clinic queue copy.pdf"),
    ],
)
def test_resaved_and_revised_file_names_compare_equal(rules, first, second):
    assert comparable_name(first, rules, file_name=True) is not None
    assert comparable_name(first, rules, file_name=True) == comparable_name(
        second, rules, file_name=True
    )


@pytest.mark.parametrize(
    "name", ["Thesis Final.pdf", "manuscript (2).docx", "Capstone Project v3.pdf", "", None]
)
def test_generic_or_missing_file_names_never_identify_a_work(rules, name):
    assert comparable_name(name, rules, file_name=True) is None


@pytest.mark.parametrize(
    "name",
    [
        "Group 3",
        "Team A",
        "group 12",
        "GROUP-4 (final)",
        "IT41S2-G3",
        "G3",
        "Pangkat 3",
        "Final Defense Manuscript",
        "CS Thesis",
        "Kabanata 1",
    ],
)
def test_generic_team_labels_and_titles_never_identify_a_work(rules, name):
    assert comparable_name(name, rules) is None


@pytest.mark.parametrize(
    "first,second",
    [
        ("Smart Queue Management System 2", "Smart Queue Management System"),
        ("Library System 2024", "Library System 2025"),
    ],
)
def test_titles_and_team_names_keep_their_numbers(rules, first, second):
    # Only file names lose re-save markers; a sequel or a later cohort's
    # project is a different work.
    assert comparable_name(first, rules) != comparable_name(second, rules)


def test_generic_file_names_never_identify_a_work_even_when_identical(rules):
    assert comparable_name("BSIT_Capstone_Manuscript.pdf", rules, file_name=True) is None


def test_different_works_do_not_compare_equal(rules):
    assert comparable_name("alpha-attendance.pdf", rules, file_name=True) != comparable_name(
        "beta-attendance.pdf", rules, file_name=True
    )


def test_name_rules_are_read_from_the_configured_file(tmp_path):
    custom = tmp_path / "names.json"
    custom.write_text(
        json.dumps(
            {
                "file_name_markers": ["\\s+\\d+"],
                "generic_words": ["veridical"],
                "generic_token_patterns": [],
            }
        ),
        encoding="utf-8",
    )
    settings = get_settings().model_copy(update={"reuse_same_work_names_file": custom})
    rules = load_name_rules(settings)
    assert comparable_name("Veridical 2.pdf", rules, file_name=True) is None
    # "final" is a marker only in the packaged file.
    assert comparable_name("Clinic final.pdf", rules, file_name=True) == "clinic final"


def test_same_work_lead_names_every_record_and_accuses_no_one():
    reason = reuse_reason(
        {
            "kind": "reuse_same_instructor_resubmission",
            "matched_manuscript_id": 3,
            "matched_manuscript_ids": [3, 9],
            "same_work": [
                {"manuscript_id": 3, "basis": "same_file", "similarity": 1.0},
                {"manuscript_id": 9, "basis": "same_file_name", "similarity": 0.9},
            ],
        }
    )
    assert "#3 and #9" in reason
    assert "#3: same file" in reason and "#9: similar text, same file name" in reason
    assert "your own Library records" in reason
    assert "earlier version" in reason
    assert "does not change readiness" in reason
    for accusing in ("plagiar", "copied", "copying", "fabricat", "cheat"):
        assert accusing not in reason.lower()


def test_an_earlier_resubmission_lead_still_reads_correctly():
    # Leads saved before BUG-662 carry only the one record id.
    reason = reuse_reason(
        {"kind": "reuse_same_instructor_resubmission", "matched_manuscript_id": 7}
    )
    assert "record #7 appears" in reason
    assert "()" not in reason
