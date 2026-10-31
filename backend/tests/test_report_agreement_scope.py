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
    # The outcome comes first, with the compared count as coverage.
    assert texts[1] == (
        "Among the 31 pairs VERIDICAL matched, the comparison found no possible inconsistency."
    )
    assert "so this check does not change readiness." in whole
    assert "An objective with no matching result is not reported here." in whole
    assert (
        "About these pairs: VERIDICAL matches each objective with the results most like it."
        in whole
    )
    # The set-aside statements are printed in full, with their places.
    assert "1. Literature review, p. 20: “Smith found that users preferred it.”" in whole
    assert "3. Literature review: “Lee showed a faster response.”" in whole
    # Never a count of agreeing pairs.
    for word in ("consistent", "agree with", "agreed"):
        assert word not in whole.replace("possible inconsistency", "")


def test_fewer_matched_objectives_than_found_is_said():
    scope = SCOPE_44.model_copy(update={"n_objectives_matched": 9})
    assert (
        "Found 17 possible objectives and 34 possible results. 9 of the 17 objectives were "
        "matched to at least one result. One objective can be paired with more than one "
        "result." in " ".join(_render(scope=scope))
    )
    every = SCOPE_44.model_copy(update={"n_objectives_matched": 17})
    assert "matched to a result" not in " ".join(_render(scope=every))


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
        "Among the 10 pairs VERIDICAL matched, the comparison found 2 possible "
        "inconsistencies. They are listed under Findings." in whole
    )
    assert "1 other pair could not be decided. It is listed under Pairs to read yourself." in whole


def test_an_interrupted_run_says_so():
    skipped = SCOPE_44.model_copy(update={"n_pairs_skipped": 2})
    whole = " ".join(_render(scope=skipped))
    assert (
        "The check was interrupted before it compared every pair. Pairs it did not compare are "
        "not treated as passed." in whole
    )
    some = " ".join(
        _render(
            scope=skipped,
            undetermined=UndeterminedPairsOut(count=0, compared_count=5, pairs=[]),
        )
    )
    assert (
        "The check was interrupted. Among the 5 pairs it compared before then, the comparison "
        "found no possible inconsistency. Pairs it did not compare are not treated as passed."
        in some
    )
    assert some.count("interrupted") == 1
    # Every compared pair undecided: the outcome still names the interruption.
    undecided = " ".join(
        _render(
            scope=skipped,
            undetermined=UndeterminedPairsOut(count=2, compared_count=2, pairs=[]),
        )
    )
    assert (
        "The check was interrupted. The comparison could not decide on any of the 2 pairs it "
        "compared before then, so it makes no finding." in undecided
    )
    assert undecided.count("interrupted") == 1


def test_pairs_never_judged_are_not_read_as_nothing_close_enough():
    """Every pair skipped (AI down, a rejected key): the reason is the
    interruption, said once, never "no result was close enough"."""
    skipped = SCOPE_44.model_copy(update={"n_pairs_skipped": 6})
    texts = _render(
        scope=skipped, undetermined=UndeterminedPairsOut(count=0, compared_count=0, pairs=[])
    )
    whole = " ".join(texts)
    assert "The check was interrupted before it compared any pair, so it makes no finding." in whole
    assert "close enough" not in whole
    assert whole.count("interrupted") == 1


def test_an_injection_warning_is_not_counted_as_an_inconsistency():
    texts = _render(
        undetermined=UndeterminedPairsOut(count=0, compared_count=8, pairs=[]),
        flags=[_flag("agreement_injection_suspected")],
    )
    whole = " ".join(texts)
    assert (
        "Among the 8 pairs VERIDICAL matched, the comparison found no possible inconsistency."
        in whole
    )
    assert "1 possible inconsistency" not in whole


def test_an_early_exit_run_is_not_called_old_or_a_comparison():
    """The engine's early exit for no objectives and no results writes no
    results_sections; that is a run made today, not an older report."""
    empty = AgreementScopeOut(n_objectives=0, n_results=0, absence_leads_withheld=True)
    lines = scope_paragraphs(empty, findings=0, undecided=0, compared=0, interrupted=False)
    whole = " ".join(lines)
    assert "No objectives were found, so nothing was compared" in whole
    assert "made before" not in whole


def test_an_unknown_set_aside_count_gives_no_guessed_reason():
    unknown = AgreementScopeOut(
        n_objectives=4, n_results=3, n_results_set_aside=None, n_results_too_short=None
    )
    assert outcome_lines(unknown, findings=0, undecided=0, compared=0)[0] == (
        "No pair was compared, so the check makes no finding."
    )


