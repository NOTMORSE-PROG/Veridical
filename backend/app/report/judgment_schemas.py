"""Private saved-judgment revision contracts (F3.5/F8.3, screens 4h/4w)."""

from datetime import datetime
from math import isfinite, ulp
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import get_settings
from app.report.rule_facts import RuleFacts
from app.report.schemas import CriterionLevelOut, EvidenceItem, ReportOut, RubricLevelOut
from app.report.source_coverage import CriterionSourceCoverageOut

ResolutionType = Literal[
    "mark_pass", "mark_fail", "mark_level", "needs_document", "accept_majority"
]
RevisionResolution = Literal["mark_pass", "mark_fail", "mark_level", "needs_document"]


class PrivateJudgmentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JudgmentSnapshot(PrivateJudgmentModel):
    judgment_revision: int = Field(strict=True, ge=0)
    resolution_type: ResolutionType
    outcome: Literal["passed", "failed", "not_applicable"]
    score: float | None
    level: CriterionLevelOut | None
    reason: str
    actor_id: int | None = Field(strict=True, gt=0)
    recorded_at: datetime | None
    provenance: Literal["recorded", "legacy_partial"]

    @field_validator("score", mode="before")
    @classmethod
    def numeric_score(cls, value):
        if value is not None and (type(value) not in (int, float) or not isfinite(value)):
            raise ValueError("A recorded score must be a finite number or null.")
        return value

    @field_validator("level", mode="before")
    @classmethod
    def recorded_level(cls, value):
        from app.report.judgment_snapshot import stored_level

        if value is None:
            return None
        raw = value.model_dump() if isinstance(value, CriterionLevelOut) else value
        level = stored_level({"level": raw})
        if level is None:
            raise ValueError("The recorded named grade is malformed.")
        return level

    @model_validator(mode="after")
    def consistent_recorded_facts(self) -> "JudgmentSnapshot":
        from app.report.judgment_snapshot import manual_grade_consistent

        # Reuse the persisted manual-grade contract; historical JSON is not
        # trusted merely because it carries a schema-version label.
        if (
            self.resolution_type in {"needs_document", "accept_majority"}
            and not self.reason.strip()
        ) or not manual_grade_consistent(self.resolution_type, self.outcome, self.score):
            raise ValueError("The recorded judgment has inconsistent grade facts.")
        if self.recorded_at is not None and self.recorded_at.utcoffset() is None:
            raise ValueError("A recorded timestamp must identify its time zone.")
        if self.provenance == "recorded" and (
            self.judgment_revision == 0
            or self.actor_id is None
            or self.recorded_at is None
            or (self.resolution_type == "mark_level" and self.level is None)
        ):
            raise ValueError("The recorded judgment lacks its stated provenance.")
        return self


class JudgmentChoices(PrivateJudgmentModel):
    kind: Literal["levelled", "pass_fail", "unavailable"]
    levels: list[RubricLevelOut] = Field(default_factory=list)
    exclusion_allowed: bool
    unavailable_reason: str | None = None


class OriginalJudgmentAssessment(PrivateJudgmentModel):
    source_coverage: CriterionSourceCoverageOut | None = None
    rule_facts: RuleFacts | None = None
    rule_scope_unconfirmed: bool = False
    kind: str
    basis: str | None
    reason: str | None
    reasoning: str | None
    ai_majority_verdict: str | None
    evidence: list[EvidenceItem]
    unverified_evidence: list[str]
    support_withheld: bool
    support_status: str | None
    evidence_unavailable: bool = False
    original_outcome: str | None = None
    original_score: float | None = None
    original_level: CriterionLevelOut | None = None

    @field_validator("original_score", mode="before")
    @classmethod
    def original_numeric_score(cls, value):
        if value is not None and (type(value) not in (int, float) or not isfinite(value)):
            raise ValueError("An original score must be a finite number or null.")
        return value

    @field_validator("original_level", mode="before")
    @classmethod
    def original_named_level(cls, value):
        from app.report.judgment_snapshot import stored_level

        if value is None:
            return None
        raw = value.model_dump() if isinstance(value, CriterionLevelOut) else value
        level = stored_level({"level": raw})
        if level is None:
            raise ValueError("The original named grade is malformed.")
        return level


