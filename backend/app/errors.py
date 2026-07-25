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
    # A rubric-decomposition parse that failed schema validation (V-010) —
    # user-actionable (re-review/re-upload); V-011 wraps this in a retry
    # loop so it only ever reaches HTTP after repeated failure.
    "parse_failed": 422,
}
