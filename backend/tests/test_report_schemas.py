"""Pure Pydantic-schema-level tests for `app.report.schemas` -- no DB, no
live server. BUG-096: the resolution-reason minimum length lives here.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.report.schemas import ResolveEscalationIn


def test_a_one_character_reason_is_rejected():
    """The exact `newcomer`-reproduced failure: "x" used to satisfy
    `min_length=1` and was then published verbatim to the report, the
    exported PDF, and the public share link as "Marked Pass. x"."""
    with pytest.raises(ValidationError, match="at least"):
        ResolveEscalationIn(resolution="mark_pass", reason="x")


def test_a_reason_of_only_whitespace_is_rejected():
    with pytest.raises(ValidationError, match="at least"):
        ResolveEscalationIn(resolution="mark_pass", reason="          ")


def test_a_real_reason_is_accepted_and_stripped():
    parsed = ResolveEscalationIn(resolution="mark_pass", reason="  Verified in Chapter 2.  ")
    assert parsed.reason == "Verified in Chapter 2."


def test_a_reason_exactly_at_the_minimum_is_accepted():
    minimum = Settings().resolution_reason_min_length
    parsed = ResolveEscalationIn(resolution="mark_pass", reason="x" * minimum)
    assert len(parsed.reason) == minimum


def test_a_reason_one_character_short_of_the_minimum_is_rejected():
    minimum = Settings().resolution_reason_min_length
    with pytest.raises(ValidationError, match="at least"):
        ResolveEscalationIn(resolution="mark_pass", reason="x" * (minimum - 1))


def test_default_minimum_matches_the_frontend_hardcoded_copy():
    """`EscalatedPanel.tsx`'s `RESOLUTION_REASON_MIN_LENGTH` can't read
    backend `Settings` per-request (same disclosed exception D-023 made
    for `weight_importance`'s ratios) -- this is the tripwire: if this
    default ever changes, this test fails and says so, rather than the
    two copies silently drifting apart."""
    assert Settings().resolution_reason_min_length == 10