class JudgmentContextOut(PrivateJudgmentModel):
    check_run_id: int
    criterion_id: int
    check_result_id: int
    criterion_text: str
    assessment_revision: int = Field(strict=True, ge=0)
    decision: Literal["approved", "returned", "rejected"] | None
    can_revise: bool
    can_record: bool = False
    unavailable_reason: str | None
    current: JudgmentSnapshot | None
    choices: JudgmentChoices
    original_assessment: OriginalJudgmentAssessment


class JudgmentRecordIn(PrivateJudgmentModel):
    expected_revision: int = Field(strict=True, ge=0)
    resolution: RevisionResolution
    level: int | None = Field(default=None, strict=True)
    reason: str

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        stripped = value.strip()
        settings = get_settings()
        if len(stripped) < settings.resolution_reason_min_length:
            raise ValueError(
                f"reason must be at least {settings.resolution_reason_min_length} characters"
            )
        if len(stripped) > settings.judgment_revision_reason_max_length:
            raise ValueError(
                f"reason must be at most {settings.judgment_revision_reason_max_length} characters"
            )
        return stripped

    @model_validator(mode="after")
    def validate_level(self) -> "JudgmentRecordIn":
        if (self.resolution == "mark_level") != (self.level is not None):
            raise ValueError("a level is required only for a named-level judgment")
        return self


class JudgmentRevisionIn(JudgmentRecordIn):
    expected_judgment_revision: int = Field(strict=True, ge=0)


class JudgmentHistoryEntry(PrivateJudgmentModel):
    schema_version: Literal[1, 2]
    entry_id: int
    check_run_id: int
    criterion_id: int
    check_result_id: int
    action: Literal["initial_resolution", "judgment_revised", "judgment_recorded"]
    assessment_revision_before: int = Field(strict=True, ge=0)
    assessment_revision_after: int = Field(strict=True, ge=0)
    before: JudgmentSnapshot | None
    after: JudgmentSnapshot
    performed_by: int = Field(strict=True, gt=0)
    performed_at: datetime
    original_assessment: OriginalJudgmentAssessment | None = None

    @model_validator(mode="after")
    def versioned_action(self) -> "JudgmentHistoryEntry":
        if self.schema_version == 2:
            if self.action != "judgment_recorded" or self.original_assessment is None:
                raise ValueError("The initial judgment event lacks its original assessment.")
            from app.checks.levels import outcome_and_score
            from app.models.run import CheckResult

            original = self.original_assessment
            _, full, _ = outcome_and_score(None, "pass")
            _, empty, _ = outcome_and_score(None, "fail")
            if self.after.resolution_type == "accept_majority":
                raise ValueError("An initial judgment must record an explicit instructor choice.")
            if (
                self.after.resolution_type in ("mark_pass", "mark_fail", "needs_document")
                and self.after.level is not None
            ):
                raise ValueError("A binary judgment or exclusion cannot carry a named grade.")
            if self.after.resolution_type == "mark_level":
                level = self.after.level
                precision = CheckResult.__table__.c.score.type.scale
                tolerance = 10**-precision / 2 + ulp(full)
                if (
                    level is None
                    or abs(self.after.score - level.points / level.max_points * full) > tolerance
                ):
                    raise ValueError("The recorded named grade and stored score disagree.")
            if (
                original.kind not in ("structural", "semantic")
                or original.original_outcome not in ("passed", "failed")
                or original.original_score is None
                or not isfinite(original.original_score)
                or not empty <= original.original_score <= full
                or (original.original_outcome == "failed" and original.original_score != empty)
            ):
                raise ValueError("The initial judgment event has no valid settled assessment.")
        elif self.action == "judgment_recorded" or self.original_assessment is not None:
            raise ValueError("The saved event does not match its history schema.")
        return self


class JudgmentHistoryOut(PrivateJudgmentModel):
    check_run_id: int
    criterion_id: int
    check_result_id: int
    assessment_revision: int = Field(strict=True, ge=0)
    judgment_revision: int = Field(strict=True, ge=0)
    current: JudgmentSnapshot
    entries: list[JudgmentHistoryEntry]
    history_incomplete: bool
    next_cursor: str | None


class JudgmentRevisionOut(PrivateJudgmentModel):
    context: JudgmentContextOut
    entry: JudgmentHistoryEntry
    report: ReportOut


class JudgmentRecordOut(JudgmentRevisionOut):
    """Acknowledgment of an initial judgment, with no prior manual snapshot."""
