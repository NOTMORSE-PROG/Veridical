"""Per-instructor rate limiting for quota-spending endpoints (BUG-004,
D-020): defense-in-depth against a single actor exhausting the shared
Gemini pool (D-001/D-014). Reuses the same sliding-window primitive as
login throttling (`app/auth/rate_limit.py`) — one algorithm, many scopes.
"""

from app.auth.rate_limit import LoginRateLimiter
from app.config import Settings
from app.errors import RateLimitedError

_limiters: dict[str, LoginRateLimiter] = {}


def _get_limiter(settings: Settings, scope: str) -> LoginRateLimiter:
    limiter = _limiters.get(scope)
    if limiter is None:
        limiter = LoginRateLimiter(
            max_attempts=settings.action_rate_limit_max_attempts,
            window_seconds=settings.action_rate_limit_window_seconds,
        )
        _limiters[scope] = limiter
    return limiter


def enforce_action_rate_limit(settings: Settings, scope: str, instructor_id: int) -> None:
    """Raise `RateLimitedError` if `instructor_id` has exceeded the shared
    per-scope budget (config, D-020). Call once per request, before doing
    the expensive/quota-spending work — each `scope` (e.g. "check_run",
    "manuscript_ingest", "rubric_upload") gets its own independent bucket
    per instructor, so hammering one endpoint doesn't lock out another."""
    limiter = _get_limiter(settings, scope)
    key = str(instructor_id)
    if limiter.is_blocked(key):
        raise RateLimitedError("Too many requests — please wait a bit before trying again.")
    limiter.record_attempt(key)
