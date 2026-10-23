"""Pure-function coverage for `_passage_pair_from_detail` (BUG-153). No
live DB needed -- same convention as `test_checks_reuse_service.py`'s
pure-function tests for the wording-template drafts this detail dict
comes from."""

import pytest

from app.flags.service import _agreement_pair, _p_value_comparison, _passage_pair_from_detail


def agreement_detail():
    return {
        "kind": "agreement_contradictory",
        "intent_text": " To build a login module.\n",
        "intent_anchor": "page 1",
        "outcome_text": "The login module was not implemented.",
        "outcome_anchor": "page 2",
        "gemini_reasoning": "Private raw model response",
        "private": "not exposed",
    }


def test_agreement_pair_is_whitelisted_exact_saved_evidence():
    detail = agreement_detail()
    assert _agreement_pair("internal_agreement", detail).model_dump() == {
        key: detail[key]
        for key in ("intent_text", "intent_anchor", "outcome_text", "outcome_anchor")
    }
    assert _agreement_pair("citation_integrity", detail) is None
    assert (
        _agreement_pair("internal_agreement", {**detail, "kind": "agreement_unmatched_intent"})
        is None
    )


@pytest.mark.parametrize("missing", [None, "", "   ", 0, False, [], {}])
@pytest.mark.parametrize("field", ["intent_text", "outcome_text"])
def test_incomplete_agreement_pair_never_gets_fabricated(field, missing):
    assert _agreement_pair("internal_agreement", {**agreement_detail(), field: missing}) is None


@pytest.mark.parametrize("missing", [None, 0, False, [], {}])
def test_agreement_missing_anchor_preserves_both_texts_without_inventing_location(missing):
    pair = _agreement_pair("internal_agreement", {**agreement_detail(), "outcome_anchor": missing})
    assert pair.outcome_text == agreement_detail()["outcome_text"]
    assert pair.outcome_anchor is None


@pytest.mark.parametrize("kind", [None, [], {}, "unknown"])
def test_invalid_evidence_kind_degrades_without_an_exception(kind):
    detail = {**agreement_detail(), "kind": kind}
    assert _agreement_pair("internal_agreement", detail) is None
    assert _p_value_comparison("statistical_forensics", detail) is None


def test_p_value_comparison_is_exact_whitelisted_saved_display():
    detail = {
        "kind": "p_value_decision_error",
        "reported_p_display": "= 0.900",
        "computed_p_display": "0.021",
        "private": "not public",
    }
    comparison = _p_value_comparison("statistical_forensics", detail)
    assert comparison.model_dump() == {"reported": "= 0.900", "recalculated": "0.021"}
    assert _p_value_comparison("citation_integrity", detail) is None
    assert (
        _p_value_comparison("statistical_forensics", {**detail, "kind": "grim_inconsistent"})
        is None
    )


@pytest.mark.parametrize("missing", [None, "", "   ", 0.021, {"value": "0.021"}])
def test_p_value_comparison_does_not_invent_missing_display(missing):
    assert (
        _p_value_comparison(
            "statistical_forensics",
            {
                "kind": "p_value_mismatch",
                "reported_p_display": "= 0.900",
                "computed_p_display": missing,
            },
        )
        is None
    )


def test_passage_kind_flag_builds_pair_from_evidence_excerpt_as_before():
    """Unchanged pre-BUG-153 path: a genuine passage-level flag's own
    `evidence_excerpt` IS the own-side excerpt."""
    detail = {
        "kind": "reuse_exact_duplicate_passage",
        "own_context_text": "Before text. The quoted passage. After text.",
        "matched_manuscript_id": 7,
        "matched_text": "The quoted passage.",
        "matched_context_text": "Matched before. The quoted passage. Matched after.",
        "similarity": 0.97,
    }
    pair = _passage_pair_from_detail("The quoted passage.", detail)

    assert pair is not None
    assert pair.own_excerpt == "The quoted passage."
    assert pair.own_context_before == "Before text."
    assert pair.own_context_after == "After text."
    assert pair.matched_ref == 7
    assert pair.matched_excerpt == "The quoted passage."
    assert pair.level == "exact_duplicate"


