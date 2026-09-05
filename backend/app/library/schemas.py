"""V-066: the shared-corpus library HTTP surface -- browse every manuscript
VERIDICAL has ever ingested, across every instructor account (BUG-050
Branch B, owner-decided: the corpus is shared by design, matching gets
stronger as it grows). `is_own` is the only ownership signal this surface
ever exposes about another account's manuscript -- never a raw
`instructor_id` -- the same "identify the record, not the account" posture
BUG-050's fix established for F7 flags.
"""

from datetime import datetime

from pydantic import BaseModel


class LibraryDuplicateUploadOut(BaseModel):
    """BUG-148: one OTHER upload sharing this row's exact `content_hash`,
    for the same instructor only -- see `LibraryItemOut.duplicate_uploads`."""

    manuscript_id: int
    created_at: datetime
    purged_at: datetime | None
    original_filename: str | None
    # Same "no report yet" honesty as the dashboard's own
    # `ManuscriptListItem.latest_done_check_run_id` (`app.ingest.service`) --
    # null rather than a fabricated link when this specific upload was never
    # successfully checked.
    latest_done_check_run_id: int | None


class LibraryItemOut(BaseModel):
    manuscript_id: int
    group_label: str
    # NULL = "not set" -- V-063's extraction found nothing, or the group
    # predates this column (see `manuscript_group.title`'s own docstring).
    title: str | None
    authors: list[str]
    program: str | None
    original_filename: str | None
    created_at: datetime
    purged_at: datetime | None
    # True only when this manuscript belongs to the instructor making the
    # request -- the one signal that decides which two-up mode the
    # frontend may use (full document vs. bounded excerpt, Q2's ruling).
    is_own: bool
    # BUG-148: the OTHER instructor-owned manuscripts sharing this exact
    # `content_hash` (never includes this row's own manuscript_id), newest
    # first. Present only when `is_own` is True and at least one other of
    # THIS instructor's own manuscripts is a byte-identical re-upload --
    # `None` otherwise, including always for `is_own: False` (cross-tenant
    # hash correlation is not a fact this endpoint discloses; same
    # same-instructor scope BUG-140 already established for the reuse
    # check). This row (`manuscript_id`, above) is always the GROUP's own
    # representative (see `_dedup_own_rows` in `library/service.py`), never
    # one of the entries listed here.
    duplicate_uploads: list[LibraryDuplicateUploadOut] | None = None
    # BUG-148 (`ux-critic` finding: a hidden duplicate-group sibling could
    # link straight to its own completed report while the far more visible
    # representative row could not -- a new asymmetry this ticket's own fix
    # introduced next to it on the same card). Same "no report yet" honesty
    # as `LibraryDuplicateUploadOut.latest_done_check_run_id` -- and, like
    # `duplicate_uploads`, always `None` for `is_own: False` (never disclose
    # whether another account has a completed report).
    latest_done_check_run_id: int | None = None


class PaginatedLibrary(BaseModel):
    items: list[LibraryItemOut]
    total: int
    page: int
    page_size: int


class LibraryChapterExcerptOut(BaseModel):
    chapter_index: int
    title: str
    # ONE representative passage, split the same way `PassagePairPanel`
    # already renders a matched passage (`app.checks.reuse.embed.
    # split_context`) -- `excerpt` is the passage itself, `context_before`/
    # `context_after` are the bounded surrounding words. All `None` when
    # this chapter has no qualifying passage (an honest gap, not an error).
    excerpt: str | None
    context_before: str | None
    context_after: str | None


class LibraryExcerptOut(BaseModel):
    """The cross-tenant-safe detail view (Q2's ruling, enforced here at the
    API, not merely in the UI): identity plus a bounded, configurable
    excerpt, never the full document. Every field is re-served from data
    already persisted at embed time -- this endpoint never opens the
    owning account's stored file."""

    manuscript_id: int
    chapters: list[LibraryChapterExcerptOut]
    # Honest disclosure (charter rule 9): how many chapters exist beyond
    # what's shown, so a short list never silently implies a short document.
    total_chapters: int
    limitations: str
    # V-066 (ui-designer finding, purge-retention gap): non-NULL means this
    # manuscript's owner purged its stored content -- `chapters` is always
    # `[]` in that case, defense-in-depth against `ManuscriptPassageArchive`
    # rows outliving a purge (see `get_library_excerpt`'s own docstring;
    # the retention gap itself is `BUG-123`, filed separately, not fixed
    # here -- this is the belt, not the buckle).
    purged_at: datetime | None = None
