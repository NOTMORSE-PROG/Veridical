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
