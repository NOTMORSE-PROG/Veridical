"""V-066: the shared-corpus library. Two-up compare forks on ownership
(Pre-implementation research Q3, `tickets/V8-real-use/open/V-066.md`):

- **Own manuscript** (`get_library_document`/`_file_path`/`_paragraphs`):
  reuses `app.report.service`'s manuscript-keyed core helpers -- the exact
  file-opening/purge-guard logic V-065's check-run-scoped viewer already
  uses, just resolved by `manuscript_id` directly instead of through a
  check run, and with no flags overlay (a library record isn't a check
  run). 404s if the manuscript isn't the requester's own -- this codebase's
  established "uniform 404, never 403" ownership convention (Track B,
  2026-08-16 audit).
- **Another instructor's manuscript** (`get_library_excerpt`): NEVER opens
  the owning account's file. Re-serves the bounded excerpt V-072 already
  computed and persisted at embed time (`ManuscriptChapterArchive`/
  `ManuscriptPassageArchive`) -- the same data `PassagePairPanel` already
  renders for a reuse flag. This is Q2's ruling (owner, 2026-08-11, carried
  from V-058/BUG-050 Branch B) enforced at the API: identity plus a
  bounded, configurable excerpt, never the full document.
"""

from pathlib import Path

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.reuse.embed import split_context
from app.config import Settings
from app.errors import NotFoundError
from app.groups.service import UNSET_PROGRAM_FILTER
from app.library.schemas import (
    LibraryChapterExcerptOut,
    LibraryExcerptOut,
    LibraryItemOut,
    PaginatedLibrary,
)
from app.models.group import Group, GroupMember, Program
from app.models.manuscript import Manuscript, ManuscriptChapterArchive, ManuscriptPassageArchive
from app.report.schemas import DocumentParagraphsOut, ManuscriptViewerOut
from app.report.service import (
    manuscript_file_path_for,
    manuscript_paragraphs_for,
    manuscript_viewer_for,
)


async def _resolve_manuscript(session: AsyncSession, manuscript_id: int) -> Manuscript:
    manuscript = await session.get(Manuscript, manuscript_id)
    if manuscript is None:
        raise NotFoundError(f"No manuscript with id {manuscript_id}.")
    return manuscript


async def _resolve_owned_manuscript(
    session: AsyncSession, instructor_id: int, manuscript_id: int
) -> Manuscript:
    """Same 404-not-403 convention `_owned_check_run`/`purge_manuscript`
    already use elsewhere in this codebase (never disclose "exists but
    isn't yours" as a distinct outcome from "doesn't exist")."""
    manuscript = await _resolve_manuscript(session, manuscript_id)
    if manuscript.instructor_id != instructor_id:
        raise NotFoundError(f"No manuscript with id {manuscript_id}.")
    return manuscript


async def _authors_by_group_id(session: AsyncSession, group_ids: set[int]) -> dict[int, list[str]]:
    if not group_ids:
        return {}
    rows = (
        await session.execute(
            select(GroupMember.group_id, GroupMember.name)
            .where(GroupMember.group_id.in_(group_ids))
            .order_by(GroupMember.id)
        )
    ).all()
    authors: dict[int, list[str]] = {}
    for group_id, name in rows:
        authors.setdefault(group_id, []).append(name)
    return authors


def _item_out(
    manuscript: Manuscript,
    *,
    title: str | None,
    program_name: str | None,
    authors: list[str],
    requesting_instructor_id: int,
) -> LibraryItemOut:
    """BUG-147 (Critical, owner-decision-documented fix -- ticket's own
    "cheapest first" option 1): another instructor's manuscript keeps
    ONLY what F7 needs to stay explainable -- a non-identifying reference,
    program, and processing date, exactly the ticket's own worked example
    ("archived manuscript #3, IT, Aug 2026"). Real student names, the
    capstone title, the team name, and the original filename are never
    returned for a row this requester doesn't own. The originality corpus
    itself stays shared by design (BUG-050, owner decision, 2026-08-16,
    restated in FEATURES.md) -- this fixes the PAYLOAD, not the sharing."""
    is_own = manuscript.instructor_id == requesting_instructor_id
    if not is_own:
        return LibraryItemOut(
            manuscript_id=manuscript.id,
            group_label=f"Archived manuscript #{manuscript.id}",
            title=None,
            authors=[],
            program=program_name,
            original_filename=None,
            created_at=manuscript.created_at,
            purged_at=manuscript.purged_at,
            is_own=False,
        )
    return LibraryItemOut(
        manuscript_id=manuscript.id,
        group_label=manuscript.group_label,
        title=title,
        authors=authors,
        program=program_name,
        original_filename=manuscript.original_filename,
        created_at=manuscript.created_at,
        purged_at=manuscript.purged_at,
        is_own=True,
    )


