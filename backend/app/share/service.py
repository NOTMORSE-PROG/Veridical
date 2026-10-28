"""Read-only, tokenized report sharing (F8.7, V-040)."""

import hashlib
import secrets
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_event
from app.errors import GoneError, NotFoundError
from app.models.audit import AuditLog
from app.models.enums import CheckRunStatus
from app.models.manuscript import Manuscript
from app.models.run import CheckResult, CheckRun, Flag
from app.models.share import ShareLink
from app.pipeline.service import get_check_run
from app.report.assessment import lock_assessment
from app.report.schemas import PublicCriterionResultOut, PublicReportOut, ReportOut
from app.report.service import flags_for_check_run, report_out_for_check_run
from app.share.schemas import (
    PublicFindingDetailOut,
    PublicShareSnapshot,
    SharedReportOut,
    ShareLinkOut,
)

# backend review finding (P1, live-reproduced with 15 concurrent POSTs):
# "only one active link at a time" was previously check-then-act in the
# service layer only -- a real race a double-click or two open tabs can
# trigger. `share_link` carries a DB-level partial unique index
# (migration 0020) as the actual guarantee, but a first attempt at
# handling it with an optimistic retry loop alone still lost under real
# 15-way contention (this test's own run exhausted a 3-attempt budget
# and raised): a `SELECT ... FOR UPDATE` lock on the check_run row below
# serializes concurrent create/revoke calls for the same report instead,
# so the unique-index collision this retry loop guards against becomes
# the rare defensive fallback it was always meant to be, not the normal
# path under contention.
_MAX_CREATE_ATTEMPTS = 3


async def _lock_check_run(session: AsyncSession, check_run_id: int, instructor_id: int) -> None:
    locked = await session.scalar(
        select(CheckRun.id)
        .join(Manuscript, Manuscript.id == CheckRun.manuscript_id)
        .where(CheckRun.id == check_run_id, Manuscript.instructor_id == instructor_id)
        .with_for_update()
    )
    if locked is None:
        raise NotFoundError(f"No check run with id {check_run_id}.")


def _to_out(link: ShareLink) -> ShareLinkOut:
    return ShareLinkOut(
        token=link.token,
        check_run_id=link.check_run_id,
        created_at=link.created_at,
        expires_at=link.expires_at,
    )


async def _active_link(session: AsyncSession, check_run_id: int) -> ShareLink | None:
    return await session.scalar(
        select(ShareLink).where(
            ShareLink.check_run_id == check_run_id, ShareLink.revoked_at.is_(None)
        )
    )


