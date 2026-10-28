"""Aggregation service (F8.1): projects a check_run's persisted
check_results/flags into `ScorableResult`/`ScorableFlag`, runs the pure
`score_check_run`, and persists the composite/status onto the run's
`ReadinessReport` (created once, updated on re-aggregation — e.g. after
an instructor resolves an escalation).
"""

import asyncio
import dataclasses
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pymupdf
from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_event
from app.checks.citations.history_review import citation_metadata_review_warning
from app.checks.escalation import (
    NEEDS_REVIEW_OUTCOMES,
    EscalatedItem,
    list_escalated,
    resolve_escalation,
)
from app.checks.reuse.embed import PassageEmbedding, compute_passage_embeddings, split_context
from app.checks.reuse.query import (
    canonical_library_copies,
    comparison_exclusions,
    passage_archive_size,
    query_similar_passages,
)
from app.checks.reuse.source import passage_match_supported
from app.checks.support import SOURCE_SCOPE_POLICY_VERSION
from app.config import Settings, get_settings
from app.errors import ConflictError, GoneError, NotFoundError
from app.findings.policy import exclusion_reason, reuse_excerpt
from app.ingest.figures import saved_figure_readings
from app.ingest.regions import recover_region
from app.ingest.schemas import SectionTree
from app.ingest.service import load_raw_store, load_raw_store_async
from app.library.passage_indexing import project_indexing_summary
from app.models.audit import AuditLog
from app.models.enums import (
    CheckKind,
    CheckRunStatus,
    FlagSeverity,
    ReadinessStatus,
    ReportDecision,
    ResultOutcome,
    RubricParseStatus,
)
from app.models.manuscript import Manuscript, ManuscriptPassageArchive
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun, Flag, ReadinessReport
from app.report.assessment import assessment_write, lock_assessment
from app.report.document_projection import project_docx_source
from app.report.docx_images import manuscript_docx_image_for
from app.report.module_scope import integrity_modules
from app.report.page_images import build_page_evidence_images
from app.report.rating import compute_levelled_rating
from app.report.rule_facts import project_rule_facts, project_rule_scope_unconfirmed
from app.report.schemas import (
    COVERAGE_REASONS,
    CriterionLevelOut,
    CriterionResultOut,
    DocumentParagraphsOut,
    EscalatedItemOut,
    EvidenceItem,
    ExcludedReuseMatchOut,
    FlagRegionOut,
    FlagSummaryOut,
    IntegrityCheckStatusOut,
    LevelledRatingOut,
    ManuscriptViewerOut,
    OriginalityScopeOut,
    PageEvidenceImageOut,
    ReportExportData,
    ReportOut,
    ResolutionOut,
    ResolveEscalationOut,
    ReuseMatchesOut,
    RubricLevelOut,
)
from app.report.scoring import (
    ScorableFlag,
    ScorableResult,
    flag_ai_verdict_summary,
    score_check_run,
    scoring_result_as_dict,
)
from app.report.source_coverage import project_source_coverage
from app.report.source_files import manuscript_source_file, open_source_pdf, readable_source_file
from app.report.statistical_scope import statistical_check_scope, unassessed_descriptive_rows
from app.report.subject_identity import recorded_check_subject
from app.report.weight_importance import WeightImportance, weight_importance
from app.storage.access import read_source


