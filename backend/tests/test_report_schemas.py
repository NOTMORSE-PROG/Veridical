"""Pending grade comments are optional; exception and reopen reasons stay required."""

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.report.schemas import ReopenIn, ResolveEscalationIn


@pytest.mark.parametrize("resolution", ["needs_document", "accept_majority"])
def test_an_exception_one_character_reason_is_rejected(resolution):
    with pytest.raises(ValidationError, match="at least"):
        ResolveEscalationIn(expected_revision=0, resolution=resolution, reason="x")


@pytest.mark.parametrize("resolution", ["needs_document", "accept_majority"])
def test_an_exception_reason_of_only_whitespace_is_rejected(resolution):
    with pytest.raises(ValidationError, match="at least"):
        ResolveEscalationIn(expected_revision=0, resolution=resolution, reason="          ")


def test_a_real_reason_is_accepted_and_stripped():
    parsed = ResolveEscalationIn(
        expected_revision=0, resolution="mark_pass", reason="  Verified in Chapter 2.  "
    )
    assert parsed.reason == "Verified in Chapter 2."


@pytest.mark.parametrize("resolution", ["needs_document", "accept_majority"])
def test_an_exception_reason_exactly_at_the_minimum_is_accepted(resolution):
    minimum = Settings().resolution_reason_min_length
    parsed = ResolveEscalationIn(expected_revision=0, resolution=resolution, reason="x" * minimum)
    assert len(parsed.reason) == minimum


@pytest.mark.parametrize("resolution", ["needs_document", "accept_majority"])
def test_an_exception_reason_one_character_short_of_the_minimum_is_rejected(resolution):
    minimum = Settings().resolution_reason_min_length
    with pytest.raises(ValidationError, match="at least"):
        ResolveEscalationIn(expected_revision=0, resolution=resolution, reason="x" * (minimum - 1))


@pytest.mark.parametrize("resolution", ["mark_pass", "mark_fail", "mark_level"])
@pytest.mark.parametrize("comment", ["", "  ", "x", "  Brief note  "])
def test_ordinary_grade_comments_are_optional_and_normalized(resolution, comment):
    parsed = ResolveEscalationIn(expected_revision=0, resolution=resolution, reason=comment)
    assert parsed.reason == comment.strip()


@pytest.mark.parametrize("resolution", ["mark_pass", "mark_fail", "mark_level"])
def test_ordinary_grade_can_omit_the_comment_field(resolution):
    parsed = ResolveEscalationIn(expected_revision=0, resolution=resolution)
    assert parsed.reason == ""


@pytest.mark.parametrize("resolution", ["needs_document", "accept_majority"])
def test_exception_cannot_omit_its_reason(resolution):
    with pytest.raises(ValidationError, match="at least"):
        ResolveEscalationIn(expected_revision=0, resolution=resolution)


@pytest.mark.parametrize("value", [None, 123])
def test_optional_comment_remains_a_text_field(value):
    with pytest.raises(ValidationError):
        ResolveEscalationIn(expected_revision=0, resolution="mark_pass", reason=value)


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
        ReopenIn(expected_revision=0, reason="x")


def test_bug_105_a_real_reopen_reason_is_accepted_and_stripped():
    parsed = ReopenIn(expected_revision=0, reason="  Found a citation issue after deciding.  ")
    assert parsed.reason == "Found a citation issue after deciding."
