"""One pipeline execution and everything it produces.

check_run is explicit (not implicit in manuscript) so re-runs under a new
rubric version are first-class (Flow E) and every result/flag/report hangs
off exactly one execution.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, PkCreatedMixin
from app.models.enums import (
    CheckKind,
    CheckRunStatus,
    FlagSeverity,
    LLMMode,
    ReadinessStatus,
    ReportDecision,
    ResultOutcome,
)


class CheckRun(Base, PkCreatedMixin):
    __tablename__ = "check_run"

    manuscript_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("manuscript.id"), index=True)
    rubric_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rubric.id"), index=True)
    status: Mapped[CheckRunStatus] = mapped_column(
        Enum(CheckRunStatus, native_enum=False), server_default=CheckRunStatus.queued
    )
    # BUG-049: which LLM backend produced this run's AI-graded results —
    # persisted at creation (never inferred later) so a report can always
    # honestly disclose whether it describes the real manuscript or a
    # canned fixture, even after the fact.
    llm_mode: Mapped[LLMMode] = mapped_column(
        Enum(LLMMode, native_enum=False), server_default=LLMMode.unknown
    )
    # Per-stage progress + failure taxonomy state, rendered by screens 4f–4g;
    # stage granularity is what makes quota-exhausted runs resumable (D-001).
    stage_status: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    results: Mapped[list["CheckResult"]] = relationship(back_populates="check_run")
    report: Mapped["ReadinessReport | None"] = relationship(back_populates="check_run")


class CheckResult(Base, PkCreatedMixin):
    __tablename__ = "check_result"

    check_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("check_run.id", ondelete="CASCADE"), index=True
    )
    # NULL for integrity checks (F4–F7) — they aren't rubric criteria.
    criterion_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("criterion.id"), index=True
    )
    kind: Mapped[CheckKind] = mapped_column(Enum(CheckKind, native_enum=False))
    outcome: Mapped[ResultOutcome] = mapped_column(Enum(ResultOutcome, native_enum=False))
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    check_run: Mapped["CheckRun"] = relationship(back_populates="results")
    flags: Mapped[list["Flag"]] = relationship(back_populates="check_result")


class Flag(Base, PkCreatedMixin):
    """A possible inconsistency, never an accusation (charter rule 3).

    evidence_excerpt and page_anchor are NOT NULL by design: a flag the
    instructor can't verify in 10 seconds is a bad flag even when correct
    (charter judgment rule 1).
    """

    __tablename__ = "flag"

    check_result_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("check_result.id", ondelete="CASCADE"), index=True
    )
    severity: Mapped[FlagSeverity] = mapped_column(Enum(FlagSeverity, native_enum=False))
    # Agreement score from self-consistency voting (D-006); NULL for
    # deterministic checks where confidence isn't a meaningful concept.
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    evidence_excerpt: Mapped[str] = mapped_column(Text)
    # Where to look: page/section locator; DOCX has no pages, so free-form.
    page_anchor: Mapped[str] = mapped_column(Text)
    # Structured per-FLAG detail (kind/reason/arithmetic) — added V-033.
    # A semantic-grading check_result has exactly one flag, so its "reason"
    # could live on check_result.detail (V-020's original design); F5/F6
    # (V-027-033) put MANY flags under ONE check_result, each with its own
    # distinct honest-wording reason, which check_result.detail's single
    # top-level key can't hold. `app.flags.service._to_flag_out` prefers
    # this field and falls back to check_result.detail for older checks.
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    annotation: Mapped[str | None] = mapped_column(Text)
    overridden: Mapped[bool] = mapped_column(Boolean, server_default="false")
    override_reason: Mapped[str | None] = mapped_column(Text)
    # BUG-078: distinguishes "confirmed the citation source is legitimate"
    # (also marks the shared citation_cache row, FEATURES.md §9) from an
    # ordinary override -- both set overridden/override_reason the same
    # way (same score-recalculation path), and override_reason is
    # instructor-authored free text on BOTH paths, so text-matching it
    # can't tell them apart reliably. A real column, not an inference
    # (ui-designer spec, 2026-08-24).
    confirmed_citation_source: Mapped[bool] = mapped_column(Boolean, server_default="false")

    check_result: Mapped["CheckResult"] = relationship(back_populates="flags")


class ReadinessReport(Base, PkCreatedMixin):
    __tablename__ = "readiness_report"

    check_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("check_run.id", ondelete="CASCADE"), unique=True
    )
    # NULL means "no real number exists" (V-019 edge case: an all-escalated
    # run, or a rubric whose decidable weight sum is zero) — never a
    # fabricated 0 or 100 (charter rule 9).
    composite_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    status: Mapped[ReadinessStatus] = mapped_column(Enum(ReadinessStatus, native_enum=False))
    # The human decision (F8.5) — VERIDICAL itself never sets this.
    decision: Mapped[ReportDecision | None] = mapped_column(Enum(ReportDecision, native_enum=False))
    # V-038: when/who/optional-note for the decision above. Cleared back to
    # NULL on reopen (the reopen event itself, with its required reason, is
    # what the immutable audit log preserves — this trio is a display
    # convenience for the CURRENT decision only, not the historical record).
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_instructor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("instructor.id")
    )
    decision_note: Mapped[str | None] = mapped_column(Text)

    check_run: Mapped["CheckRun"] = relationship(back_populates="report")
