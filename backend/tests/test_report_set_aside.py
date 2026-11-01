"""What the originality check decided not to raise reaches the report as
counts by reason and standard names, read from its own saved detail."""

import pytest

from app.report.schemas import OriginalityScopeOut
from app.report.service import set_aside_counts, standard_names


def test_saved_counts_and_names_reach_the_report_by_reason():
    detail = {
        "n_similar_without_shared_wording": 3,
        "n_sections_evidence_below_passage_bar": 1,
        "n_shared_wording_only_in_cited_quotations": 2,
        "n_shared_wording_only_in_standard_texts": 2,
        "standard_texts_set_aside": [
            "ISO/IEC 25010 usability definition",
            "Approval sheet statement",
        ],
    }
    assert set_aside_counts(detail) == {
        "n_set_aside_no_shared_wording": 3,
        "n_set_aside_weak_section_evidence": 1,
        "n_set_aside_cited_quotation": 2,
        "n_set_aside_standard_text": 2,
        "standard_texts_set_aside": [
            "Approval sheet statement",
            "ISO/IEC 25010 usability definition",
        ],
    }


def test_a_run_that_did_not_record_a_reason_shows_unknown_not_zero():
    counts = set_aside_counts({"n_similar_without_shared_wording": 0})
    assert counts["n_set_aside_no_shared_wording"] == 0
    assert counts["n_set_aside_cited_quotation"] is None
    assert counts["n_set_aside_standard_text"] is None
    assert counts["standard_texts_set_aside"] == []


@pytest.mark.parametrize("bad", [-1, True, 2.5, "3", None, [1]])
def test_a_badly_saved_count_is_unknown(bad):
    assert (
        set_aside_counts({"n_shared_wording_only_in_cited_quotations": bad})[
            "n_set_aside_cited_quotation"
        ]
        is None
    )


@pytest.mark.parametrize(
    "value, expected",
    [
        (["B", "A", "B"], ["A", "B"]),
        (["A", 5, None, " ", {"x": 1}], ["A"]),
        ("ISO name as a bare string", []),
        (None, []),
    ],
)
def test_standard_names_are_strings_only_each_once(value, expected):
    assert standard_names(value) == expected


def test_the_scope_carries_the_counts_and_older_scopes_still_validate():
    scope = OriginalityScopeOut(
        compared_count=4,
        corpus="library",
        **set_aside_counts({"n_similar_without_shared_wording": 2}),
    )
    assert scope.n_set_aside_no_shared_wording == 2
    older = OriginalityScopeOut(compared_count=4, corpus="library")
    assert older.n_set_aside_standard_text is None and older.standard_texts_set_aside == []
