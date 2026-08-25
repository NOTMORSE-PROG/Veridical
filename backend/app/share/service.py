"""Read-only, tokenized report sharing (F8.7, V-040)."""

import secrets
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import GoneError, NotFoundError
from app.models.manuscript import Manuscript
from app.models.run import CheckRun
from app.models.share import ShareLink
from app.pipeline.service import get_check_run
from app.report.schemas import PublicCriterionResultOut, PublicReportOut, ReportOut
from app.report.service import flags_for_check_run, report_out_for_check_run
from app.share.schemas import SharedReportOut, ShareLinkOut

# backend-critic finding (P1, live-reproduced with 15 concurrent POSTs):
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
            await session.commit()
        except IntegrityError:
            await session.rollback()
            if attempt == _MAX_CREATE_ATTEMPTS - 1:
                raise
            continue
        await session.refresh(link)
        return _to_out(link)
    raise AssertionError("unreachable")  # loop always returns or raises


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
    newest such exclusion, NOT yet a settled policy call the way the
    others are: whether an external adviser should see that an F4/F5
    check partially executed is a real open question (carried forward,
    not decided here) -- see BUG-125.md's own gap note."""
    return PublicReportOut(
        check_run_id=report.check_run_id,
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
                # V-069 AC5: the level IS the judgment for this row
                # (owner-approved treatment) -- deliberately published,
                # same reasoning BUG-052 already applied to
                # rubric_needs_review below.
                level=r.level,
                # V-069 (`ux-critic` finding, live-reproduced): the bare
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
    if link.expires_at is not None and link.expires_at < datetime.now(UTC):
        raise GoneError("This link has expired.")

    check_run = await session.get(CheckRun, link.check_run_id)
    if check_run is None:
        raise NotFoundError("This link doesn't exist.")

    report: ReportOut = await report_out_for_check_run(session, check_run)
    flags = await flags_for_check_run(session, check_run.id)
    return SharedReportOut(report=_to_public_report(report), flags=flags)
