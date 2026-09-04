"""Pure-function coverage for `_match_to_flag_draft`'s wording templates
(BUG-050/BUG-097 display-leak fix). No live DB needed -- unlike
`test_checks_reuse_query.py`'s end-to-end pgvector tests, this exercises
all four severity/granularity combinations directly, including the
HIGH_SIMILARITY templates the pipeline-level tests never reach (their
fixtures always produce exact duplicates). `backend-critic` review
(2026-08-19) flagged that gap as the one real hole in the original
regression test."""

import pytest

from app.checks.reuse.query import PassageMatch, SimilarityMatch
from app.checks.reuse.service import (
    _match_to_flag_draft,
    _passage_match_to_flag_draft,
    _supporting_passage_detail,
    _supporting_passage_from_precomputed,
)
from app.models.enums import FlagSeverity

OTHER_GROUP = "BSIT-4A Attendance Monitoring System Group"
OTHER_CHAPTER = "CHAPTER 2 REVIEW OF RELATED LITERATURE AND STUDIES"
OWN_CHAPTER = "Related Work"


def _passage_match(**overrides) -> PassageMatch:
    defaults = dict(
        own_passage_index=0,
        own_chapter_index=0,
        own_page=1,
        own_paragraph=None,
        own_char_start=0,
        own_char_end=10,
        own_text="own text",
        own_context_text="own text",
        own_is_reference_list=False,
        own_is_block_quote=False,
        level="high_similarity",
        similarity=0.6,
        matched_manuscript_id=42,
        matched_group_label=OTHER_GROUP,
        matched_instructor_id=99,
        matched_chapter_index=0,
        matched_page=1,
        matched_paragraph=None,
        matched_char_start=0,
        matched_char_end=10,
        matched_text="matched text",
        matched_context_text="matched text",
        matched_is_reference_list=False,
        matched_is_block_quote=False,
    )
    defaults.update(overrides)
    return PassageMatch(**defaults)


@pytest.mark.parametrize(
    "level,chapter_title,expected_severity",
    [
        ("exact_duplicate", None, FlagSeverity.high),
        ("high_similarity", None, FlagSeverity.med),
        ("exact_duplicate", OTHER_CHAPTER, FlagSeverity.high),
        ("high_similarity", OTHER_CHAPTER, FlagSeverity.med),
    ],
)
def test_reason_never_leaks_matched_identity_or_heading(level, chapter_title, expected_severity):
    match = SimilarityMatch(
        level=level,
        similarity=0.83,
        matched_manuscript_id=42,
        matched_instructor_id=99,
        matched_group_label=OTHER_GROUP,
        matched_chapter_title=chapter_title,
        own_chapter_title=OWN_CHAPTER if chapter_title else None,
    )
    severity, reason, detail = _match_to_flag_draft(match)

    assert severity == expected_severity
    assert OTHER_GROUP not in reason
    if chapter_title:
        assert chapter_title not in reason
        assert OWN_CHAPTER in reason  # the instructor's own chapter IS safe to show
    # Bounded, non-identifying reference (BUG-050 item 5): identifiable,
    # never identifying.
    assert "#42" in reason
    assert "%" not in reason

    # `detail` stays internal bookkeeping only -- confirmed elsewhere
    # (app/flags/service.py) that it is never wholesale-serialized to
    # any API response, so it deliberately still carries the raw fields.
    assert detail["matched_group_label"] == OTHER_GROUP
    assert detail["matched_chapter_title"] == chapter_title
    assert detail["similarity"] == pytest.approx(0.83)


def test_passage_reason_uses_a_named_band_while_raw_similarity_remains_internal():
    match = PassageMatch(
        own_passage_index=3,
        level="high_similarity",
        similarity=0.84,
        matched_manuscript_id=42,
        matched_instructor_id=99,
        matched_group_label=OTHER_GROUP,
        own_chapter_index=1,
        own_page=4,
        own_paragraph=8,
        own_char_start=0,
        own_char_end=38,
        own_text="The manuscript's own bounded passage.",
        own_context_text="The manuscript's own bounded passage.",
        own_is_reference_list=False,
        own_is_block_quote=False,
        matched_chapter_index=2,
        matched_page=7,
        matched_paragraph=14,
        matched_char_start=0,
        matched_char_end=29,
        matched_text="The archived bounded passage.",
        matched_context_text="The archived bounded passage.",
        matched_is_reference_list=False,
        matched_is_block_quote=False,
    )

    severity, evidence, _anchor, detail = _passage_match_to_flag_draft(match)

    assert severity == FlagSeverity.med
    assert evidence == match.own_text
    assert "%" not in detail["reason"]
    assert "high textual similarity" in detail["reason"]
    assert detail["similarity"] == pytest.approx(0.84)


