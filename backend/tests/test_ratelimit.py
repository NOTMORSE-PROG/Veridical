"""BUG-004/D-020: per-instructor rate limiting for quota-spending
endpoints (check-run creation, manuscript ingest, rubric upload).
"""

import pytest

import app.ratelimit as ratelimit
from app.config import get_settings
from app.errors import RateLimitedError


@pytest.fixture(autouse=True)
def _reset_limiters():
    """Module-global limiter cache (app/ratelimit.py) — don't leak counts
    across tests, same reasoning as auth_service._rate_limiter resets."""
    ratelimit._limiters.clear()
    yield
    ratelimit._limiters.clear()


def test_blocks_the_call_after_max_attempts_within_the_window(monkeypatch):
    monkeypatch.setenv("ACTION_RATE_LIMIT_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("ACTION_RATE_LIMIT_WINDOW_SECONDS", "3600")
    get_settings.cache_clear()
    settings = get_settings()

    for _ in range(3):
        ratelimit.enforce_action_rate_limit(settings, "test_scope_a", 1)
    with pytest.raises(RateLimitedError):
        ratelimit.enforce_action_rate_limit(settings, "test_scope_a", 1)


def test_different_instructors_have_independent_buckets(monkeypatch):
    monkeypatch.setenv("ACTION_RATE_LIMIT_MAX_ATTEMPTS", "1")
    get_settings.cache_clear()
    settings = get_settings()

    ratelimit.enforce_action_rate_limit(settings, "test_scope_b", 1)
    with pytest.raises(RateLimitedError):
        ratelimit.enforce_action_rate_limit(settings, "test_scope_b", 1)
    ratelimit.enforce_action_rate_limit(settings, "test_scope_b", 2)  # different instructor: fine


def test_different_scopes_have_independent_buckets(monkeypatch):
    monkeypatch.setenv("ACTION_RATE_LIMIT_MAX_ATTEMPTS", "1")
    get_settings.cache_clear()
    settings = get_settings()

    ratelimit.enforce_action_rate_limit(settings, "test_scope_c1", 1)
    with pytest.raises(RateLimitedError):
        ratelimit.enforce_action_rate_limit(settings, "test_scope_c1", 1)
    ratelimit.enforce_action_rate_limit(settings, "test_scope_c2", 1)  # different scope: fine
