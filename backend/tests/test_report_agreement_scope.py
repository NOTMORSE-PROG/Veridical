"""BUG-617: the agreement module says what it covered, never how many pairs
"agree", in the PDF export as on the screen."""

from types import SimpleNamespace

import pytest

from app.report.agreement_scope import outcome_lines, scope_paragraphs, set_aside_lines
from app.report.export import _agreement_scope_flowables, _styles
from app.report.schemas import AgreementScopeOut, IntegrityModuleOut, UndeterminedPairsOut

STYLES = _styles("Helvetica")

SCOPE_44 = AgreementScopeOut(
    n_objectives=17,
    n_results=34,
    results_section_identified=True,
    n_results_set_aside=3,
    n_results_too_short=2,
    set_aside_by_role=[{"role": "literature", "count": 3}],
    set_aside=[
        {
            "text": "Smith found that users preferred it.",
            "page_anchor": "p. 20",
            "role": "literature",
        },
        {"text": "Cruz reported higher accuracy.", "page_anchor": "p. 22", "role": "literature"},
        {"text": "Lee showed a faster response.", "page_anchor": None, "role": "literature"},
    ],
    absence_leads_withheld=True,
)


def _flag(kind="agreement_contradictory", check_kind="internal_agreement"):
    return SimpleNamespace(check_kind=check_kind, problem_kind=kind)


def _render(
    scope=SCOPE_44, *, state="partial", undetermined=None, flags=(), legacy=(), status=None
):
    module = IntegrityModuleOut(
        check_kind="internal_agreement",
        state=state,
        undetermined=undetermined,
        agreement_scope=scope,
    )
    report = SimpleNamespace(integrity_check_status=[status] if status else [])
    items = _agreement_scope_flowables(report, module, list(flags), list(legacy), STYLES)
    return [item.getPlainText() for item in items]


def test_report_44_says_what_was_covered_and_no_agreeing_count():
    texts = _render(undetermined=UndeterminedPairsOut(count=0, compared_count=31, pairs=[]))
    whole = " ".join(texts)
    assert texts[0] == "What this check compared"
    assert "Found 17 possible objectives and 34 possible results." in whole
    assert (
        "3 results from the literature review were set aside, because that part of a paper "
        "usually describes other studies." in whole
    )
    assert "2 results were set aside as too short to compare." in whole
    assert "The comparison found no possible inconsistency among the pairs it compared." in whole
    assert "so these comparisons do not change readiness" in whole
    assert "An objective with no matching result is not reported here." in whole
    # The set-aside statements are printed in full, with their places.
    assert "1. Literature review, p. 20: “Smith found that users preferred it.”" in whole
    assert "3. Literature review: “Lee showed a faster response.”" in whole
    # Never a count of agreeing or compared pairs.
    for word in ("consistent", "agree with", "31"):
        assert word not in whole.replace("possible inconsistency", "")


def test_a_run_without_a_scope_adds_nothing():
    assert _render(scope=None) == []


def test_findings_and_undecided_pairs_are_named_where_they_are_listed():
    texts = _render(
        undetermined=UndeterminedPairsOut(count=0, compared_count=10, pairs=[]),
        flags=[_flag(), _flag(), _flag(check_kind="citation_integrity")],
        legacy=[SimpleNamespace()],
    )
    whole = " ".join(texts)
    assert (
        "The comparison found 2 possible inconsistencies. They are listed under Findings." in whole
    )
    assert "1 other pair could not be decided. It is listed under Pairs to read yourself." in whole


def test_an_interrupted_run_says_so():
    status = SimpleNamespace(
        check_kind="internal_agreement",
        n_skipped_quota=2,
        n_skipped_api_down=0,
        n_skipped_parse_failure=0,
    )
    whole = " ".join(_render(status=status))
    assert "The check was interrupted before it compared every pair." in whole


def _scope(**fields):
    return AgreementScopeOut.model_validate({**SCOPE_44.model_dump(), **fields})


@pytest.mark.parametrize(
    "scope, findings, undecided, compared, expected",
    [
        (
            _scope(),
            0,
            3,
            10,
            [
                "The comparison found no possible inconsistency among the pairs it could decide.",
                "3 pairs could not be decided. They are listed under Pairs to read yourself.",
            ],
        ),
        (
            _scope(),
            0,
            1,
            1,
            [
                "The comparison could not decide on the one pair it compared, so it makes no "
                "finding. The pair is listed under Pairs to read yourself."
            ],
        ),
        (
            _scope(n_objectives=0),
            0,
            0,
            0,
            [
                "No objectives were found, so nothing was compared and the check makes no finding.",
                "This does not mean the objectives and results agree.",
            ],
        ),
        (
            _scope(n_results=5, n_results_set_aside=3, n_results_too_short=2),
            0,
            0,
            0,
            [
                "No results were left to compare, so the check makes no finding.",
                "This does not mean the objectives and results agree.",
            ],
        ),
        (
            _scope(),
            0,
            0,
            0,
            [
                "No result was close enough to an objective to compare, so the check makes no "
                "finding.",
                "This does not mean the objectives and results agree.",
            ],
        ),
        (_scope(), 0, 0, None, []),
    ],
)
def test_outcome_wording(scope, findings, undecided, compared, expected):
    assert (
        outcome_lines(scope, findings=findings, undecided=undecided, compared=compared) == expected
    )


def test_set_aside_sentences_name_each_part_and_fold_the_rest():
    scope = _scope(
        set_aside_by_role=[
            {"role": "literature", "count": 3},
            {"role": "methodology", "count": 1},
            {"role": "appendix", "count": 2},
            {"role": "mystery", "count": 1},
        ],
        n_results_too_short=1,
    )
    assert set_aside_lines(scope) == [
        "3 results from the literature review were set aside, because that part of a paper "
        "usually describes other studies.",
        "1 result from the methodology was set aside, because that part of a paper usually "
        "describes how the study was done.",
        "3 more were set aside from other parts of the paper.",
        "1 result was set aside as too short to compare.",
    ]
    assert set_aside_lines(
        _scope(set_aside_by_role=[{"role": "mystery", "count": 1}], n_results_too_short=0)
    ) == [
        "1 result from another part of the paper was set aside, because that part does not "
        "usually report the study's own results."
    ]


def test_results_section_not_found_replaces_the_set_aside_lines():
    lines = scope_paragraphs(
        _scope(results_section_identified=False),
        findings=0,
        undecided=0,
        compared=4,
        interrupted=False,
    )
    assert any(line.startswith("The results section could not be found") for line in lines)
    assert not any("literature review" in line for line in lines)


def test_an_older_run_says_its_set_aside_record_is_missing():
    lines = scope_paragraphs(
        _scope(results_section_identified=None),
        findings=0,
        undecided=0,
        compared=4,
        interrupted=False,
    )
    assert lines[-1] == "This report was made before VERIDICAL recorded which results it set aside."
