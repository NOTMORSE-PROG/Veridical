"""The exported report lists undecided agreement pairs once, not a page each."""

from types import SimpleNamespace

from app.report.export import _is_legacy_undecided, _styles, _undecided_pairs_flowables
from app.report.schemas import IntegrityModuleOut, UndeterminedPairsOut

STYLES = _styles("Helvetica")


def _report(undetermined=None):
    return SimpleNamespace(
        integrity_modules=[
            IntegrityModuleOut(
                check_kind="internal_agreement", state="partial", undetermined=undetermined
            )
        ]
    )


def _legacy(flag_id, kind="agreement_cannot_determine"):
    return SimpleNamespace(
        id=flag_id,
        check_kind="internal_agreement",
        problem_kind=kind,
        evidence_excerpt=f"Old objective {flag_id}",
        page_anchor="p. 2",
    )


def _texts(items):
    return [item.getPlainText() for item in items]


def test_saved_pairs_and_old_rows_become_one_list():
    saved = UndeterminedPairsOut(
        count=1,
        compared_count=12,
        pairs=[
            {
                "intent_text": "To build a login module.",
                "intent_anchor": "p. 3",
                "outcome_text": "The login screen was redesigned.",
                "outcome_anchor": "p. 40",
            }
        ],
    )
    texts = _texts(_undecided_pairs_flowables(_report(saved), [_legacy(9)], STYLES))
    assert texts[0] == "Pairs to read yourself"
    assert "could not decide on 2 of 12" in texts[1]
    assert "not a problem found in the manuscript" in texts[1]
    assert len(texts) == 4


def test_old_rows_alone_invent_no_compared_count():
    texts = _texts(_undecided_pairs_flowables(_report(), [_legacy(9), _legacy(10)], STYLES))
    assert texts[1].startswith("VERIDICAL could not decide on 2 objective and result pairs. ")
    assert " of " not in texts[1].split(".")[0]


def test_nothing_undecided_adds_nothing():
    assert _undecided_pairs_flowables(_report(), [], STYLES) == []


def test_only_undecided_agreement_rows_are_folded():
    assert _is_legacy_undecided(_legacy(1))
    assert not _is_legacy_undecided(_legacy(2, kind="agreement_contradictory"))