async def _load_scorable_results(session: AsyncSession, check_run_id: int) -> list[ScorableResult]:
    rows = (
        await session.execute(
            select(CheckResult, Criterion.weight)
            .join(Criterion, Criterion.id == CheckResult.criterion_id)
            .where(CheckResult.check_run_id == check_run_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    return [
        ScorableResult(
            criterion_id=result.criterion_id,
            weight=float(weight),
            outcome=result.outcome,
            score=float(result.score) if result.score is not None else None,
        )
        for result, weight in rows
    ]


async def _load_scorable_flags(session: AsyncSession, check_run_id: int) -> list[ScorableFlag]:
    rows = (
        await session.execute(
            select(Flag, CheckResult)
            .join(CheckResult, CheckResult.id == Flag.check_result_id)
            .where(CheckResult.check_run_id == check_run_id)
            .execution_options(populate_existing=True)
        )
    ).all()

    # BUG-150: the same criterion-text lookup `flags_for_check_run` already
    # does for the report's own flags panel, needed here too so the verdict
    # gate's finding-identity clustering agrees with what the instructor sees.
    criterion_ids = {r.criterion_id for _, r in rows if r.criterion_id is not None}
    criteria_by_id: dict[int, Criterion] = {}
    if criterion_ids:
        criteria = (
            await session.execute(select(Criterion).where(Criterion.id.in_(criterion_ids)))
        ).scalars()
        criteria_by_id = {c.id: c for c in criteria}

    return [
        ScorableFlag(
            severity=flag.severity,
            overridden=flag.overridden,
            # Same `flag.detail or result.detail` precedence `_to_flag_out`
            # uses (a semantic-grading flag, V-020, has no `Flag.detail` of
            # its own and falls back to its check_result's) — computing
            # this two different ways in two places is exactly the
            # duplication that let "999%"/"1082%" ship as two disagreeing
            # numbers at once (Track D's Critical 2, D-023).
            has_verdict=(
                flag_ai_verdict_summary(flag.detail or result.detail) is not None
                and exclusion_reason(result.kind.value, flag.detail or result.detail) is None
            ),
            id=flag.id,
            check_kind=result.kind.value,
            problem_kind=(flag.detail or {}).get("kind"),
            matched_ref=(flag.detail or {}).get("matched_manuscript_id"),
            criterion_text=(
                criteria_by_id[result.criterion_id].text
                if result.criterion_id in criteria_by_id
                else None
            ),
            evidence_excerpt=flag.evidence_excerpt,
        )
        for flag, result in rows
    ]


async def _assessment_incomplete(session: AsyncSession, check_run_id: int) -> bool:
    rows = (
        (await session.execute(select(CheckResult).where(CheckResult.check_run_id == check_run_id)))
        .scalars()
        .all()
    )
    incomplete = {
        ResultOutcome.escalated,
        ResultOutcome.unverifiable,
        ResultOutcome.api_down,
        ResultOutcome.quota_exhausted,
    }
    return any(
        (row.kind == CheckKind.internal_agreement and row.outcome == ResultOutcome.passed)
        or (
            row.kind == CheckKind.statistical_forensics
            and unassessed_descriptive_rows(row.detail) != 0
        )
        or row.outcome in incomplete
        and (
            row.criterion_id is not None
            or row.kind
            in {
                CheckKind.internal_agreement,
                CheckKind.citation_integrity,
                CheckKind.statistical_forensics,
            }
        )
        for row in rows
    )


async def aggregate_and_score(
    session: AsyncSession, check_run_id: int, settings: Settings | None = None, *, commit: bool
) -> ReadinessReport:
    settings = settings or get_settings()
    results = await _load_scorable_results(session, check_run_id)
    flags = await _load_scorable_flags(session, check_run_id)
    scoring = score_check_run(
        results,
        flags,
        settings,
        assessment_incomplete=await _assessment_incomplete(session, check_run_id),
    )

    report = await session.scalar(
        select(ReadinessReport).where(ReadinessReport.check_run_id == check_run_id)
    )
    composite = (
        Decimal(str(round(scoring.composite_score, 2)))
        if scoring.composite_score is not None
        else None
    )
    if report is None:
        report = ReadinessReport(
            check_run_id=check_run_id, composite_score=composite, status=scoring.status
        )
        session.add(report)
    else:
        report.composite_score = composite
        report.status = scoring.status
    # BUG-151: a verdict-derivation event, so the number on the report
    # screen is traceable to something (charter judgment 4) -- pairs with
    # BUG-150's own open question about the deduction cap. Written on a
    # genuine re-aggregation (a flag override, an escalation resolution
    # changed the number), not just the first -- but NOT unconditionally on
    # every call: `aggregate_and_score` recomputes from scratch every time,
    # including on pipeline resume after a crash between this function's
    # own commit and the stage-status advance that follows it
    # (`pipeline/machine.py`'s `_transition_after_boundary`), which would
    # otherwise write a second, indistinguishable row for identical input
    # (backend review finding, live-reproduced: two calls with nothing
    # changed produced two byte-identical audit rows). Every F4-F7 check
    # already guards its own write this way via `existing_*_result`
    # idempotency checks; this compares against the run's own last recorded
    # verdict instead, since a report row (unlike a check_result) is
    # legitimately re-written every call regardless of whether anything
    # changed.
    new_verdict_payload = {
        "composite_score": scoring.composite_score,
        "status": scoring.status.value,
        "reason": scoring.reason,
        "thresholds": scoring.thresholds,
        "flag_deduction": scoring.flag_deduction,
        "unresolved_high_flag_count": scoring.unresolved_high_flag_count,
    }
    last_verdict_payload = await session.scalar(
        select(AuditLog.payload)
        .where(AuditLog.check_run_id == check_run_id, AuditLog.event_type == "verdict_computed")
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    if last_verdict_payload != new_verdict_payload:
        await write_audit_event(
            session,
            event_type="verdict_computed",
            check_run_id=check_run_id,
            payload=new_verdict_payload,
        )
    if commit:
        await session.commit()
    else:
        await session.flush()
    await session.refresh(report)
    return report


async def build_report_payload(
    session: AsyncSession, check_run_id: int, settings: Settings | None = None
) -> dict[str, Any]:
    """Recomputes the full explainable breakdown fresh from the current
    check_results/flags (V-020's `GET /runs/{id}/report` calls this) — the
    SAME pure function that produced the persisted composite/status, so
    the two can never silently disagree."""
    settings = settings or get_settings()
    results = await _load_scorable_results(session, check_run_id)
    flags = await _load_scorable_flags(session, check_run_id)
    return scoring_result_as_dict(
        score_check_run(
            results,
            flags,
            settings,
            assessment_incomplete=await _assessment_incomplete(session, check_run_id),
        )
    )


def _to_criterion_result(
    result: CheckResult,
    criterion: Criterion,
    *,
    importance: WeightImportance,
) -> CriterionResultOut:
    detail = result.detail or {}
    evidence = [EvidenceItem(**item) for item in detail.get("evidence", [])]
    resolution_detail = detail.get("resolution")
    resolution = (
        ResolutionOut(
            type=resolution_detail["type"],
            reason=resolution_detail["reason"],
            ai_majority_verdict=resolution_detail.get("ai_majority_verdict"),
        )
        if resolution_detail
        else None
    )
    return CriterionResultOut(
        rule_scope_unconfirmed=project_rule_scope_unconfirmed(
            detail, criterion=criterion, kind=result.kind.value, outcome=result.outcome.value
        ),
        source_coverage=project_source_coverage(detail.get("source_coverage")),
        rule_facts=project_rule_facts(
            detail,
            criterion_type=criterion.type.value,
            kind=result.kind.value,
            outcome=result.outcome.value,
        ),
        check_result_id=result.id,
        judgment_revision=result.judgment_revision,
        criterion_id=criterion.id,
        text=criterion.text,
        type=criterion.type.value,
        weight=float(criterion.weight),
        weight_importance=importance,
        kind=result.kind.value,
        outcome=result.outcome.value,
        score=float(result.score) if result.score is not None else None,
        basis=detail.get("basis"),
        anchor=detail.get("anchor"),
        reasoning=detail.get("reasoning"),
        reason=detail.get("reason"),
        evidence=evidence,
        resolution=resolution,
        # V-069 AC2: `detail["level"]` is set by `checks/semantic.py`,
        # `checks/consistency.py`, and `checks/escalation.resolve_escalation`
        # for a levelled criterion's decided result — `None` otherwise.
        level=CriterionLevelOut(**detail["level"]) if detail.get("level") else None,
    )


def _statistical_notation_state(detail: dict | None) -> bool | None:
    """An absent historical inventory cannot establish an examined empty set."""
    detail = detail or {}
    ledger = detail.get("statistical_notation_coverage")
    value = detail.get("statistical_notation_unassessed")
    if (
        not isinstance(ledger, dict)
        or ledger.get("version") != "v1"
        or not isinstance(ledger.get("items"), list)
        or type(value) is not bool
        or value != bool(ledger["items"])
    ):
        return None
    return value


def _integrity_skip_count(kind: CheckKind, detail: dict[str, Any] | None, cause: str) -> int:
    """BUG-125: F4's own `detail` (agreement/service.py) keys skip counts
    `n_skipped_{cause}`; F5's (citations/verify.py) scopes the same three
    causes to its claim-support sub-check, `n_claim_support_skipped_{cause}`
    -- same three causes (BUG-072), two key prefixes. Branches on `kind`
    explicitly (backend review finding) rather than trying both keys and
    taking whichever is truthy: that fallback would silently return 0 for
    a future check kind whose `detail` used neither naming convention,
    instead of surfacing the gap."""
    detail = detail or {}
    if kind in {CheckKind.internal_agreement, CheckKind.statistical_forensics}:
        key = f"n_skipped_{cause}"
    elif kind == CheckKind.citation_integrity:
        key = f"n_claim_support_skipped_{cause}"
    else:
        return 0
    return detail.get(key, 0)


async def _owned_check_run(
    session: AsyncSession, check_run_id: int, instructor_id: int
) -> CheckRun:
    check_run = await session.scalar(
        select(CheckRun)
        .join(Manuscript, Manuscript.id == CheckRun.manuscript_id)
        .where(CheckRun.id == check_run_id, Manuscript.instructor_id == instructor_id)
    )
    if check_run is None:
        raise NotFoundError(f"No check run with id {check_run_id}.")
    return check_run


async def get_report(session: AsyncSession, check_run_id: int, instructor_id: int) -> ReportOut:
    """Screen 4h's data source (F8.1-F8.2): read-only in V2, gains
    escalation resolution in V-023 (this module's own
    `resolve_escalation_for_run`) and flag annotation/override in V-026.
    A run must have finished (`done`) before there's anything real to
    show — never a partial or guessed report."""
    check_run = await _owned_check_run(session, check_run_id, instructor_id)
    return await report_out_for_check_run(session, check_run)


async def attach_report_figures(
    session: AsyncSession, report: ReportOut, settings: Settings | None = None
) -> ReportOut:
    """Read immutable source diagnostics after the assessment snapshot is bound.

    Request owners release their assessment lock before this file work. Never
    re-read score, decision or revision while enriching an acknowledgment.
    """
    settings = settings or get_settings()
    manuscript = await session.get(Manuscript, report.manuscript_id, populate_existing=True)
    if manuscript is None:
        return report
    try:
        readings = saved_figure_readings(
            await read_source(
                session, manuscript, lambda: load_raw_store_async(settings, manuscript.id)
            ),
            manuscript.content_hash,
        )
    except (FileNotFoundError, ValidationError, OSError, GoneError):
        readings = None
    return report.model_copy(update={"figure_readings": readings})


async def report_out_for_check_run(
    session: AsyncSession, check_run: CheckRun, settings: Settings | None = None
) -> ReportOut:
    """V-040: the same report-assembly logic `get_report` uses, split out
    so the public share-link view (`app.share.service`, token-authorized
    instead of instructor-owned) can build the identical `ReportOut` a
    signed-in instructor sees -- one source of truth for what a report
    IS, two different authorization paths to reach it."""
    settings = settings or get_settings()
    check_run_id = check_run.id
    if check_run.status != CheckRunStatus.done:
        raise ConflictError("This check hasn't finished yet. Its report isn't ready.")

    manuscript = await session.get(Manuscript, check_run.manuscript_id)
    rubric = await session.get(Rubric, check_run.rubric_id)
    report = await lock_assessment(session, check_run_id)

    rows = (
        await session.execute(
            select(CheckResult, Criterion)
            .join(Criterion, Criterion.id == CheckResult.criterion_id)
            .where(CheckResult.check_run_id == check_run_id)
            .order_by(Criterion.position)
            .execution_options(populate_existing=True)
        )
    ).all()
    # D-023: weight is relative (scoring.py normalises, never a
    # must-total-100 rule) -- bucketed into Low/Medium/High relative to
    # this run's own equal-split average, never shown as a bare
    # percentage. `average_weight <= 0` can't happen in practice (every
    # persisted `Criterion.weight` is API-validated `> 0`, schemas.py),
    # but a run with zero criteria could reach this with an empty `rows`.
    average_weight = (
        sum(float(criterion.weight) for _, criterion in rows) / len(rows) if rows else 0.0
    )
    results = [
        _to_criterion_result(
            result,
            criterion,
            importance=weight_importance(
                float(criterion.weight), average_weight=average_weight, settings=settings
            ),
        )
        for result, criterion in rows
    ]

    scoring_payload = await build_report_payload(session, check_run_id)
    assessment_provenance = "current"
    if report is not None and report.decision is not None:
        # Preserve decided history, rather than replacing its recorded band
        # using a later eligibility policy. New checks use current eligibility.
        recorded = await session.scalar(
            select(AuditLog.payload)
            .where(
                AuditLog.check_run_id == check_run_id,
                AuditLog.event_type == "verdict_computed",
            )
            .order_by(AuditLog.id.desc())
            .limit(1)
        )
        if recorded:
            assessment_provenance = "recorded_decision"
            for key in ("thresholds", "flag_deduction", "unresolved_high_flag_count"):
                if key in recorded:
                    scoring_payload[key] = recorded[key]
            scoring_payload.update(
                status=report.status.value,
                composite_score=float(report.composite_score)
                if report.composite_score is not None
                else None,
                reason=(
                    "The readiness band recorded with the instructor's decision is preserved. "
                    "Assessment rules may differ in a new check."
                ),
            )
        else:
            assessment_provenance = "recomputed_without_recorded_breakdown"
            scoring_payload["reason"] = (
                "The instructor's decision is retained. Its recorded readiness band was "
                f"{report.status.value.replace('_', ' ')}. "
                "The original scoring breakdown is unavailable. "
                "The assessment detail shown here is "
                "recomputed under the current policy, not the original decision's calculation."
            )
    pending_review_count = await session.scalar(
        select(func.count())
        .select_from(CheckResult)
        .where(
            CheckResult.check_run_id == check_run_id,
            CheckResult.outcome.in_(NEEDS_REVIEW_OUTCOMES),
            # BUG-073 follow-up: an F4/F5 integrity check (criterion_id is
            # always None, ENGINEERING §2) can now also carry a
            # needs-review outcome (quota_exhausted/api_down, a partially-
            # executed run) -- but `list_escalated` INNER JOINs Criterion,
            # so an integrity check's row can never appear there to be
            # resolved. Without this filter, this count (and the decide
            # gate below) would say "N items need your review" for
            # something the review panel can never show or let the
            # instructor resolve -- a real deadlock, not just an
            # off-by-one, since the report could never be decided.
            CheckResult.criterion_id.isnot(None),
        )
    )

    # V-041: the most recent OTHER done+reported run for the SAME
    # manuscript UNDER THE SAME RUBRIC FAMILY (e.g. this run is a
    # re-check under a newer version of the same format) — the
    # version-comparison line's data source. backend review found live
    # that without the family filter, a manuscript checked against an
    # unrelated format in between two real versions would win the
    # comparison, manufacturing a "v1 -> v2" story that never happened
    # (two different formats' verdicts, not two versions of one). None
    # when this is the manuscript's first reported run under this family.
    previous = (
        await session.execute(
            select(ReadinessReport.status, ReadinessReport.composite_score)
            .join(CheckRun, CheckRun.id == ReadinessReport.check_run_id)
            .join(Rubric, Rubric.id == CheckRun.rubric_id)
            .where(
                CheckRun.manuscript_id == check_run.manuscript_id,
                CheckRun.id < check_run_id,
                CheckRun.status == CheckRunStatus.done,
                Rubric.rubric_family_id == rubric.rubric_family_id,
            )
            .order_by(CheckRun.id.desc())
            .limit(1)
        )
    ).first()

    # BUG-125: an F4/F5 CheckResult that didn't fully execute -- BUG-073
    # made the outcome honest, this is the still-missing on-screen
    # disclosure. criterion_id is always None for these (F4/F5 aren't
    # rubric criteria, ENGINEERING §2). F4's own `not_applicable` case ("no
    # objective/finding-style statements detected") is a legitimate
    # terminal state, not a partial run, so it's deliberately excluded.
    # The explicit `kind` filter (backend review finding) matters beyond
    # today's data: `IntegrityCheckStatusOut.check_kind` is a closed
    # frontend union of exactly these two kinds, and `_integrity_skip_count`
    # only knows these two `detail` key conventions -- without this filter,
    # a future criterion_id=None/partial-execution outcome from a different
    # check (F6/F7) would silently reach both as an unrecognised value.
    forensics_rows = list(
        (
            await session.scalars(
                select(CheckResult).where(
                    CheckResult.check_run_id == check_run_id,
                    CheckResult.kind == CheckKind.statistical_forensics,
                )
            )
        ).all()
    )
    integrity_rows = (
        (
            await session.execute(
                select(CheckResult).where(
                    CheckResult.check_run_id == check_run_id,
                    CheckResult.criterion_id.is_(None),
                    CheckResult.kind.in_(
                        (
                            CheckKind.internal_agreement,
                            CheckKind.citation_integrity,
                            CheckKind.statistical_forensics,
                            CheckKind.originality_reuse,
                        )
                    ),
                    or_(
                        and_(
                            CheckResult.kind == CheckKind.internal_agreement,
                            CheckResult.outcome == ResultOutcome.passed,
                        ),
                        CheckResult.outcome.in_(
                            (
                                ResultOutcome.escalated,
                                ResultOutcome.unverifiable,
                                ResultOutcome.api_down,
                                ResultOutcome.quota_exhausted,
                            )
                        ),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    integrity_row_ids = {row.id for row in integrity_rows}
    integrity_rows.extend(
        row for row in forensics_rows
        if unassessed_descriptive_rows(row.detail) != 0 and row.id not in integrity_row_ids
    )
    integrity_check_status = [
        IntegrityCheckStatusOut(
            check_kind=row.kind.value,
            outcome="escalated"
            if row.outcome == ResultOutcome.passed and (
                row.kind == CheckKind.internal_agreement
                or row.kind == CheckKind.statistical_forensics
                and unassessed_descriptive_rows(row.detail) != 0
            )
            else row.outcome.value,
            n_skipped_quota=_integrity_skip_count(row.kind, detail, "quota"),
            n_skipped_api_down=_integrity_skip_count(row.kind, detail, "api_down"),
            n_skipped_parse_failure=_integrity_skip_count(row.kind, detail, "parse_failure"),
            coverage_reason=(
                detail.get("coverage_reason")
                if detail.get("coverage_reason") in COVERAGE_REASONS
                else "statement_roles_unverified"
                if row.kind == CheckKind.internal_agreement
                else None
            ),
            n_metadata_skipped_insufficient=detail.get(
                "n_metadata_skipped_insufficient", 0
            ),
            n_linkage_unassessed=detail.get("n_linkage_unassessed", 0),
            n_source_pages_unassessed=detail.get("n_source_pages_unassessed", 0),
            n_claim_support_unassessed=detail.get("n_claim_support_unassessed", 0),
            statistical_notation_unassessed=_statistical_notation_state(detail)
            if row.kind == CheckKind.statistical_forensics
            else None,
            reference_list_page=detail.get("reference_list_page"),
            analysis_coverage=detail.get("analysis_coverage")
            if detail.get("analysis_coverage") in {"complete", "partial", "unsupported"}
            else None,
            assessment_scope=detail.get("assessment_scope")
            if detail.get("assessment_scope")
            in {
                "reference_metadata_only",
                "available_text_and_tables",
                "available_text_and_reference_metadata",
            }
            else None,
            n_reference_metadata_attempted=detail.get(
                "n_reference_metadata_attempted", 0
            ),
            n_metadata_api_down=detail.get("n_metadata_api_down", 0),
            n_images_unassessed=detail.get("n_images_unassessed", 0),
            n_image_occurrences_omitted=detail.get("n_image_occurrences_omitted", 0),
            n_equations_unassessed=detail.get("n_equations_unassessed", 0),
            n_recovery_figures_unassessed=detail.get(
                "n_recovery_figures_unassessed", 0
            ),
            n_recovery_tables_unassessed=detail.get("n_recovery_tables_unassessed", 0),
            image_coverage_items=[
                {key: item.get(key) for key in ("image_index", "page", "paragraph", "reason")}
                for item in detail.get("image_coverage_items", [])
            ],
            native_math_inventory_state=detail.get("native_math_inventory_state"),
            n_source_candidates_withheld=detail.get("n_source_candidates_withheld", 0),
            n_native_equations_unassessed=detail.get("n_native_equations_unassessed"),
            n_native_equations_unlocated=detail.get("n_native_equations_unlocated"),
            native_math_excluded_paragraphs=detail.get(
                "native_math_excluded_paragraphs", []
            ),
            native_math_coverage_items=[
                {key: item.get(key) for key in ("paragraph", "kind", "n_occurrences")}
                for item in detail.get("native_math_coverage_items", [])
            ],
        )
        for row in integrity_rows
        for detail in [row.detail if isinstance(row.detail, dict) else {}]
    ]
    # Keep source and statement-role limits intact. AI capability is a separate
    # private disclosure in the existing status shape, without repeated counts
    # or source/native-inventory fields from the primary record.
    for row in integrity_rows:
        if row.kind not in {CheckKind.internal_agreement, CheckKind.citation_integrity}:
            continue
        for cause in ("ai_not_configured", "ai_key_rejected"):
            count = _integrity_skip_count(row.kind, row.detail, cause)
            if type(count) is not int or count <= 0:
                continue
            if (row.detail or {}).get("coverage_reason") == cause:
                continue
            integrity_check_status.append(
                IntegrityCheckStatusOut(
                    check_kind=row.kind.value,
                    outcome=row.outcome.value,
                    coverage_reason=cause,
                    n_skipped_quota=0,
                    n_skipped_api_down=0,
                    n_skipped_parse_failure=0,
                )
            )

    # V-077 (D-028): what the originality check compared against. Read from
    # the run's own F7 result, never recomputed from today's Library size,
    # so an old report keeps describing the comparison it actually made.
    originality_row = await session.scalar(
        select(CheckResult).where(
            CheckResult.check_run_id == check_run_id,
            CheckResult.kind == CheckKind.originality_reuse,
        )
    )
    originality_scope = None
    if originality_row is not None:
        originality_detail = originality_row.detail or {}
        compared = originality_detail.get("archive_size_n")
        ran = originality_detail.get("comparison_ran", "note" not in originality_detail)
        originality_scope = OriginalityScopeOut(
            library_passage_indexing=(
                summary.model_dump(mode="json")
                if (
                    summary := project_indexing_summary(
                        originality_detail.get("library_passage_indexing")
                    )
                )
                is not None
                else None
            ),
            compared_count=compared if isinstance(compared, int) and ran else None,
            corpus="library" if originality_detail.get("corpus") == "library" else "pre_library",
            not_compared_reason=(
                "source_unresolved"
                if originality_detail.get("coverage_reason") == "source_reading_order_unresolved"
                else "no_comparison_archive"
                if originality_detail.get("coverage_reason") == "no_comparison_archive"
                else None
                if ran
                else "no_text"
            ),
        )

    # V-069 AC2: `None` whenever no criterion in `rows` is levelled (AC3 —
    # a pass/fail rubric's report gains no new visible field). Reuses the
    # SAME `rows` this function already loaded, never a second query.
    levelled_rating_result = compute_levelled_rating(
        [criterion for _, criterion in rows], {result.criterion_id: result for result, _ in rows}
    )
    levelled_rating = (
        LevelledRatingOut(
            achieved_points=levelled_rating_result.achieved_points,
            max_points=levelled_rating_result.max_points,
            rating_percent=levelled_rating_result.rating_percent,
            n_decided=levelled_rating_result.n_decided,
            n_levelled=levelled_rating_result.n_levelled,
        )
        if levelled_rating_result is not None and levelled_rating_result.n_decided > 0
        else None
    )

    citation_flag_rows = (
        await session.execute(
            select(Flag, CheckResult)
            .join(CheckResult, Flag.check_result_id == CheckResult.id)
            .where(
                CheckResult.check_run_id == check_run_id,
                CheckResult.kind == CheckKind.citation_integrity,
            )
        )
    ).all()
    forensics_row = forensics_rows[0] if forensics_rows else None
    return ReportOut(
        historical_native_math_unverified=(
            check_run.status == CheckRunStatus.done
            and (
                forensics_row is None
                or not isinstance(forensics_row.detail, dict)
                or forensics_row.detail.get("native_math_coverage_version") != "v1"
            )
        ),
        historical_citation_title_unverified=any(
            citation_metadata_review_warning(
                "citation_integrity", flag.evidence_excerpt, flag.detail or result.detail or {}
            )
            for flag, result in citation_flag_rows
        ),
        assessment_revision=report.assessment_revision if report is not None else None,
        assessment_provenance=assessment_provenance,
        historical_semantic_support_unverified=any(
            result.kind == CheckKind.semantic
            and (result.detail or {}).get("basis") == "llm"
            and (result.detail or {}).get("support_check", {}).get("source_scope_policy")
            != SOURCE_SCOPE_POLICY_VERSION
            for result, _criterion in rows
        ),
        check_run_id=check_run_id,
        subject=await recorded_check_subject(session, check_run, manuscript),
        integrity_modules=integrity_modules(
            list((await session.scalars(select(CheckResult).where(
                CheckResult.check_run_id == check_run_id,
                CheckResult.criterion_id.is_(None),
            ))).all()),
            {status.check_kind for status in integrity_check_status},
        ),
        manuscript_id=check_run.manuscript_id,
        manuscript_group_label=manuscript.group_label,
        manuscript_original_filename=manuscript.original_filename,
        rubric_title=rubric.title,
        status=scoring_payload["status"],
        composite_score=scoring_payload["composite_score"],
        thresholds=scoring_payload["thresholds"],
        reason=scoring_payload["reason"],
        flag_deduction=scoring_payload["flag_deduction"],
        unresolved_high_flag_count=scoring_payload["unresolved_high_flag_count"],
        llm_mode=check_run.llm_mode.value,
        results=results,
        decision=report.decision.value if report is not None and report.decision else None,
        decided_at=report.decided_at if report is not None else None,
        decision_note=report.decision_note if report is not None else None,
        pending_review_count=pending_review_count or 0,
        rubric_is_current=rubric.is_active,
        rubric_needs_review=rubric.parse_status == RubricParseStatus.needs_review,
        rubric_parse_issues=rubric.parse_issues,
        previous_status=previous.status.value if previous is not None else None,
        previous_composite_score=(
            float(previous.composite_score)
            if previous is not None and previous.composite_score is not None
            else None
        ),
        integrity_check_status=integrity_check_status,
        statistical_check_scope=statistical_check_scope(
            [row.outcome for row in forensics_rows], [row.detail for row in forensics_rows]
        ),
        originality_scope=originality_scope,
        levelled_rating=levelled_rating,
    )


def _to_escalated_out(
    item: EscalatedItem, assessment_revision: int | None = None
) -> EscalatedItemOut:
    return EscalatedItemOut(
        assessment_revision=assessment_revision,
        check_result_id=item.check_result_id,
        criterion_id=item.criterion_id,
        criterion_text=item.criterion_text,
        weight=item.weight,
        agreement=item.agreement,
        votes=item.votes,
        ai_majority_verdict=item.detail.get("verdict"),
        verdict_unrecognized=bool(item.detail.get("verdict_unrecognized")),
        support_withheld=bool(item.detail.get("support_withheld")),
        support_status=item.detail.get("support_check", {}).get("status"),
        evidence=[EvidenceItem(**entry) for entry in item.detail.get("evidence", [])],
        reasoning=item.detail.get("reasoning"),
        reason=item.reason,
        review_reason=item.review_reason,
        unverified_evidence=item.unverified_evidence,
        injection_suspected=item.injection_suspected,
        injection_matched_snippet=item.injection_matched_snippet,
        levels=[RubricLevelOut(**lvl) for lvl in item.levels] if item.levels else None,
    )


async def list_escalated_for_run(
    session: AsyncSession, check_run_id: int, instructor_id: int
) -> list[EscalatedItemOut]:
    """Bind pending rows to a recorded assessment epoch, without source I/O."""
    try:
        await _owned_check_run(session, check_run_id, instructor_id)
        report = await lock_assessment(session, check_run_id)
        items = await list_escalated(session, check_run_id)
        out = [
            _to_escalated_out(item, report.assessment_revision if report is not None else None)
            for item in items
        ]
        await session.commit()
        return out
    except BaseException:
        await session.rollback()
        raise


# BUG-033/design review spec: fixed F4→F7 declaration order — flags never
# jump around the panel as severities change, matching this project's own
# recognition-over-recall precedent (Jakob's Law, DESIGN.md).
_CHECK_KIND_ORDER = {
    CheckKind.internal_agreement: 0,
    CheckKind.citation_integrity: 1,
    CheckKind.statistical_forensics: 2,
    CheckKind.originality_reuse: 3,
}
_SEVERITY_ORDER = {FlagSeverity.high: 0, FlagSeverity.med: 1, FlagSeverity.low: 2}


async def list_flags_for_run(
    session: AsyncSession, check_run_id: int, instructor_id: int
) -> list[FlagSummaryOut]:
    """The report's flags panel data source (BUG-033) — its own endpoint,
    not a field on `ReportOut`, mirroring `list_escalated_for_run`'s own
    precedent exactly (a sibling panel self-fetches; the common case of
    zero flags on a run never adds dead weight to every report fetch)."""
    await _owned_check_run(session, check_run_id, instructor_id)
    return await flags_for_check_run(session, check_run_id)


async def flags_for_check_run(session: AsyncSession, check_run_id: int) -> list[FlagSummaryOut]:
    """V-040: split out of `list_flags_for_run` for the same reason as
    `report_out_for_check_run` -- the public share-link view needs the
    identical flags list, reached through token authorization instead of
    instructor ownership."""
    rows = (
        await session.execute(
            select(Flag, CheckResult)
            .join(CheckResult, CheckResult.id == Flag.check_result_id)
            .where(CheckResult.check_run_id == check_run_id)
        )
    ).all()

    criterion_ids = {r.criterion_id for _, r in rows if r.criterion_id is not None}
    criteria_by_id: dict[int, Criterion] = {}
    if criterion_ids:
        criteria = (
            await session.execute(select(Criterion).where(Criterion.id.in_(criterion_ids)))
        ).scalars()
        criteria_by_id = {c.id: c for c in criteria}

    summaries = [
        FlagSummaryOut(
            citation_metadata_review_warning=citation_metadata_review_warning(
                result.kind.value, flag.evidence_excerpt, flag.detail or result.detail or {}
            ),
            id=flag.id,
            check_kind=result.kind.value,
            severity=flag.severity.value,
            criterion_text=(
                criteria_by_id[result.criterion_id].text
                if result.criterion_id in criteria_by_id
                else None
            ),
            evidence_excerpt=(
                reuse_excerpt(flag.detail or {}, flag.evidence_excerpt)
                if result.kind == CheckKind.originality_reuse
                else flag.evidence_excerpt
            ),
            page_anchor=flag.page_anchor,
            overridden=flag.overridden,
            affects_readiness=exclusion_reason(result.kind.value, flag.detail or result.detail)
            is None,
            readiness_exclusion_reason=exclusion_reason(
                result.kind.value, flag.detail or result.detail
            ),
            is_passage_level=bool((flag.detail or {}).get("kind", "").endswith("_passage")),
            first_upload_context=bool((flag.detail or {}).get("first_upload_context")),
            confirmed_citation_source=flag.confirmed_citation_source,
            problem_kind=(flag.detail or {}).get("kind"),
            matched_ref=(flag.detail or {}).get("matched_manuscript_id"),
        )
        for flag, result in rows
    ]
    return sorted(
        summaries,
        key=lambda f: (
            _CHECK_KIND_ORDER.get(CheckKind(f.check_kind), len(_CHECK_KIND_ORDER)),
            f.overridden,
            _SEVERITY_ORDER.get(FlagSeverity(f.severity), len(_SEVERITY_ORDER)),
            f.id,
        ),
    )


async def get_manuscript_viewer(
    session: AsyncSession, check_run_id: int, instructor_id: int, settings: Settings | None = None
) -> ManuscriptViewerOut:
    """V-065 AC1/2/7: what the manuscript-viewer screen loads. Opens ONLY
    the current manuscript's own file — F7 flags anchored to a chapter
    title always name `match.own_chapter_title` (`app/checks/reuse/
    service.py`), never the matched manuscript's, so there is never a
    reason for this endpoint to touch another instructor's file (the same
    boundary BUG-050/BUG-097 established for evidence_excerpt)."""
    settings = settings or get_settings()
    check_run = await _owned_check_run(session, check_run_id, instructor_id)
    manuscript = await session.get(Manuscript, check_run.manuscript_id)

    flags = (
        (
            await session.execute(
                select(Flag)
                .join(CheckResult, CheckResult.id == Flag.check_result_id)
                .where(CheckResult.check_run_id == check_run_id)
            )
        )
        .scalars()
        .all()
    )
    return await read_source(
        session,
        manuscript,
        lambda: manuscript_viewer_for(manuscript, flags, settings),
        allow_purged=True,
    )


def _open_manuscript_source(
    manuscript: Manuscript, settings: Settings
) -> tuple[pymupdf.Document | None, Literal["pdf", "docx", "unknown"], str | None]:
    """Shared file-opening/purge-guard logic behind BOTH the manuscript
    viewer (V-065, `manuscript_viewer_for` below) and the PDF export's
    page-image rendering (V-070, `app.report.page_images` -- same Q5
    sharing `regions.py` already established between those two tickets).
    Extracted from `manuscript_viewer_for`'s own prior inline steps so the
    purge/format/open-failure wording is stated in exactly one place, not
    two independently-maintained copies (the drift class BUG-032's history
    warns against).

    Returns `(doc-or-None, source_format, reason-or-None)`. `reason` is set
    ONLY for the three HARD-unavailable cases (purged, unsupported format,
    a PDF that won't open) -- a DOCX source returns `(None, "docx", None)`:
    no doc to open, but that is a normal, available manuscript, not an
    error. Callers own closing `doc`."""
    source_format: Literal["pdf", "docx", "unknown"] = "unknown"
    if manuscript.purged_at is not None:
        return (
            None,
            source_format,
            "This manuscript's source file was deleted and can no longer be viewed.",
        )
    try:
        # BUG-138: re-populates the local cache from durable storage first
        # if Render's ephemeral disk lost it since upload -- the honest
        # "could not be opened" reason below now only fires for a
        # genuinely-gone file (pre-migration rows), not every file past the
        # next redeploy.
        local_path, source_format = manuscript_source_file(manuscript, settings)
        if source_format == "docx":
            return None, source_format, None
        doc = open_source_pdf(local_path)
    except (GoneError, ConflictError) as exc:
        return None, source_format, str(exc)
    except (
        FileNotFoundError,
        pymupdf.FileDataError,
        pymupdf.FileNotFoundError,
        RuntimeError,
        ValueError,
        OSError,
    ):
        return None, source_format, "This manuscript's source file could not be opened."
    return doc, source_format, None


def _open_manuscript_pdf_or_none(
    manuscript: Manuscript, settings: Settings
) -> pymupdf.Document | None:
    """`list_reuse_passage_matches`'s own open -- same durable-storage
    fallback as `_open_manuscript_source`, but excluded-match rendering is a
    nice-to-have (the caller falls back to unavailable regions), so a
    failure here is silent rather than reasoned like the viewer's."""
    doc, _, _ = _open_manuscript_source(manuscript, settings)
    return doc


def _manuscript_export_source_format(
    manuscript: Manuscript, settings: Settings
) -> Literal["pdf", "docx", "unknown"]:
    doc, source_format, _ = _open_manuscript_source(manuscript, settings)
    if doc is not None:
        doc.close()
    return source_format


async def manuscript_viewer_for(
    manuscript: Manuscript, flags: list[Flag], settings: Settings
) -> ManuscriptViewerOut:
    """The check-run-independent core of `get_manuscript_viewer` (V-066:
    extracted so the library's own-manuscript document view, which has no
    check run and no flags at all, can open the identical file-opening/
    purge-guard logic instead of a second copy of it existing somewhere
    else -- the exact class of drift BUG-032's history warns against).
    Callers own resolving AND authorizing `manuscript` before this is ever
    called; this function does no ownership check of its own."""

    def _unavailable_regions() -> list[FlagRegionOut]:
        # §5.7 of the design review spec: one mechanism for "nothing to show"
        # -- every flag collapses to the same `unavailable` kind whether the
        # cause is a purge, an unsupported format, or an unopenable file, so
        # the frontend's evidence-section copy is consistent either way.
        return [
            FlagRegionOut(
                flag_id=flag.id,
                kind="unavailable",
                page=None,
                end_page=None,
                bbox=None,
                all_bboxes=[],
                paragraph=None,
                index=None,
            )
            for flag in flags
        ]

    # BUG-138 (backend review finding): `_open_manuscript_source` can now
    # do a real durable-storage network fetch on a cache miss, not just a
    # local read -- off the event loop, same as every other PyMuPDF-touching
    # call in this file (CODING.md's rule).
    doc, source_format, hard_reason = await asyncio.get_running_loop().run_in_executor(
        None, _open_manuscript_source, manuscript, settings
    )
    if hard_reason is not None:
        return ManuscriptViewerOut(
            manuscript_id=manuscript.id,
            original_filename=manuscript.original_filename,
            source_format=source_format,
            available=False,
            unavailable_reason=hard_reason,
            purged_at=manuscript.purged_at,
            page_count=None,
            regions=_unavailable_regions(),
        )

    section_tree = SectionTree.model_validate(manuscript.section_tree or {"source": "none"})
    page_count = doc.page_count if doc is not None else None

    try:
        regions = []
        for flag in flags:
            region = recover_region(
                doc, section_tree, flag.page_anchor, flag.evidence_excerpt, settings=settings
            )
            regions.append(
                FlagRegionOut(
                    flag_id=flag.id,
                    kind=region.kind,
                    page=region.page,
                    end_page=region.end_page,
                    bbox=region.bbox,
                    all_bboxes=list(region.all_bboxes),
                    paragraph=region.paragraph,
                    index=region.index,
                )
            )
    finally:
        if doc is not None:
            doc.close()

    return ManuscriptViewerOut(
        manuscript_id=manuscript.id,
        original_filename=manuscript.original_filename,
        source_format=source_format,
        available=True,
        unavailable_reason=None,
        purged_at=None,
        page_count=page_count,
        regions=regions,
    )


def render_flag_page_images_for(
    manuscript: Manuscript, flags: list[FlagSummaryOut], llm_mode: str, settings: Settings
) -> dict[int, PageEvidenceImageOut]:
    """V-070: the page-image half of the PDF export. Shares
    `_open_manuscript_source` with the manuscript viewer above -- same
    file-opening/purge-guard logic, never a second copy of it (V-066's own
    extraction rationale). Synchronous by design: PyMuPDF rendering is
    CPU-bound, so the caller (`get_report_export_data`) runs this through
    `run_in_executor` rather than blocking VERIDICAL's single event loop
    (CODING.md's PyMuPDF/embeddings rule; this export renders full pages
    via `get_pixmap`, materially heavier than the viewer's
    `search_for`-only path above)."""
    doc, source_format, unavailable_reason = _open_manuscript_source(manuscript, settings)
    section_tree = SectionTree.model_validate(manuscript.section_tree or {"source": "none"})
    try:
        return build_page_evidence_images(
            doc,
            section_tree,
            flags,
            source_format=source_format,
            unavailable_reason=unavailable_reason,
            llm_mode=llm_mode,
            settings=settings,
        )
    finally:
        if doc is not None:
            doc.close()


async def list_reuse_passage_matches(
    session: AsyncSession,
    check_run_id: int,
    instructor_id: int,
    *,
    include_reference_list: bool,
    include_block_quote: bool,
    settings: Settings | None = None,
) -> ReuseMatchesOut:
    """V-072 (F7.4), design review spec §4.2/§6: the exploration toggle's
    live data source. Uses THIS manuscript's own persisted passage rows when
    it is itself an active Library entry, otherwise recomputes them from
    its stored extraction (V-077: checks no longer write into the corpus).
    Returns only matches the DEFAULT policy would exclude (own or matched
    side is a reference-list/block-quote passage) — a match between two
    ordinary body passages is already a real, scored `Flag` and has no
    business appearing twice here. Never turns any of these into a `Flag`
    row (module docstring, `app/checks/reuse/query.py`)."""
    settings = settings or get_settings()
    check_run = await _owned_check_run(session, check_run_id, instructor_id)
    manuscript = await session.get(Manuscript, check_run.manuscript_id)

    # F7.4 exploration only returns policy-excluded passages. With neither
    # exclusion category requested, or no eligible comparison source, no
    # match can be returned; coverage must not depend on source/model access.
    exclude = await comparison_exclusions(session, manuscript.id)
    archive_size_n = await passage_archive_size(
        session, exclude_manuscript_ids=exclude, settings=settings
    )
    if not (include_reference_list or include_block_quote) or archive_size_n == 0:
        return ReuseMatchesOut(passage_archive_size_n=archive_size_n, matches=[])

    archive_rows = (
        (
            await session.execute(
                select(ManuscriptPassageArchive).where(
                    ManuscriptPassageArchive.manuscript_id == manuscript.id
                )
            )
        )
        .scalars()
        .all()
    )
    passages = [
        PassageEmbedding(
            passage_index=r.passage_index,
            chapter_index=r.chapter_index,
            anchor_kind="page" if r.page is not None else "paragraph",
            page=r.page,
            paragraph=r.paragraph,
            char_start=r.char_start,
            char_end=r.char_end,
            text=r.text,
            context_text=r.context_text,
            is_reference_list=r.is_reference_list,
            is_block_quote=r.is_block_quote,
            embedding=r.embedding,
        )
        for r in archive_rows
    ]
    if not passages:
        # V-077 (D-028): a checked manuscript is no longer written into the
        # corpus, so its own passages are recomputed from its stored
        # extraction (local model, deterministic, zero Gemini calls). A
        # Library manuscript re-checked from the dashboard still has stored
        # rows and takes the branch above.
        # A genuinely gone extraction (purged manuscript, pre-BUG-138 row)
        # leaves nothing to explore, the same empty state as before.
        try:
            extraction = await read_source(
                session, manuscript, lambda: load_raw_store_async(settings, manuscript.id)
            )
        except (FileNotFoundError, GoneError):
            extraction = None
        if extraction is not None:
            passages = await asyncio.get_running_loop().run_in_executor(
                None, compute_passage_embeddings, extraction, settings
            )

    query_result = await query_similar_passages(
        session,
        manuscript.id,
        passages,
        settings,
        include_reference_list=include_reference_list,
        include_block_quote=include_block_quote,
    )
    excluded = [
        m
        for m in query_result.matches
        if m.own_is_reference_list
        or m.own_is_block_quote
        or m.matched_is_reference_list
        or m.matched_is_block_quote
    ]
    # V-077 (AC6): byte-identical Library copies name one record here too,
    # exactly as they do in the check's own findings.
    remapped = await canonical_library_copies(
        session,
        {m.matched_manuscript_id for m in excluded},
        manuscript.instructor_id,
        exclude_manuscript_ids=await comparison_exclusions(session, manuscript.id),
    )
    if remapped:
        excluded = [
            dataclasses.replace(
                m,
                matched_manuscript_id=remapped[m.matched_manuscript_id][0],
                matched_instructor_id=remapped[m.matched_manuscript_id][1],
            )
            if m.matched_manuscript_id in remapped
            else m
            for m in excluded
        ]
    source_cache = {}
    supported_excluded = []
    for match in excluded:
        if await passage_match_supported(session, manuscript.id, match, settings, source_cache):
            supported_excluded.append(match)
    withheld = query_result.n_source_candidates_withheld + len(excluded) - len(supported_excluded)
    excluded = supported_excluded

    section_tree = SectionTree.model_validate(manuscript.section_tree or {"source": "none"})
    doc = None
    if manuscript.purged_at is None:
        # BUG-138 (backend review finding): off the event loop, same reason
        # as `_open_manuscript_source` above.
        doc = await read_source(
            session,
            manuscript,
            lambda: asyncio.to_thread(_open_manuscript_pdf_or_none, manuscript, settings),
            allow_purged=True,
            on_cancel=lambda opened: opened.close() if opened is not None else None,
        )

    try:
        results: list[ExcludedReuseMatchOut] = []
        for i, m in enumerate(excluded):
            own_anchor = f"p. {m.own_page}" if m.own_page is not None else f"¶{m.own_paragraph}"
            # Same OWN-side anchor→region mechanism every other flag
            # already uses (V-065's `recover_region`) — the excluded
            # match's real quotable text (`m.own_text`) is what lets it
            # find and highlight a precise box, not a templated sentence.
            region = recover_region(doc, section_tree, own_anchor, m.own_text, settings=settings)
            own_before, own_after = split_context(m.own_context_text, m.own_text)
            matched_before, matched_after = split_context(m.matched_context_text, m.matched_text)
            reasons: list[str] = []
            if m.own_is_reference_list or m.matched_is_reference_list:
                reasons.append("reference_list")
            if m.own_is_block_quote or m.matched_is_block_quote:
                reasons.append("block_quote")
            results.append(
                ExcludedReuseMatchOut(
                    id=f"excl-{i}",
                    own_excerpt=m.own_text,
                    own_context_before=own_before,
                    own_context_after=own_after,
                    own_region=FlagRegionOut(
                        # Synthetic, never a real Flag.id (module docstring
                        # above) — negative so it can never collide with a
                        # real flag_id, per the design review spec's own call.
                        flag_id=-(i + 1),
                        kind=region.kind,
                        page=region.page,
                        end_page=region.end_page,
                        bbox=region.bbox,
                        all_bboxes=list(region.all_bboxes),
                        paragraph=region.paragraph,
                        index=region.index,
                    ),
                    matched_ref=m.matched_manuscript_id,
                    matched_excerpt=m.matched_text,
                    matched_context_before=matched_before,
                    matched_context_after=matched_after,
                    context_words_each_side=settings.reuse_passage_context_words,
                    similarity=round(m.similarity, 3),
                    level=m.level,
                    excluded_reason=reasons,
                )
            )
    finally:
        if doc is not None:
            doc.close()

    return ReuseMatchesOut(
        passage_archive_size_n=query_result.passage_archive_size_n,
        matches=results,
        n_source_candidates_withheld=withheld,
    )


async def get_manuscript_file_path(
    session: AsyncSession, check_run_id: int, instructor_id: int, settings: Settings | None = None
) -> Path:
    """V-065 AC1: the raw-bytes half of the viewer, split from
    `get_manuscript_viewer`'s JSON metadata per the design review spec's Β§2.2
    -- streaming a file has no business being wrapped in a Pydantic model.
    PDF sources only (DOCX never reaches this endpoint; the frontend's
    text pane is served from `section_tree`, not raw bytes) and never a
    purged manuscript -- both are the router's job to enforce with the
    right HTTP status (410/404), not this function's."""
    settings = settings or get_settings()
    check_run = await _owned_check_run(session, check_run_id, instructor_id)
    manuscript = await session.get(Manuscript, check_run.manuscript_id)
    # BUG-138 (backend review finding): off the event loop -- a cache miss
    # now means a real durable-storage network fetch, not just a local read.
    return await read_source(
        session,
        manuscript,
        lambda: asyncio.to_thread(manuscript_file_path_for, manuscript, settings),
    )


def manuscript_file_path_for(manuscript: Manuscript, settings: Settings) -> Path:
    """The ownership-independent core of `get_manuscript_file_path` (V-066,
    same extraction rationale as `manuscript_viewer_for` above). Callers
    own resolving AND authorizing `manuscript` first.

    BUG-138: fetches from durable storage into the local cache first if
    Render's ephemeral disk lost it. BUG-139: an authorized, known manuscript
    whose source is absent from both storage layers is honestly gone; storage
    configuration and network failures retain their own exception types."""
    path, source_format = readable_source_file(manuscript, settings)
    if source_format != "pdf":
        raise ConflictError("This manuscript's source file isn't a PDF.")
    return path


async def get_manuscript_paragraphs(
    session: AsyncSession, check_run_id: int, instructor_id: int, settings: Settings | None = None
) -> DocumentParagraphsOut:
    """V-065 AC1 (DOCX gap): the reconstructed-text pane's actual content.
    DOCX sources only (mirrors `get_manuscript_file_path`'s PDF-only
    guard, the opposite direction) and never a purged manuscript. Reads
    the same persisted extraction `load_raw_store` already serves to the
    structural check engine (V-016) -- never a second extraction pass."""
    settings = settings or get_settings()
    check_run = await _owned_check_run(session, check_run_id, instructor_id)
    manuscript = await session.get(Manuscript, check_run.manuscript_id)
    # BUG-138 (backend review finding): off the event loop, same reason as
    # `get_manuscript_file_path` above (`load_raw_store` inside this can now
    # hit durable storage over the network on a cache miss).
    return await read_source(
        session,
        manuscript,
        lambda: asyncio.to_thread(manuscript_paragraphs_for, manuscript, settings),
    )


def manuscript_paragraphs_for(manuscript: Manuscript, settings: Settings) -> DocumentParagraphsOut:
    """The ownership-independent core of `get_manuscript_paragraphs`
    (V-066, same extraction rationale as `manuscript_viewer_for` above).
    Callers own resolving AND authorizing `manuscript` first."""
    _, source_format = manuscript_source_file(manuscript, settings)
    if source_format != "docx":
        raise ConflictError("This manuscript's source file isn't a DOCX.")
    # backend review finding (2026-08-22): this is the first caller of
    # `load_raw_store` reached directly from a synchronous HTTP request --
    # every other caller runs inside the pipeline worker, where BUG-032's
    # own history shows the same FileNotFoundError (DB says ingested, disk
    # raw-store is missing -- a backup/restore or migration skew) gets
    # silently swallowed by Starlette's BackgroundTask instead of crashing.
    # Here it would otherwise surface as a raw 500, not the honest
    # "why is this unavailable" state ground rule 3 asks for everywhere
    # else in this file (see `get_manuscript_viewer`'s PDF-open guard,
    # immediately above).
    try:
        extraction = load_raw_store(settings, manuscript.id)
    except (FileNotFoundError, ValidationError):
        raise GoneError(
            "This manuscript's extracted text could not be found and can no longer be read."
        ) from None
    section_tree = SectionTree.model_validate(manuscript.section_tree or {"source": "none"})
    return project_docx_source(extraction, manuscript.content_hash, section_tree, settings)


async def get_manuscript_docx_image(
    session: AsyncSession,
    check_run_id: int,
    instructor_id: int,
    image_index: int,
    settings: Settings,
) -> tuple[bytes, str]:
    check_run = await _owned_check_run(session, check_run_id, instructor_id)
    manuscript = await session.get(Manuscript, check_run.manuscript_id)
    return await read_source(
        session,
        manuscript,
        lambda: asyncio.to_thread(manuscript_docx_image_for, manuscript, image_index, settings),
    )


async def get_report_export_data(
    session: AsyncSession, check_run_id: int, instructor_id: int, settings: Settings | None = None
) -> ReportExportData:
    """V-039 (screen 4h export action): assembles everything the PDF
    export needs from the SAME data sources the on-screen report/flags
    panels already use -- never a second, divergent read path. Ownership
    is validated once, by `get_report` itself (raises `NotFoundError`);
    the two supplementary reads below trust that check.
    """
    settings = settings or get_settings()
    report = await get_report(session, check_run_id, instructor_id)
    flags = await list_flags_for_run(session, check_run_id, instructor_id)

    # F7's archive-size disclosure (V-037) lives on its own CheckResult
    # row (criterion_id=None, one per run) -- never surfaced in
    # ReportOut.results (that table is criteria-linked results only), so
    # it needs its own small read here.
    archive_check = await session.scalar(
        select(CheckResult).where(
            CheckResult.check_run_id == check_run_id,
            CheckResult.kind == CheckKind.originality_reuse,
        )
    )
    archive_size_n = (
        archive_check.detail.get("archive_size_n")
        if archive_check is not None
        and archive_check.detail
        # V-077: a check that found no text compared nothing, so the export
        # says it did not run rather than "compared against 0".
        and archive_check.detail.get("comparison_ran", "note" not in archive_check.detail)
        else None
    )

    # V-070: the actual submitted page, rendered, per page-anchored flag.
    # `render_flag_page_images_for` is synchronous PyMuPDF work (`get_pixmap`
    # over up to `export_max_page_images` full pages) -- run off the event
    # loop rather than blocking VERIDICAL's single uvicorn worker for the
    # duration of one export (CODING.md's PyMuPDF/embeddings rule).
    check_run = await _owned_check_run(session, check_run_id, instructor_id)
    manuscript = await session.get(Manuscript, check_run.manuscript_id)
    # Local imports avoid the flags -> report service dependency cycle. Project
    # the SAME persisted flag detail as the evidence screen, then an export-only
    # allowlist. In particular private annotations never reach ReportExportData.
    from app.flags.service import _to_flag_out
    from app.report.evidence_references import build_evidence_reference_snapshot
    from app.report.export_data import flag_export_detail, unassessed_references

    detail_rows = (
        await session.execute(
            select(Flag, CheckResult)
            .join(CheckResult, CheckResult.id == Flag.check_result_id)
            .where(CheckResult.check_run_id == check_run_id)
        )
    ).all()
    full_flags = [
        await _to_flag_out(
            session,
            flag,
            result,
            manuscript,
            check_run,
            assessment_revision=report.assessment_revision,
        )
        for flag, result in detail_rows
    ]
    flag_details = {
        flag.id: flag_export_detail(
            projected,
            recorded_detail=flag.detail or result.detail or {},
            recorded_excerpt=flag.evidence_excerpt,
        )
        for (flag, result), projected in zip(detail_rows, full_flags, strict=True)
    }
    membership = tuple(
        (
            await session.scalars(
                select(Flag.id)
                .join(CheckResult, CheckResult.id == Flag.check_result_id)
                .where(CheckResult.check_run_id == check_run_id)
                .order_by(Flag.id)
            )
        ).all()
    )
    evidence_snapshot = build_evidence_reference_snapshot(
        report,
        full_flags,
        expected_flag_ids=membership,
        source_sha256=manuscript.content_hash,
        audience="private",
    )
    citation_check = await session.scalar(
        select(CheckResult).where(
            CheckResult.check_run_id == check_run_id,
            CheckResult.kind == CheckKind.citation_integrity,
        )
    )
    reference_limits = unassessed_references(
        citation_check.detail or {} if citation_check is not None else {}
    )
    # The compound report/flags/detail snapshot is complete. This export request
    # owns its read transaction; release assessment locks before source rendering.
    await session.commit()
    report = await attach_report_figures(session, report, settings)
    from app.report.evidence_images import EvidenceSourceFile, build_recorded_evidence_images
    from app.report.export import source_image_print_width, source_image_settings_for

    async def render_evidence():
        source = EvidenceSourceFile(manuscript.file_ref, manuscript.purged_at)
        tree = SectionTree.model_validate(
            manuscript.section_tree or {"source": "none", "nodes": []}
        )
        return await asyncio.to_thread(
            lambda: build_recorded_evidence_images(
                source,
                evidence_snapshot,
                tree,
                settings,
                llm_mode=report.llm_mode,
                settings_for_reference=source_image_settings_for(report, settings),
                print_content_width=source_image_print_width(),
            )
        )

    source_evidence = await read_source(
        session,
        manuscript,
        render_evidence,
        allow_purged=True,
    )

    return ReportExportData(
        report=report,
        flags=flags,
        archive_size_n=archive_size_n,
        page_images={},
        source_evidence=source_evidence,
        flag_details=flag_details,
        unassessed_references=reference_limits,
        source_format=await read_source(
            session,
            manuscript,
            lambda: asyncio.to_thread(_manuscript_export_source_format, manuscript, settings),
            allow_purged=True,
        ),
    )


async def resolve_escalation_for_run(
    session: AsyncSession,
    check_run_id: int,
    check_result_id: int,
    instructor_id: int,
    resolution: str,
    reason: str,
    *,
    expected_revision: int,
    level: int | None = None,
) -> ResolveEscalationOut:
    """Save a pending judgment, audit and readiness as one versioned action."""
    async with assessment_write(session, check_run_id, instructor_id, expected_revision) as parent:
        result = await resolve_escalation(
            session,
            check_run_id,
            check_result_id,
            instructor_id,
            resolution,
            reason,
            level=level,
            commit=False,
            assessment_revision=parent.assessment_revision,
        )
        await aggregate_and_score(session, check_run_id, commit=False)
        report = await get_report(session, check_run_id, instructor_id)
        out = ResolveEscalationOut(
            check_result_id=result.id,
            outcome=result.outcome.value,
            score=float(result.score) if result.score is not None else None,
            report=report,
        )
    return out


async def _get_owned_report(
    session: AsyncSession, check_run_id: int, instructor_id: int
) -> ReadinessReport:
    """Shared precondition for decide/reopen: the run must be owned, done,
    and have a real persisted report (never a guessed/partial one — the
    same rule `get_report` itself enforces)."""
    check_run = await _owned_check_run(session, check_run_id, instructor_id)
    if check_run.status != CheckRunStatus.done:
        raise ConflictError("This check hasn't finished yet. Its report isn't ready.")
    report = await session.scalar(
        select(ReadinessReport).where(ReadinessReport.check_run_id == check_run_id)
    )
    if report is None:
        raise NotFoundError(f"No report for check run {check_run_id}.")
    return report


async def raise_if_decided(session: AsyncSession, check_run_id: int) -> None:
    """V-038 AC2 ("decided report immutable until reopened") only means
    something if it also binds every OTHER score-mutating action, not just
    a second decide — backend review found live that overriding a flag
    or resolving an escalation after decision would otherwise silently
    change the persisted composite/status on the SAME `ReadinessReport`
    row a human already signed off on, with no reopen and no audit trace
    of the drift. Called by `resolve_escalation_for_run` (this module) and
    `app.flags.service.override_flag` before either mutates anything —
    both already recompute via `aggregate_and_score` right after, which is
    exactly the write this guards."""
    report = await session.scalar(
        select(ReadinessReport).where(ReadinessReport.check_run_id == check_run_id)
    )
    if report is not None and report.decision is not None:
        raise ConflictError(
            "This report has already been decided. Reopen it first before changing "
            "anything that affects its score."
        )


# BUG-095: every OTHER instructor-written justification on this product
# is required (overriding a flag, resolving an escalation, reopening a
# decision) -- the single highest-stakes, most panel-visible action
# (approving a manuscript for defense) was the one exception, optional
# even when it overrules the system's own computed verdict. Required only
# when the decision DISAGREES with what VERIDICAL computed -- that
# disagreement is exactly what ground rule 1 says the human is for, and
# is the one thing worth capturing. Left optional when decision and
# verdict agree, so a routine approval of an already-Ready manuscript
# isn't forced to invent a reason that doesn't exist.
#
# backend review finding (BUG-095 review, live-reproduced): `needs_review`
# was missing entirely -- that status means nothing was computed at all
# (composite_score is None, `scoring.py`'s own comment: "No criteria could
# be auto-scored"). There is no verdict to "agree" or "disagree" with, so
# EITHER decision on it is pure human judgment with zero AI signal behind
# it -- the case ground rule 1 most wants a reason recorded for, and the
# one case that was sailing through with none.
DECISIONS_REQUIRING_A_REASON: frozenset[tuple[ReportDecision, ReadinessStatus]] = frozenset(
    {
        (ReportDecision.approved, ReadinessStatus.not_ready),
        (ReportDecision.approved, ReadinessStatus.conditionally_ready),
        (ReportDecision.approved, ReadinessStatus.needs_review),
        (ReportDecision.rejected, ReadinessStatus.ready),
        (ReportDecision.rejected, ReadinessStatus.needs_review),
    }
)


async def decide_report(
    session: AsyncSession,
    check_run_id: int,
    instructor_id: int,
    decision: str,
    note: str | None,
    settings: Settings | None = None,
    *,
    expected_revision: int,
) -> ReportOut:
    """Record the instructor decision against the exact assessment reviewed.

    Return preserves unresolved criteria. Approval and rejection retain their
    pending-judgment gate. Every decision freezes subsequent score changes.
    """
    settings = settings or get_settings()
    try:
        decision_enum = ReportDecision(decision)
    except ValueError as error:
        raise ConflictError("Choose an available instructor decision.") from error
    async with assessment_write(session, check_run_id, instructor_id, expected_revision) as report:
        pending_rows = (
            await session.execute(
                select(CheckResult.id, CheckResult.criterion_id, CheckResult.outcome)
                .where(
                    CheckResult.check_run_id == check_run_id,
                    CheckResult.outcome.in_(NEEDS_REVIEW_OUTCOMES),
                    CheckResult.criterion_id.isnot(None),
                )
                .order_by(CheckResult.id)
            )
        ).all()
        if pending_rows and decision_enum != ReportDecision.returned:
            count = len(pending_rows)
            raise ConflictError(
                f"{count} criteri{'on' if count == 1 else 'a'} still need your review "
                "before approval or rejection. Resolve them first, or return for revision."
            )
        current_scoring = await build_report_payload(session, check_run_id, settings)
        current_status = ReadinessStatus(current_scoring["status"])
        stripped_note = note.strip() if note and note.strip() else None
        minimum = settings.resolution_reason_min_length
        requires_reason = (decision_enum == ReportDecision.returned and bool(pending_rows)) or (
            decision_enum,
            current_status,
        ) in DECISIONS_REQUIRING_A_REASON
        if requires_reason and (stripped_note is None or len(stripped_note) < minimum):
            if decision_enum == ReportDecision.returned:
                raise ConflictError(
                    "Explain why this report is being returned for revision "
                    f"in at least {minimum} characters."
                )
            raise ConflictError(
                f"This decision disagrees with VERIDICAL's own "
                f"{current_status.value.replace('_', ' ')} verdict. Explain why in at least "
                f"{minimum} characters, so the audit trail records the disagreement."
            )
        score = current_scoring["composite_score"]
        report.status = current_status
        report.composite_score = Decimal(str(round(score, 2))) if score is not None else None
        report.decision = decision_enum
        report.decided_at = await session.scalar(select(func.clock_timestamp()))
        report.decided_by_instructor_id = instructor_id
        report.decision_note = stripped_note
        await session.flush()
        await write_audit_event(
            session,
            event_type="verdict_computed",
            check_run_id=check_run_id,
            created_at=report.decided_at,
            payload={
                key: current_scoring[key]
                for key in (
                    "composite_score",
                    "status",
                    "reason",
                    "thresholds",
                    "flag_deduction",
                    "unresolved_high_flag_count",
                )
            },
        )
        await write_audit_event(
            session,
            event_type="report_decided",
            check_run_id=check_run_id,
            created_at=report.decided_at,
            payload={
                "instructor_id": instructor_id,
                "decision": decision,
                "note": stripped_note,
                "composite_score": float(report.composite_score)
                if report.composite_score is not None
                else None,
                "status": report.status.value,
                "assessment_revision_before": expected_revision,
                "assessment_revision": report.assessment_revision,
                "pending_criteria": [
                    {
                        "check_result_id": row.id,
                        "criterion_id": row.criterion_id,
                        "outcome": row.outcome.value,
                    }
                    for row in pending_rows
                ],
                "assessment": current_scoring,
            },
        )
        out = await get_report(session, check_run_id, instructor_id)
    return out


async def reopen_report(
    session: AsyncSession,
    check_run_id: int,
    instructor_id: int,
    reason: str,
    settings: Settings | None = None,
    *,
    expected_revision: int,
) -> ReportOut:
    """Explicitly reopen the reviewed decision without erasing its audit history."""
    minimum = (settings or get_settings()).resolution_reason_min_length
    if not reason or len(reason.strip()) < minimum:
        raise ConflictError(f"A reason of at least {minimum} characters is required to reopen.")
    async with assessment_write(
        session, check_run_id, instructor_id, expected_revision, allow_decided=True
    ) as report:
        if report.decision is None:
            raise ConflictError("This report hasn't been decided yet. There's nothing to reopen.")
        prior_decision = report.decision.value
        prior_note = report.decision_note
        prior_at = report.decided_at
        prior_actor = report.decided_by_instructor_id
        report.decision = None
        report.decided_at = None
        report.decided_by_instructor_id = None
        report.decision_note = None
        await write_audit_event(
            session,
            event_type="report_reopened",
            check_run_id=check_run_id,
            created_at=await session.scalar(select(func.clock_timestamp())),
            payload={
                "instructor_id": instructor_id,
                "reason": reason.strip(),
                "prior_decision": prior_decision,
                "prior_note": prior_note,
                "prior_decided_at": prior_at.isoformat() if prior_at else None,
                "prior_decided_by_instructor_id": prior_actor,
                "composite_score": float(report.composite_score)
                if report.composite_score is not None
                else None,
                "status": report.status.value,
                "assessment_revision_before": expected_revision,
                "assessment_revision": report.assessment_revision,
            },
        )
        await session.flush()
        out = await get_report(session, check_run_id, instructor_id)
    return out