async def _revoke_all_active(session: AsyncSession, check_run_id: int) -> int:
    """A bulk UPDATE, not fetch-one-and-set -- self-healing against any
    duplicate-active rows a concurrent create race left behind (either
    from before migration 0020, or from the narrow window it still
    allows between the check and the commit). Returns the row count."""
    result = await session.execute(
        update(ShareLink)
        .where(ShareLink.check_run_id == check_run_id, ShareLink.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    return result.rowcount


async def get_active_share_link(
    session: AsyncSession, check_run_id: int, instructor_id: int
) -> ShareLinkOut | None:
    await get_check_run(session, check_run_id, instructor_id)
    link = await _active_link(session, check_run_id)
    return _to_out(link) if link is not None else None


async def create_share_link(
    session: AsyncSession, check_run_id: int, instructor_id: int, expires_at: datetime | None
) -> ShareLinkOut:
    """Generate (or regenerate) the one active link for this report.
    Rows are never mutated in place (matches this app's own history-
    never-mutates precedent, `Rubric`/`CheckRun` versioning) -- every
    active link is revoked, then a fresh token is inserted, so a link
    that was ever live can always be audited as having existed."""
    # Ownership check + row lock, not a plain read: the plain
    # `check_run_id` parameter (an int) is used below, never an attribute
    # off the returned/locked row. A rollback after a caught
    # IntegrityError expires every ORM object on this session (SQLAlchemy's
    # default), and re-accessing an expired attribute mid-retry triggers
    # an implicit lazy-load that isn't awaited here -- a real bug this
    # test's own concurrency run caught live (`MissingGreenlet`), not a
    # hypothetical.
    await _lock_check_run(session, check_run_id, instructor_id)

    for attempt in range(_MAX_CREATE_ATTEMPTS):
        await _revoke_all_active(session, check_run_id)
        link = ShareLink(
            token=secrets.token_urlsafe(32),
            check_run_id=check_run_id,
            expires_at=expires_at,
        )
        session.add(link)
        try:
            await _record_public_details(session, link)
            await session.commit()
        except IntegrityError:
            await session.rollback()
            if attempt == _MAX_CREATE_ATTEMPTS - 1:
                raise
            continue
        await session.refresh(link)
        return _to_out(link)
    raise AssertionError("unreachable")  # loop always returns or raises


_SHARE_SNAPSHOT_EVENT = "public_report_details_recorded"


async def _record_public_details(session: AsyncSession, link: ShareLink) -> None:
    # Local import preserves the report/flag assembly dependency order.
    from app.flags.service import _to_flag_out

    run = await session.get(CheckRun, link.check_run_id)
    if run is None or run.status != CheckRunStatus.done:
        return
    saved = await lock_assessment(session, run.id)
    if saved is None:
        return
    report = await report_out_for_check_run(session, run)
    manuscript = await session.get(Manuscript, run.manuscript_id)
    details = []
    rows = (
        await session.execute(
            select(Flag, CheckResult)
            .join(
                CheckResult,
                CheckResult.id == Flag.check_result_id,
            )
            .where(CheckResult.check_run_id == run.id)
            .order_by(Flag.id)
        )
    ).all()
    for flag, result in rows:
        private = await _to_flag_out(
            session, flag, result, manuscript, run, assessment_revision=saved.assessment_revision
        )
        details.append(
            PublicFindingDetailOut(
                id=private.id,
                ai_verdict_summary=private.ai_verdict_summary,
                ai_reasoning=private.ai_reasoning,
                agreement_pair=private.agreement_pair,
                passage_pair=private.passage_pair,
                p_value_comparison=private.p_value_comparison,
                citation_metadata_comparison=private.citation_metadata_comparison,
                citation_source_locator=private.citation_source_locator,
                citation_metadata_review_warning=private.citation_metadata_review_warning,
            )
        )
    snapshot = PublicShareSnapshot(
        version=1,
        check_run_id=run.id,
        assessment_revision=saved.assessment_revision,
        token_sha256=hashlib.sha256(link.token.encode()).hexdigest(),
        subject=report.subject,
        integrity_modules=report.integrity_modules,
        finding_details=details,
    )
    await write_audit_event(
        session,
        event_type=_SHARE_SNAPSHOT_EVENT,
        check_run_id=run.id,
        payload=snapshot.model_dump(mode="json"),
    )


async def _public_details(session: AsyncSession, link: ShareLink, report: ReportOut):
    digest = hashlib.sha256(link.token.encode()).hexdigest()
    rows = list(
        (
            await session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.check_run_id == link.check_run_id,
                    AuditLog.event_type == _SHARE_SNAPSHOT_EVENT,
                    AuditLog.payload["token_sha256"].astext == digest,
                )
                .limit(2)
            )
        ).all()
    )
    if len(rows) != 1 or rows[0].created_at != link.created_at:
        return None, "not_recorded"
    try:
        snapshot = PublicShareSnapshot.model_validate(rows[0].payload)
    except ValidationError:
        return None, "not_recorded"
    if snapshot.check_run_id != link.check_run_id or snapshot.token_sha256 != digest:
        return None, "not_recorded"
    if snapshot.assessment_revision != report.assessment_revision:
        return None, "stale"
    return snapshot, "current"


async def revoke_share_link(session: AsyncSession, check_run_id: int, instructor_id: int) -> None:
    await get_check_run(session, check_run_id, instructor_id)
    revoked_count = await _revoke_all_active(session, check_run_id)
    if revoked_count == 0:
        await session.rollback()
        raise NotFoundError("This report has no active share link to revoke.")
    await session.commit()