def test_too_short_results_are_still_counted_when_no_results_section_was_found():
    lines = scope_paragraphs(
        _scope(
            results_section_identified=False,
            set_aside_by_role=[],
            set_aside=[],
            n_results_too_short=3,
        ),
        findings=0,
        undecided=0,
        compared=0,
        interrupted=False,
    )
    whole = " ".join(lines)
    assert "3 results were set aside as too short to compare." in whole
    assert "literature review" not in whole


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
                "Among the 10 pairs VERIDICAL matched, the comparison found no possible "
                "inconsistency in the pairs it could decide.",
                "3 pairs could not be decided. They are listed under Pairs to read yourself.",
            ],
        ),
        (
            _scope(),
            0,
            1,
            1,
            [
                "The comparison could not decide on the one pair VERIDICAL matched, so it makes "
                "no "
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
        (_scope(), 0, 0, None, ["This report did not record how many pairs were compared."]),
        (
            _scope(n_results=0),
            0,
            0,
            None,
            [
                "No results were left to compare, so the check makes no finding.",
                "This does not mean the objectives and results agree.",
            ],
        ),
        (
            _scope(),
            2,
            1,
            None,
            [
                "This report did not record how many pairs were compared.",
                "It found 2 possible inconsistencies, listed under Findings.",
                "1 pair could not be decided; it is listed under Pairs to read yourself.",
            ],
        ),
        (
            _scope(),
            0,
            4,
            4,
            [
                "The comparison could not decide on any of the 4 pairs VERIDICAL matched, so it "
                "makes no finding. The pairs are listed under Pairs to read yourself."
            ],
        ),
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


def test_the_no_findings_line_is_dropped_only_when_the_card_shows():
    from app.report.export import _agreement_scope_flowables as card

    assert _render()  # the card renders for a module with a scope
    module = IntegrityModuleOut(check_kind="internal_agreement", state="partial")
    assert card(SimpleNamespace(integrity_check_status=[]), module, [], [], STYLES) == []


def test_a_results_chapter_under_a_literature_heading_is_named_by_its_heading():
    from app.report.module_scope import _agreement_scope

    scope = _agreement_scope(
        {
            "n_intents": 2,
            "results_sections": {
                "results_section_identified": True,
                "n_results_set_aside": 1,
                "set_aside": [
                    {
                        "text": "Other work found X.",
                        "anchor": "p. 4",
                        "chapter_role": "results",
                        "heading_role": "literature",
                    }
                ],
            },
        }
    )
    assert scope.set_aside[0].role == "literature"


async def test_a_share_snapshot_never_carries_set_aside_statements(monkeypatch):
    """Through the real share path: the snapshot written to the audit log
    keeps the counts and drops each statement and its page."""
    from unittest.mock import AsyncMock

    from app.models.enums import CheckRunStatus
    from app.share import service as share

    report = SimpleNamespace(
        subject={},
        integrity_modules=[
            IntegrityModuleOut(
                check_kind="internal_agreement", state="partial", agreement_scope=SCOPE_44
            )
        ],
    )
    run = SimpleNamespace(id=7, status=CheckRunStatus.done, manuscript_id=3)
    session = SimpleNamespace(
        get=AsyncMock(side_effect=lambda model, key: run if key == 7 else SimpleNamespace(id=3)),
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
    )
    audit = AsyncMock()
    monkeypatch.setattr(
        share, "lock_assessment", AsyncMock(return_value=SimpleNamespace(assessment_revision=1))
    )
    monkeypatch.setattr(share, "report_out_for_check_run", AsyncMock(return_value=report))
    monkeypatch.setattr(share, "write_audit_event", audit)
    monkeypatch.setattr(
        share,
        "PublicShareSnapshot",
        lambda **fields: SimpleNamespace(
            model_dump=lambda mode=None: {
                "integrity_modules": [
                    module.model_dump(mode="json") for module in fields["integrity_modules"]
                ]
            }
        ),
    )
    await share._record_public_details(session, SimpleNamespace(check_run_id=7, token="t"))
    payload = audit.await_args.kwargs["payload"]
    [module] = payload["integrity_modules"]
    assert module["agreement_scope"]["n_results_set_aside"] == 3
    assert module["agreement_scope"]["set_aside"] == []
    assert "Smith found" not in str(payload)
