"""Pure-function coverage for `_match_to_flag_draft`'s wording templates
(BUG-050/BUG-097 display-leak fix). No live DB needed -- unlike
`test_checks_reuse_query.py`'s end-to-end pgvector tests, this exercises
all four severity/granularity combinations directly, including the
HIGH_SIMILARITY templates the pipeline-level tests never reach (their
fixtures always produce exact duplicates). `backend-critic` review
(2026-08-19) flagged that gap as the one real hole in the original
regression test."""

import pytest

from app.checks.reuse.query import SimilarityMatch
from app.checks.reuse.service import _match_to_flag_draft
from app.models.enums import FlagSeverity

OTHER_GROUP = "BSIT-4A Attendance Monitoring System Group"
OTHER_CHAPTER = "CHAPTER 2 REVIEW OF RELATED LITERATURE AND STUDIES"
OWN_CHAPTER = "Related Work"


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

    # `detail` stays internal bookkeeping only -- confirmed elsewhere
    # (app/flags/service.py) that it is never wholesale-serialized to
    # any API response, so it deliberately still carries the raw fields.
    assert detail["matched_group_label"] == OTHER_GROUP
    assert detail["matched_chapter_title"] == chapter_title


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
        matched_group_label=OTHER_GROUP,
    )
    plain_severity, plain_reason, plain_detail = _match_to_flag_draft(match, first_upload=False)
    first_severity, first_reason, first_detail = _match_to_flag_draft(match, first_upload=True)

    assert plain_severity == first_severity == FlagSeverity.high
    assert plain_detail["first_upload_context"] is False
    assert first_detail["first_upload_context"] is True
    assert first_reason == plain_reason  # no duplicate disclosure in the reason text
