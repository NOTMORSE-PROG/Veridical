"""Failure taxonomy (TESTING.md §5) as typed exceptions.

Services raise these; one handler (added with the first public router) maps
them to the HTTP error envelope. The five codes are distinct states and must
never be conflated with findings: an API being down is not an integrity
problem, N/A is not a pass, unverifiable is not fake.
"""

from typing import ClassVar


class VeridicalError(Exception):
    """Base class; `code` is the taxonomy code exposed to clients."""

    code: ClassVar[str] = "internal"


class ApiDownError(VeridicalError):
    """External service unreachable — retry later."""

    code = "api_down"


class QuotaExhaustedError(VeridicalError):
    """Daily/period quota spent — resume when it resets (D-001)."""

    code = "quota_exhausted"


class FileMalformedError(VeridicalError):
    """The uploaded file can't be read — user-fixable."""

    code = "file_malformed"


class NotApplicableError(VeridicalError):
    """Check doesn't apply to this document — reported as N/A, never as a pass."""

    code = "not_applicable"


class FileTooLargeError(VeridicalError):
    """Upload exceeds the configured limit — rejected early, user-fixable."""

    code = "too_large"


class UnverifiableError(VeridicalError):
    """Source can't be checked — manual review, NOT an accusation."""

    code = "unverifiable"


class UnauthenticatedError(VeridicalError):
    """No valid session (F9.1) — the route guard's one taxonomy code;
    the message is always generic (no user enumeration)."""

    code = "unauthenticated"


class NotFoundError(VeridicalError):
    """The requested resource (rubric, manuscript, ...) doesn't exist —
    user-fixable (bad id, stale link), never a generic 500."""

    code = "not_found"


class ConflictError(VeridicalError):
    """The action can't complete because of existing dependent state
    (e.g. deleting/editing a rubric version that has reports pinned to
    it) — history is immutable (F2.4); the fix is a new version, not a
    mutation."""

    code = "conflict"


class GoneError(VeridicalError):
    """V-040: a share link that once worked but was deliberately revoked
    or has expired — distinct from `not_found` (a token that never
    existed) precisely so a revoked link's own visitor sees an honest
    "this was turned off" message, not an ambiguous "wrong URL" one."""

    code = "gone"


class InvalidApiKeyError(VeridicalError):
    """V-052: an instructor-pasted Gemini API key failed a real validation
    probe — user-fixable (wrong/revoked key, typo), never a generic 500."""

    code = "invalid_api_key"


class RateLimitedError(VeridicalError):
    """Too many requests in the configured window (BUG-004/D-020) — resume
    once the window resets; distinct from `unauthenticated` because the
    caller IS who they say they are, just going too fast."""

    code = "rate_limited"


# HTTP mapping for the one exception handler (main.py). Codes absent here
# are result STATES (not_applicable, unverifiable) that must never be
# raised to HTTP; if one ever is, the handler still answers with the
# structured envelope (and 500 signals the programming error it is).
HTTP_STATUS: dict[str, int] = {
    "file_malformed": 422,
    "too_large": 413,
    "quota_exhausted": 429,
    "api_down": 502,
    "unauthenticated": 401,
    "not_found": 404,
    "conflict": 409,
    "gone": 410,
    "rate_limited": 429,
    "invalid_api_key": 422,
    # A rubric-decomposition parse that failed schema validation (V-010) —
    # user-actionable (re-review/re-upload); V-011 wraps this in a retry
    # loop so it only ever reaches HTTP after repeated failure.
    "parse_failed": 422,
}
