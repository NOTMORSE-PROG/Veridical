"""The exported report lists undecided agreement pairs once, not a page each."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

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
    assert "2 selected pairs left unresolved" in texts[1]
    assert "not manuscript findings and do not change readiness" in texts[1]
    assert "Pairs compared with AI: 12." in texts
    assert len(texts) == 7
    assert texts.count("Comparison status not recorded.") == 2


def test_local_decline_export_retains_true_counts_and_reason():
    saved = UndeterminedPairsOut(
        count=1,
        compared_count=0,
        locally_unassessed_count=1,
        pairs=[
            dict(
                intent_text="Objective in Release Two.",
                intent_anchor="p. 3",
                outcome_text="Result in Release One.",
                outcome_anchor="p. 40",
                reason="The selected statements name different releases.",
                comparison_method="phase_guard",
            )
        ],
    )
    texts = _texts(_undecided_pairs_flowables(_report(saved), [], STYLES))
    assert "Pairs compared with AI: 0." in texts
    assert "Pairs left unassessed before AI comparison: 1." in texts
    assert any(
        "Left unassessed before AI comparison: The selected statements" in text for text in texts
    )
    assert not any("it compared" in text for text in texts)


def test_legacy_only_export_does_not_invent_a_comparison_count():
    texts = _texts(_undecided_pairs_flowables(_report(), [_legacy(9)], STYLES))
    assert "The number of pairs compared with AI was not recorded." in texts
    assert not any("Pairs compared with AI:" in text for text in texts)


def test_old_rows_alone_invent_no_compared_count():
    texts = _texts(_undecided_pairs_flowables(_report(), [_legacy(9), _legacy(10)], STYLES))
    assert texts[1].startswith("2 selected pairs left unresolved. ")
    assert " of " not in texts[1].split(".")[0]


def test_nothing_undecided_adds_nothing():
    assert _undecided_pairs_flowables(_report(), [], STYLES) == []


def test_saved_unresolved_pair_payload_requires_a_recorded_ai_comparison_count():
    with pytest.raises(ValidationError, match="compared_count"):
        UndeterminedPairsOut(count=1, compared_count=None, locally_unassessed_count=1, pairs=[])


def test_only_undecided_agreement_rows_are_folded():
    assert _is_legacy_undecided(_legacy(1))
    assert not _is_legacy_undecided(_legacy(2, kind="agreement_contradictory"))
