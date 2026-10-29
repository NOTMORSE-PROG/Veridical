"""V-077 (D-028): the Library HTTP surface -- a standalone collection of
manuscripts that is also the only F7 comparison corpus.

The corpus is shared across instructor accounts (BUG-050), so another
instructor's active entries are listed, but only through the redacted shape
`_item_out` builds (BUG-147/D-026): record number, program, school year and
date added, never title, team, authors, filename or full source. `is_own` is
the only ownership signal ever exposed, never a raw `instructor_id`.

No field here carries a readiness band, criterion result, flag, score or
report link: the Library makes no judgment (owner, 2026-10-12).
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.db import DbText
from app.library.passage_indexing import PassageIndexingOut
from app.models.enums import LibraryEntrySource, LibraryEntryStatus


class LibraryDuplicateRefOut(BaseModel):
    """The requester's OWN Library entry a submission duplicates (exact) or
    may duplicate (same text). Never another account's: duplicate detection
    is own-account only (BUG-234's existence-oracle ruling)."""

    manuscript_id: int
    label: str
    original_filename: str | None
    added_at: datetime | None


class LibraryItemOut(BaseModel):
    passage_indexing: PassageIndexingOut = Field(default_factory=PassageIndexingOut)
    manuscript_id: int
    is_own: bool
    # Own: title, else team name, else filename, else the record label.
    # Foreign: always "Library record #<id>".
    label: str
    title: str | None
    team_name: str | None
    authors: list[str]
    program: str | None
    school_year: str | None
    original_filename: str | None
    status: LibraryEntryStatus
    # Own only (None for foreign): how this entry entered the Library.
    source: LibraryEntrySource | None
    added_at: datetime | None
    created_at: datetime
    # Own only: fields read from the title page and not edited since.
    proposed_fields: list[str] = Field(default_factory=list)
    # Own only, while `needs_decision`.
    possible_duplicate_of: LibraryDuplicateRefOut | None = None
    # Own only, while `failed`.
    failure_detail: str | None = None
    # Own only: a processing entry whose request died mid-way. It no longer
    # blocks re-adding the same file, and its owner may remove it.
    stalled: bool = False


class PaginatedLibrary(BaseModel):
    items: list[LibraryItemOut]
    total: int
    page: int
    page_size: int


class LibrarySubmissionOutcome(StrEnum):
    added = "added"
    exact_duplicate = "exact_duplicate"
    possible_duplicate = "possible_duplicate"
    failed = "failed"


class LibrarySubmissionOut(BaseModel):
    outcome: LibrarySubmissionOutcome
    # The entry this submission created; None for `exact_duplicate` (nothing
    # is created) and for a file rejected before an entry existed.
    item: LibraryItemOut | None
    duplicate_of: LibraryDuplicateRefOut | None
    message: str


class LibrarySubmissionIn(BaseModel):
    """Optional metadata sent with one submitted file. Blank title, team name
    and authors are proposed from the title page and stay editable."""

    title: DbText | None = Field(default=None, max_length=500)
    team_name: DbText | None = Field(default=None, max_length=200)
    authors: list[DbText] = Field(default_factory=list)
    program: DbText | None = Field(default=None, max_length=50)
    school_year: DbText | None = Field(default=None, max_length=20)


class LibraryEntryUpdate(BaseModel):
    """PATCH semantics: only fields present in the body change; an empty
    string or null clears a field."""

    title: DbText | None = Field(default=None, max_length=500)
    team_name: DbText | None = Field(default=None, max_length=200)
    authors: list[DbText] | None = None
    program: DbText | None = Field(default=None, max_length=50)
    school_year: DbText | None = Field(default=None, max_length=20)


class LibraryDecisionIn(BaseModel):
    action: Literal["add", "skip"]


class LibraryEntryStateOut(BaseModel):
    passage_indexing: PassageIndexingOut = Field(default_factory=PassageIndexingOut)
    manuscript_id: int
    status: LibraryEntryStatus
    added_at: datetime | None
    removed_at: datetime | None
    # True when the stored file itself was deleted (a Library-only
    # submission); False when only comparison data left the Library and a
    # checked manuscript's report keeps its file.
    file_deleted: bool = False


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
    purged_at: datetime | None = None
