"""Pure-function coverage for `_passage_pair_from_detail` (BUG-153). No
live DB needed -- same convention as `test_checks_reuse_service.py`'s
pure-function tests for the wording-template drafts this detail dict
comes from."""

from app.flags.service import _passage_pair_from_detail


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
    # The pair's level echoes the PARENT match's own level (set by
    # `checks.reuse.service._supporting_passage_detail`), not a
    # separately-recomputed passage-level band -- see that function's own
    # docstring for why a mismatch there would read as inconsistent.
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