def test_first_upload_context_flags_detail_but_never_changes_severity_or_reason():
    """BUG-097 (presentation-only remedy, owner ruling 2026-08-24): a
    severity downgrade was considered and explicitly rejected (it would
    have weakened genuine duplicate detection broadly, not just softened
    one rare edge case -- see `query.is_first_upload_for_instructor`'s own
    docstring). `first_upload=True` sets `detail["first_upload_context"]`
    ONLY -- `ux-critic` finding (2026-08-24, live review of the built
    banner): an earlier version also appended a caveat sentence to
    `reason`, which duplicated the banner's own disclosure on the same
    screen (Nielsen #4). The banner is the sole disclosure surface now;
    `reason` must be byte-identical regardless of `first_upload`."""
    match = SimilarityMatch(
        level="exact_duplicate",
        similarity=0.97,
        matched_manuscript_id=7,
        matched_instructor_id=99,
        matched_group_label=OTHER_GROUP,
    )
    plain_severity, plain_reason, plain_detail = _match_to_flag_draft(match, first_upload=False)
    first_severity, first_reason, first_detail = _match_to_flag_draft(match, first_upload=True)

    assert plain_severity == first_severity == FlagSeverity.high
    assert plain_detail["first_upload_context"] is False
    assert first_detail["first_upload_context"] is True
    assert first_reason == plain_reason  # no duplicate disclosure in the reason text


def test_supporting_passage_detail_uses_the_passages_own_level_not_the_parents():
    """BUG-153 (`backend-critic` finding, live-reproduced): an earlier
    version echoed the PARENT whole-doc/chapter match's own `level` onto
    the supporting passage, reasoning the two should visually "agree" --
    live-reproduced to overstate a weak supporting passage as "Exact
    duplicate" purely because a stronger AGGREGATE match happened to
    contain it (the more dangerous direction: a flag can still force Not
    Ready under BUG-150 while showing evidence stronger than it is). Fixed
    to always use the supporting passage's OWN, independently classified
    level -- both callers (`query_similar_passages` and
    `best_supporting_passage`) only ever hand back a `PassageMatch` with a
    real, non-fabricated `level`, so this is never a guess."""
    weak_supporting_passage = _passage_match(level="high_similarity", similarity=0.6)
    detail = _supporting_passage_detail(weak_supporting_passage)
    assert detail["level"] == "high_similarity"
    assert detail["similarity"] == pytest.approx(0.6)

    strong_supporting_passage = _passage_match(level="exact_duplicate", similarity=0.99)
    detail = _supporting_passage_detail(strong_supporting_passage)
    assert detail["level"] == "exact_duplicate"
    assert detail["similarity"] == pytest.approx(0.99)


def test_supporting_passage_from_precomputed_is_scoped_to_the_matched_chapter_pair():
    """BUG-153 (`backend-critic` finding, live-reproduced): without this
    scoping, a Chapter 1 flag could be handed a supporting passage that
    actually pairs Chapter 2 content on one or both sides -- real text,
    but contradicting the very chapter the flag names. A whole-document
    match (both indices `None`) stays unscoped -- "any passage anywhere
    in the doc" is exactly what that granularity claims, live-verified
    separately in `test_checks_reuse_query.py`'s DB-level regression."""
    same_manuscript_wrong_chapter = _passage_match(
        own_chapter_index=1, matched_chapter_index=0, similarity=0.99
    )
    right_chapter_pair = _passage_match(
        own_chapter_index=0, matched_chapter_index=1, similarity=0.7
    )
    candidates = [same_manuscript_wrong_chapter, right_chapter_pair]

    scoped = _supporting_passage_from_precomputed(
        42, candidates, own_chapter_index=0, matched_chapter_index=1
    )
    assert scoped is right_chapter_pair  # the weaker but correctly-scoped candidate wins

    # A candidate scoped to the wrong own-chapter alone is excluded too.
    only_wrong_own_chapter = [_passage_match(own_chapter_index=1, matched_chapter_index=1)]
    assert (
        _supporting_passage_from_precomputed(
            42, only_wrong_own_chapter, own_chapter_index=0, matched_chapter_index=1
        )
        is None
    )

    # Whole-document match (no chapter claim to violate): unscoped, picks
    # the single strongest candidate regardless of chapter.
    unscoped = _supporting_passage_from_precomputed(42, candidates)
    assert unscoped is same_manuscript_wrong_chapter  # higher similarity, no scoping applied