def _to_public_report(report: ReportOut) -> PublicReportOut:
    """BUG-044 fix: an explicit field-by-field projection, not a reuse of
    `ReportOut` or a blind `.model_dump()` reconstruction — every field
    below is a deliberate choice, so a future field added to `ReportOut`
    is invisible here until someone decides it belongs. `resolution`
    (per-criterion) and `previous_status`/`previous_composite_score`/
    `pending_review_count` (report-level) are the fields this function
    is the reason don't appear -- see `PublicReportOut`'s own docstring
    for why each was excluded. `integrity_check_status` (BUG-125) is the
    newest such exclusion. The public view carries only the boolean fact
    that assessment is incomplete, not the private per-check details."""
    return PublicReportOut(
        check_run_id=report.check_run_id,
        assessment_revision=report.assessment_revision,
        manuscript_group_label=report.manuscript_group_label,
        manuscript_original_filename=report.manuscript_original_filename,
        rubric_title=report.rubric_title,
        status=report.status,
        composite_score=report.composite_score,
        thresholds=report.thresholds,
        reason=report.reason,
        flag_deduction=report.flag_deduction,
        unresolved_high_flag_count=report.unresolved_high_flag_count,
        llm_mode=report.llm_mode,
        results=[
            PublicCriterionResultOut(
                criterion_id=r.criterion_id,
                text=r.text,
                type=r.type,
                weight=r.weight,
                weight_importance=r.weight_importance,
                kind=r.kind,
                outcome=r.outcome,
                score=r.score,
                basis=r.basis,
                anchor=r.anchor,
                reasoning=r.reasoning,
                reason=r.reason,
                evidence=r.evidence,
                rule_facts=r.rule_facts,
                rule_scope_unconfirmed=r.rule_scope_unconfirmed,
                # V-069 AC5: the level IS the judgment for this row
                # (owner-approved treatment) -- deliberately published,
                # same reasoning BUG-052 already applied to
                # rubric_needs_review below.
                level=r.level,
                # V-069 (UX review finding, live-reproduced): the bare
                # FACT of being resolved is safe to publish even though
                # the private reasoning (`r.resolution`) isn't -- without
                # this, the public row had no way to tell "AI-graded" from
                # "instructor resolved this by hand," and fell back to
                # showing the AI's own superseded evidence as if it were
                # final.
                resolved=r.resolution is not None,
            )
            for r in report.results
        ],
        decision=report.decision,
        decided_at=report.decided_at,
        decision_note=report.decision_note,
        rubric_is_current=report.rubric_is_current,
        # BUG-052: deliberately published -- the adviser needs to know the
        # measuring instrument was flagged too, not just the instructor.
        rubric_needs_review=report.rubric_needs_review,
        rubric_parse_issues=report.rubric_parse_issues,
        # V-069 AC2/AC5: the rubric's own RATING, same transcription-only
        # treatment as the instructor's own report view.
        levelled_rating=report.levelled_rating,
        originality_scope=report.originality_scope,
        statistical_check_scope=report.statistical_check_scope,
        assessment_incomplete=bool(report.integrity_check_status),
        historical_citation_title_unverified=report.historical_citation_title_unverified,
        historical_native_math_unverified=report.historical_native_math_unverified,
        semantic_support_unverified=report.historical_semantic_support_unverified,
    )


async def get_shared_report(session: AsyncSession, token: str) -> SharedReportOut:
    """The public, unauthenticated read (screen 4l) -- the token IS the
    authorization; no instructor_id anywhere in this path. Distinguishes
    "never existed" (404) from "existed, but was revoked/expired" (410)
    so a revoked link's own visitor sees an honest message, not an
    ambiguous "wrong URL" one (AC)."""
    link = await session.get(ShareLink, token)
    if link is None:
        raise NotFoundError("This link doesn't exist.")
    if link.revoked_at is not None:
        raise GoneError("This link has been turned off by the instructor.")
    if link.expires_at is not None and link.expires_at <= datetime.now(UTC):
        raise GoneError("This link has expired.")

    check_run = await session.get(CheckRun, link.check_run_id)
    if check_run is None:
        raise NotFoundError("This link doesn't exist.")

    await lock_assessment(session, check_run.id)
    report: ReportOut = await report_out_for_check_run(session, check_run)
    flags = await flags_for_check_run(session, check_run.id)
    snapshot, state = await _public_details(session, link, report)
    public = _to_public_report(report)
    if snapshot is not None:
        public.subject = snapshot.subject
        public.integrity_modules = snapshot.integrity_modules
    # Recheck after every asynchronous projection; revocation can win in flight.
    from app.report.evidence_service import authorize_evidence_run

    current_run = await authorize_evidence_run(session, token=token)
    if current_run.id != check_run.id:
        raise NotFoundError("This link doesn't exist.")
    return SharedReportOut(
        report=public,
        flags=flags,
        finding_details=snapshot.finding_details if snapshot else [],
        detail_snapshot=state,
    )
