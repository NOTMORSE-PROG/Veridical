"""Failure taxonomy (TESTING.md §5) as typed exceptions.

Services raise these; one handler (added with the first public router) maps
them to the HTTP error envelope. The five codes are distinct states and must
never be conflated with findings: an API being down is not an integrity
problem, N/A is not a pass, unverifiable is not fake.
"""

import os
import traceback
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


class RubricLockedError(ConflictError):
    """A format version a check is pinned to, or one in its version history,
    cannot change; corrections go into a new version (F2.4)."""

    code = "rubric_locked"


class RubricChangedError(ConflictError):
    """The format changed since the client loaded it (another tab saved)."""

    code = "rubric_changed"


class UnresolvedRubricIssuesError(ConflictError):
    """Activation waits until every parse issue is resolved or acknowledged
    by the instructor (BUG-566: escalate, never activate a guess)."""

    code = "unresolved_rubric_issues"

    def __init__(self, message: str, open_issue_ids: list[int]) -> None:
        super().__init__(message)
        self.details = {"open_issue_ids": open_issue_ids}


class GoneError(VeridicalError):
    """A known resource that once existed is no longer available, such as
    a revoked/expired share link or a purged/missing stored source. Distinct
    from `not_found`, which means the requested resource does not exist or
    the caller is not authorized to know whether it exists."""

    code = "gone"


class SavedGroupChangedError(ConflictError):
    """The selected saved record changed; its displayed facts need fresh review."""

    code = "saved_group_changed"


class GroupAssignmentChangedError(ConflictError):
    """The manuscript now has a different assignment; review that assignment."""

    code = "group_assignment_changed"


class InvalidApiKeyError(VeridicalError):
    """V-052: an instructor-pasted Gemini API key failed a real validation
    probe — user-fixable (wrong/revoked key, typo), never a generic 500."""

    code = "invalid_api_key"


class RuntimeApiKeyRejectedError(InvalidApiKeyError):
    """An AI request received the transport's classified authentication rejection.

    Save-time validation uses the base type for other probe failures as well.
    """


class RateLimitedError(VeridicalError):
    """Too many requests in the configured window (BUG-004/D-020) — resume
    once the window resets; distinct from `unauthenticated` because the
    caller IS who they say they are, just going too fast."""

    code = "rate_limited"


class DocumentPreparationBusyError(VeridicalError):
    """Preparation admission rejected this attempt before durable work."""

    code = "document_preparation_busy"


class FormatAIConfigurationError(VeridicalError):
    """Format client resolution refused this attempt before creation started."""

    code = "ai_not_configured"

    def __str__(self):
        return (
            "AI format parsing could not start because no usable AI configuration was available. "
            "No format draft was created by this attempt. Review your AI settings or contact "
            "the deployment administrator, then try again."
        )


class ActorContextChangedError(VeridicalError):
    code = "actor_context_changed"


class ReportExportUnavailableError(VeridicalError):
    """Saved text cannot be rendered faithfully; the assessment is unaffected."""

    code = "report_export_unavailable"

    def __str__(self):
        return (
            "PDF export stopped because some characters could not be displayed. "
            "The report remains available in VERIDICAL."
        )


class SourceReadUnavailableError(VeridicalError):
    """An authorized local source read failed; no assessment was changed."""

    code = "source_read_unavailable"


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
    "rubric_locked": 409,
    "rubric_changed": 409,
    "unresolved_rubric_issues": 409,
    "saved_group_changed": 409,
    "group_assignment_changed": 409,
    "gone": 410,
    "rate_limited": 429,
    "document_preparation_busy": 503,
    "ai_not_configured": 503,
    "actor_context_changed": 409,
    "invalid_api_key": 422,
    "report_export_unavailable": 422,
    "source_read_unavailable": 503,
    # A rubric-decomposition parse that failed schema validation (V-010) —
    # user-actionable (re-review/re-upload); V-011 wraps this in a retry
    # loop so it only ever reaches HTTP after repeated failure.
    "parse_failed": 422,
}


_APP_DIR = os.path.dirname(os.path.abspath(__file__))


def failure_summary(exc: BaseException) -> str:
    """Name a failure for the operator log without its message.

    A message can quote manuscript text, so this gives only the exception
    class and the innermost frame of our own code, in the form
    "InterfaceError at app/report/service.py:<line> (get_report_export_data)".
    """
    frames = traceback.StackSummary.extract(
        traceback.walk_tb(exc.__traceback__), lookup_lines=False
    )
    ours = [
        frame for frame in frames if os.path.abspath(frame.filename).startswith(_APP_DIR + os.sep)
    ]
    if not ours:
        return type(exc).__name__
    frame = ours[-1]
    path = os.path.relpath(os.path.abspath(frame.filename), os.path.dirname(_APP_DIR))
    return f"{type(exc).__name__} at {path.replace(os.sep, '/')}:{frame.lineno} ({frame.name})"