def test_whole_document_flag_with_no_supporting_passage_stays_null():
    """The pre-fix behavior for the case BUG-153 could not evidence at
    all (`checks.reuse.service` downgrades severity for this same case,
    tested live in `test_checks_reuse_query.py`) -- `passage_pair` must
    stay `None`, never a fabricated pair."""
    detail = {
        "kind": "reuse_exact_duplicate",
        "matched_manuscript_id": 3,
    }
    assert _passage_pair_from_detail("This manuscript appears to be a duplicate.", detail) is None


def test_whole_document_flag_builds_pair_from_supporting_passage_detail():
    """BUG-153: a whole-document match's `evidence_excerpt` is the
    templated accusation sentence, not real text -- the pair must be built
    from `detail["supporting_passage"]` instead, and the sentence itself
    must never leak into `own_excerpt`."""
    accusation = (
        "This manuscript appears to be an exact or near-exact textual duplicate of "
        "archived manuscript #3 in VERIDICAL's shared originality library: possible "
        "resubmission or reuse. Please verify manually."
    )
    detail = {
        "kind": "reuse_exact_duplicate",
        "matched_manuscript_id": 3,
        "supporting_passage": {
            "own_text": "The system architecture follows a three-tier design.",
            "own_context_text": "Chapter 3 begins here. The system architecture follows a "
            "three-tier design. It continues below.",
            "matched_text": "The system architecture follows a three-tier design.",
            "matched_context_text": "Prior context. The system architecture follows a "
            "three-tier design. Later context.",
            "matched_manuscript_id": 3,
            "similarity": 0.94,
            "level": "exact_duplicate",
        },
    }
    pair = _passage_pair_from_detail(accusation, detail)

    assert pair is not None
    assert pair.own_excerpt == "The system architecture follows a three-tier design."
    assert accusation not in pair.own_excerpt
    assert pair.own_context_before == "Chapter 3 begins here."
    assert pair.own_context_after == "It continues below."
    assert pair.matched_ref == 3
    assert pair.matched_excerpt == "The system architecture follows a three-tier design."
    assert pair.similarity == 0.94
    # Read the passage's stored classification without inferring the parent
    # band or recalculating it from current thresholds.
    assert pair.level == "exact_duplicate"


def test_chapter_flag_supporting_passage_also_builds_a_pair():
    detail = {
        "kind": "reuse_high_similarity_chapter",
        "matched_manuscript_id": 11,
        "supporting_passage": {
            "own_text": "Respondents were selected using stratified random sampling.",
            "own_context_text": "Respondents were selected using stratified random sampling.",
            "matched_text": "Respondents were selected via stratified random sampling.",
            "matched_context_text": "Respondents were selected via stratified random sampling.",
            "matched_manuscript_id": 11,
            "similarity": 0.81,
            "level": "high_similarity",
        },
    }
    pair = _passage_pair_from_detail("The section shows high textual similarity.", detail)

    assert pair is not None
    assert pair.matched_ref == 11
    assert pair.level == "high_similarity"


@pytest.mark.parametrize(
    "level_fields",
    [{}, {"level": None}, {"level": "high_similarity"}, {"level": "exact_duplicate"}],
)
def test_unclassified_or_historical_support_preserves_the_actual_pair(level_fields):
    detail = {
        "kind": "reuse_exact_duplicate",
        "supporting_passage": {
            "own_text": "Own central excerpt",
            "own_context_text": "Before own. Own central excerpt After own.",
            "matched_text": "Stored central excerpt",
            "matched_context_text": "Before stored. Stored central excerpt After stored.",
            "matched_manuscript_id": 13,
            "similarity": 0.2,
            **level_fields,
        },
    }
    pair = _passage_pair_from_detail("Parent summary is not passage evidence", detail)
    assert pair is not None
    assert pair.level == level_fields.get("level")
    assert pair.model_dump(mode="json")["level"] == level_fields.get("level")
    assert (pair.own_excerpt, pair.matched_excerpt) == (
        "Own central excerpt",
        "Stored central excerpt",
    )
    assert (pair.own_context_before, pair.own_context_after) == ("Before own.", "After own.")
    assert (pair.matched_context_before, pair.matched_context_after) == (
        "Before stored.",
        "After stored.",
    )