async def list_library(
    session: AsyncSession,
    instructor_id: int,
    *,
    page: int = 1,
    page_size: int = 50,
    program: str | None = None,
    search: str | None = None,
) -> PaginatedLibrary:
    """Spans the WHOLE corpus (no `instructor_id` filter on `Manuscript`
    itself, unlike `app.archive.service.list_archive`) -- this IS the
    screen that makes the shared library real instead of a claim on the
    Archive screen's own disclosure text (`frontend/src/archive/
    Archive.tsx`: "a match can reference a manuscript that isn't listed
    here"). `search` matches group name, extracted title, original
    filename, or any recorded member name -- AC2's "search by title/author"."""
    filters = []
    count_stmt = (
        select(func.count())
        .select_from(Manuscript)
        .join(Group, Group.id == Manuscript.group_id, isouter=True)
        .join(Program, Program.id == Group.program_id, isouter=True)
    )
    items_stmt = (
        select(Manuscript, Group.title, Group.id, Program.name)
        .join(Group, Group.id == Manuscript.group_id, isouter=True)
        .join(Program, Program.id == Group.program_id, isouter=True)
    )

    if program == UNSET_PROGRAM_FILTER:
        filters.append(or_(Manuscript.group_id.is_(None), Group.program_id.is_(None)))
    elif program is not None:
        filters.append(Program.name == program)

    if search and search.strip():
        needle = f"%{search.strip()}%"
        member_match = select(GroupMember.id).where(
            GroupMember.group_id == Group.id, GroupMember.name.ilike(needle)
        )
        # BUG-147 (`ux-critic` finding, live-reproduced): a search hit is
        # itself a disclosure -- typing another instructor's real student
        # name, capstone title, team name, or filename into this box and
        # getting ANY result back (even one whose row shows only the
        # anonymized placeholder) confirms that name exists in the shared
        # corpus, with program and date attached. That defeats the whole
        # point of `_item_out`'s redaction: an instructor doesn't need to
        # SEE a name to have it CONFIRMED. Scoped to the requester's own
        # manuscripts, matching this endpoint's ticket-cited purpose
        # ("search by title/author") -- a name search can only ever
        # search names the requester is already allowed to see.
        filters.append(
            and_(
                Manuscript.instructor_id == instructor_id,
                or_(
                    Group.name.ilike(needle),
                    Group.title.ilike(needle),
                    Manuscript.original_filename.ilike(needle),
                    member_match.exists(),
                ),
            )
        )

    total = await session.scalar(count_stmt.where(*filters)) or 0
    rows = (
        await session.execute(
            items_stmt.where(*filters)
            .order_by(Manuscript.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    group_ids = {group_id for _, _, group_id, _ in rows if group_id is not None}
    authors_by_group = await _authors_by_group_id(session, group_ids)

    items = [
        _item_out(
            manuscript,
            title=title,
            program_name=program_name,
            authors=authors_by_group.get(group_id, []),
            requesting_instructor_id=instructor_id,
        )
        for manuscript, title, group_id, program_name in rows
    ]
    return PaginatedLibrary(items=items, total=total, page=page, page_size=page_size)


async def get_library_item(
    session: AsyncSession, instructor_id: int, manuscript_id: int
) -> LibraryItemOut:
    """AC3: a single record's detail -- visible for ANY manuscript in the
    corpus (identity is the point of the library, BUG-050's DECIDED
    direction), own or not."""
    manuscript = await _resolve_manuscript(session, manuscript_id)
    title: str | None = None
    program_name: str | None = None
    authors: list[str] = []
    if manuscript.group_id is not None:
        row = (
            await session.execute(
                select(Group.title, Program.name)
                .join(Program, Program.id == Group.program_id, isouter=True)
                .where(Group.id == manuscript.group_id)
            )
        ).first()
        if row is not None:
            title, program_name = row
        authors = (await _authors_by_group_id(session, {manuscript.group_id})).get(
            manuscript.group_id, []
        )
    return _item_out(
        manuscript,
        title=title,
        program_name=program_name,
        authors=authors,
        requesting_instructor_id=instructor_id,
    )


async def get_library_document(
    session: AsyncSession, instructor_id: int, manuscript_id: int, settings: Settings
) -> ManuscriptViewerOut:
    manuscript = await _resolve_owned_manuscript(session, instructor_id, manuscript_id)
    return await manuscript_viewer_for(manuscript, [], settings)


async def get_library_file_path(
    session: AsyncSession, instructor_id: int, manuscript_id: int, settings: Settings
) -> Path:
    manuscript = await _resolve_owned_manuscript(session, instructor_id, manuscript_id)
    return manuscript_file_path_for(manuscript, settings)


async def get_library_paragraphs(
    session: AsyncSession, instructor_id: int, manuscript_id: int, settings: Settings
) -> DocumentParagraphsOut:
    manuscript = await _resolve_owned_manuscript(session, instructor_id, manuscript_id)
    return manuscript_paragraphs_for(manuscript, settings)


async def get_library_excerpt(
    session: AsyncSession, manuscript_id: int, settings: Settings
) -> LibraryExcerptOut:
    """No ownership check at all -- deliberately available for ANY
    manuscript in the corpus, including the requester's own, because it is
    bounded by construction and never touches a stored file. This is the
    two-up source for the "not mine" side (Q2's ruling); the frontend
    chooses between this and the full document based on `is_own`.

    `ui-designer` finding (V-066 spec pass, 2026-08-23), verified by reading
    `app.archive.service.purge_manuscript` in full: purge deletes
    `ManuscriptChapterArchive`/`ManuscriptArchive` and the stored files, but
    NEVER `ManuscriptPassageArchive` -- passage-level bounded text survives
    a purge today. That retention gap is real and is filed as its own bug
    (`BUG-123`), not fixed here (fixing what a purge deletes is a
    `purge_manuscript` change, out of this ticket's blast radius).

    `backend-critic` (V-066 review, 2026-08-23) correctly notes the explicit
    `purged_at` check below is not the ONLY thing protecting this endpoint
    today: since `shown` is drawn from `ManuscriptChapterArchive`, which
    purge already deletes, a purged manuscript would return zero chapters
    even without this check. The check is kept anyway, as real
    defense-in-depth against a future refactor that decouples excerpt
    display from chapter-archive presence (e.g. reading straight from
    `ManuscriptPassageArchive`, which does NOT get cleaned up by purge) --
    but it is not, today, independently load-bearing the way "the belt"
    implied. Both guards, not one, are what currently keeps this endpoint
    honest."""
    manuscript = await _resolve_manuscript(session, manuscript_id)
    if manuscript.purged_at is not None:
        return LibraryExcerptOut(
            manuscript_id=manuscript_id,
            chapters=[],
            total_chapters=0,
            limitations=(
                f"This manuscript's stored content was removed on "
                f"{manuscript.purged_at.date().isoformat()} and can no longer be previewed."
            ),
            purged_at=manuscript.purged_at,
        )

    chapter_rows = (
        await session.scalars(
            select(ManuscriptChapterArchive)
            .where(ManuscriptChapterArchive.manuscript_id == manuscript_id)
            .order_by(ManuscriptChapterArchive.chapter_index)
        )
    ).all()
    total_chapters = len(chapter_rows)
    shown = chapter_rows[: settings.library_excerpt_max_chapters]

    # One representative passage per shown chapter -- the FIRST body
    # (non-reference-list, non-block-quote) passage in chapter order, same
    # default-exclusion policy F7.4's own matching uses (ticket AC3, "on by
    # default"). A chapter with no qualifying passage (very short, or
    # entirely quotes/references) shows no excerpt rather than a fabricated
    # one -- an honest gap, not an error. Split the same way
    # `PassagePairPanel` already renders a matched passage
    # (`split_context`), so this reuses the identical visual mechanism.
    excerpts: dict[int, tuple[str | None, str | None, str | None]] = {}
    if shown:
        chapter_indices = [c.chapter_index for c in shown]
        passage_rows = (
            await session.scalars(
                select(ManuscriptPassageArchive)
                .where(
                    ManuscriptPassageArchive.manuscript_id == manuscript_id,
                    ManuscriptPassageArchive.chapter_index.in_(chapter_indices),
                    ManuscriptPassageArchive.is_reference_list.is_(False),
                    ManuscriptPassageArchive.is_block_quote.is_(False),
                )
                .order_by(
                    ManuscriptPassageArchive.chapter_index, ManuscriptPassageArchive.passage_index
                )
            )
        ).all()
        for passage in passage_rows:
            if passage.chapter_index in excerpts:
                continue
            before, after = split_context(passage.context_text, passage.text)
            excerpts[passage.chapter_index] = (before, passage.text, after)

    def _chapter_out(c: ManuscriptChapterArchive) -> LibraryChapterExcerptOut:
        before, excerpt, after = excerpts.get(c.chapter_index, (None, None, None))
        return LibraryChapterExcerptOut(
            chapter_index=c.chapter_index,
            title=c.title,
            excerpt=excerpt,
            context_before=before,
            context_after=after,
        )

    return LibraryExcerptOut(
        manuscript_id=manuscript_id,
        chapters=[_chapter_out(c) for c in shown],
        total_chapters=total_chapters,
        limitations=(
            "This is a bounded excerpt, not the full manuscript. VERIDICAL never shows another "
            "account's full document; it shows one representative passage per chapter, up to "
            f"{settings.library_excerpt_max_chapters} chapters."
        ),
    )
