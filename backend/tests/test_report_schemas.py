"""Pure Pydantic-schema-level tests for `app.report.schemas` -- no DB, no
live server. BUG-096: exceptional resolutions retain the reason minimum.
Owner 2026-10-26: ordinary grade comments are optional.
BUG-105: the identical fix for the reopen reason.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.report.schemas import ReopenIn, ResolveEscalationIn


def test_a_one_character_exclusion_reason_is_rejected():
    """Excluding an item still requires a substantive reason after the
    owner made ordinary grading comments optional. Private resolution
    reasons remain absent from the current public share projection."""
    with pytest.raises(ValidationError, match="at least"):
        ResolveEscalationIn(resolution="needs_document", reason="x")


def test_a_reason_of_only_whitespace_is_rejected():
    with pytest.raises(ValidationError, match="at least"):
        ResolveEscalationIn(resolution="needs_document", reason="          ")


def test_a_real_reason_is_accepted_and_stripped():
    parsed = ResolveEscalationIn(resolution="needs_document", reason="  Verified in Chapter 2.  ")
    assert parsed.reason == "Verified in Chapter 2."


def test_a_reason_exactly_at_the_minimum_is_accepted():
    minimum = Settings().resolution_reason_min_length
    parsed = ResolveEscalationIn(resolution="needs_document", reason="x" * minimum)
    assert len(parsed.reason) == minimum


def test_a_reason_one_character_short_of_the_minimum_is_rejected():
    minimum = Settings().resolution_reason_min_length
    with pytest.raises(ValidationError, match="at least"):
        ResolveEscalationIn(resolution="needs_document", reason="x" * (minimum - 1))


@pytest.mark.parametrize("resolution", ["mark_pass", "mark_fail", "mark_level"])
@pytest.mark.parametrize("comment", ["", "   ", "x", " OK "])
def test_ordinary_grades_allow_blank_and_short_comments(resolution, comment):
    assert ResolveEscalationIn(resolution=resolution, reason=comment).reason == comment.strip()


@pytest.mark.parametrize("resolution", ["mark_pass", "mark_fail", "mark_level"])
def test_ordinary_grade_comment_can_be_omitted(resolution):
    assert ResolveEscalationIn(resolution=resolution).reason == ""


@pytest.mark.parametrize("resolution", ["needs_document", "accept_majority"])
def test_exceptional_resolutions_require_a_reason_even_when_omitted(resolution):
    for fields in [{}, {"reason": ""}, {"reason": "   "}, {"reason": "x"}]:
        with pytest.raises(ValidationError, match="at least"):
            ResolveEscalationIn(resolution=resolution, **fields)
    minimum = Settings().resolution_reason_min_length
    assert ResolveEscalationIn(resolution=resolution, reason="x" * minimum).reason == "x" * minimum


@pytest.mark.parametrize("value", [None, 123, [], {}])
def test_optional_comment_still_requires_text(value):
    with pytest.raises(ValidationError):
        ResolveEscalationIn(resolution="mark_pass", reason=value)


def test_default_minimum_matches_the_frontend_hardcoded_copy():
    """`EscalatedPanel.tsx`'s `RESOLUTION_REASON_MIN_LENGTH` can't read
    backend `Settings` per-request (same disclosed exception D-023 made
    for `weight_importance`'s ratios) -- this is the tripwire: if this
    default ever changes, this test fails and says so, rather than the
    two copies silently drifting apart."""
    assert Settings().resolution_reason_min_length == 10


def test_bug_105_a_one_character_reopen_reason_is_rejected():
    """The identical defect BUG-096 fixed for `ResolveEscalationIn`,
    found on `ReopenIn` by backend review reviewing that same diff."""
    with pytest.raises(ValidationError, match="at least"):
        ReopenIn(reason="x")


def test_bug_105_a_real_reopen_reason_is_accepted_and_stripped():
    parsed = ReopenIn(reason="  Found a citation issue after deciding.  ")
    assert parsed.reason == "Found a citation issue after deciding."
