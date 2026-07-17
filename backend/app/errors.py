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


class UnverifiableError(VeridicalError):
    """Source can't be checked — manual review, NOT an accusation."""

    code = "unverifiable"
