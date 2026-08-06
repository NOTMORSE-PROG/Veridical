"""Schema-level enumerations (state machines and closed value sets).

These are structural invariants owned by the code — the *thresholds* that
act on them are config (ENGINEERING.md §8), but the set of states itself
is part of the schema. Values are stored as plain VARCHAR (non-native
enums, no CHECK) so adding a member never needs a Postgres enum-type
migration; membership is validated app-side by SQLAlchemy on every write.
"""

from enum import StrEnum


class CriterionType(StrEnum):
    """F3.1 criterion router: deterministic rule vs AI grading."""

    structural = "structural"
    semantic = "semantic"


class IngestStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"


class IngestFailureReason(StrEnum):
    """Closed, deliberately small taxonomy for BUG-016 (a failed-ingestion
    row must tell the instructor something honest, not nothing). Three
    buckets, not one per exception type — resist enumerating every possible
    failure; a generic-but-honest catch-all beats an ever-growing list."""

    file_too_large = "file_too_large"
    unreadable_format = "unreadable_format"
    extraction_failed = "extraction_failed"


class CheckRunStatus(StrEnum):
    """Pipeline state machine (ENGINEERING.md §4)."""

    queued = "queued"
    ingesting = "ingesting"
    structural = "structural"
    semantic = "semantic"
    integrity = "integrity"
    aggregating = "aggregating"
    done = "done"
    failed = "failed"


class CheckKind(StrEnum):
    """What produced a check_result: a rubric criterion or an integrity check."""

    structural = "structural"
    semantic = "semantic"
    internal_agreement = "internal_agreement"  # F4
    citation_integrity = "citation_integrity"  # F5
    statistical_forensics = "statistical_forensics"  # F6
    originality_reuse = "originality_reuse"  # F7


class ResultOutcome(StrEnum):
    """Findings and non-findings, kept distinct (failure taxonomy, TESTING.md §5).

    An API being down or a check not applying must never read as a result.
    """

    passed = "passed"
    failed = "failed"
    escalated = "escalated"  # low confidence → instructor decides (F3.5)
    not_applicable = "not_applicable"
    unverifiable = "unverifiable"
    api_down = "api_down"
    quota_exhausted = "quota_exhausted"


class RubricParseStatus(StrEnum):
    """F2.2: a rubric decomposition either passed the validation gate, or
    exhausted its retries and needs manual completion in the review
    screen — never a dead-end error (charter rule 1: escalate, don't
    guess)."""

    parsed = "parsed"
    needs_review = "needs_review"


class CitationParseStatus(StrEnum):
    """F1.5: a reference entry either parsed into structured fields or is
    preserved raw — never silently dropped."""

    parsed = "parsed"
    parse_failed = "parse_failed"


class FlagSeverity(StrEnum):
    high = "high"
    med = "med"
    low = "low"


class ReadinessStatus(StrEnum):
    ready = "ready"
    conditionally_ready = "conditionally_ready"
    not_ready = "not_ready"
    # F8.1/V-019 edge case: nothing was auto-decidable (all criteria
    # escalated, or the decidable weight sum is zero) — an honest state,
    # never a fabricated number (charter rule 9: N/A is not "passed").
    needs_review = "needs_review"


class ReportDecision(StrEnum):
    """The instructor's decision — always human, never VERIDICAL's (F8.5)."""

    approved = "approved"
    returned = "returned"
    rejected = "rejected"
