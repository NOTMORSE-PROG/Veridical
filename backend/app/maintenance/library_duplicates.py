"""V-077 (D-028): one-time, owner-run cleanup of duplicate Library entries.

Migration 0037 carried every pre-D-028 comparison source into the Library,
including the duplicates the old automatic write-back accumulated (BUG-140 /
BUG-148: re-uploads of the same file). This module previews and applies the
cleanup the owner asked for ("keep but remove duplicates"):

- **Exact copies within one account** (same `content_hash`, same owner,
  active entries): one representative is kept (newest added, then highest
  id) and the rest leave the Library. Certain, so `apply_cleanup` executes it.
- **Exact copies across accounts** are listed, never removed. Each owner
  keeps their own copy: removing one owner's copy would make that owner's
  next dashboard re-upload match only another account's copy, losing the
  BUG-140 single "confirm resubmission" flag for a flood of high-severity
  findings (backend-critic, 2026-10-13). Matching already treats identical
  copies as one source (`canonical_library_copies`).
- **Same text, different file** (whole-document similarity at or above
  `reuse_exact_duplicate_threshold`): listed only. A similarity is a
  judgment, so these leave only when the owner names them (ground rule 1).
- **Invariant breaks**: corpus rows with no active entry (for example a
  check that was mid-write-back across the D-028 deploy) are removed on
  apply; active entries with no whole-document row are listed.

The report is read only by the operator, so listing cross-account copies is
not the user-facing existence oracle BUG-234 rules out. Leaving the Library
never deletes a stored file here, so every removal can be reversed by
re-embedding. Every deletion re-checks the current state under the
manuscript's advisory lock, so it acts on what is true now, not on the
preview.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.archive.service import delete_archive_rows
from app.audit.service import write_audit_event
from app.config import Settings
from app.db import advisory_lock
from app.models.enums import LibraryEntryStatus, LibraryRemovalReason
from app.models.library import LibraryEntry
from app.models.manuscript import (
    Manuscript,
    ManuscriptArchive,
    ManuscriptChapterArchive,
    ManuscriptPassageArchive,
)


@dataclass(frozen=True)
class ExactCopyGroup:
    keep_manuscript_id: int
    remove_manuscript_ids: tuple[int, ...]


@dataclass(frozen=True)
class SameTextPair:
    manuscript_id: int
    other_manuscript_id: int
    similarity: float
    same_account: bool


@dataclass(frozen=True)
class LibraryDuplicateReport:
    active_entries: int
    exact_copy_groups: tuple[ExactCopyGroup, ...]
    same_text_pairs: tuple[SameTextPair, ...]
    orphan_corpus_manuscript_ids: tuple[int, ...]
    active_without_comparison_data: tuple[int, ...]
    threshold: float
    # Byte-identical copies held by different accounts: informational only.
    cross_account_copy_groups: tuple[tuple[int, ...], ...] = field(default_factory=tuple)
    applied_removals: tuple[int, ...] = field(default_factory=tuple)

    @property
    def exact_copy_removals(self) -> int:
        return sum(len(g.remove_manuscript_ids) for g in self.exact_copy_groups)


async def preview_library_duplicates(
    session: AsyncSession, settings: Settings
) -> LibraryDuplicateReport:
    rows = (
        await session.execute(
            select(
                Manuscript.id,
                Manuscript.instructor_id,
                Manuscript.content_hash,
                func.coalesce(LibraryEntry.added_at, LibraryEntry.created_at),
            )
            .join(LibraryEntry, LibraryEntry.manuscript_id == Manuscript.id)
            .where(LibraryEntry.status == LibraryEntryStatus.active)
        )
    ).all()
    owner = {mid: instructor for mid, instructor, _, _ in rows}

    by_owner_hash: dict[tuple[int, str], list[tuple[int, int, datetime]]] = {}
    owners_by_hash: dict[str, set[int]] = {}
    ids_by_hash: dict[str, set[int]] = {}
    for mid, instructor, content_hash, added in rows:
        if content_hash is not None:
            by_owner_hash.setdefault((instructor, content_hash), []).append(
                (mid, instructor, added)
            )
            owners_by_hash.setdefault(content_hash, set()).add(instructor)
            ids_by_hash.setdefault(content_hash, set()).add(mid)
    cross_account = tuple(
        tuple(sorted(ids_by_hash[h])) for h, owners in owners_by_hash.items() if len(owners) > 1
    )
    groups: list[ExactCopyGroup] = []
    removed: set[int] = set()
    for members in by_owner_hash.values():
        if len(members) < 2:
            continue
        keep = max(members, key=lambda m: (m[2], m[0]))
        remove = tuple(sorted(m[0] for m in members if m[0] != keep[0]))
        removed.update(remove)
        groups.append(
            ExactCopyGroup(
                keep_manuscript_id=keep[0],
                remove_manuscript_ids=remove,
            )
        )

    vectors = (
        await session.execute(
            select(ManuscriptArchive.manuscript_id, ManuscriptArchive.embedding).where(
                ManuscriptArchive.manuscript_id.in_(owner.keys()),
                ManuscriptArchive.model_id == settings.embedding_model_id,
            )
        )
    ).all()
    has_vector = {mid for mid, _ in vectors}
    candidates = [
        (mid, np.asarray(v, dtype=np.float64)) for mid, v in vectors if mid not in removed
    ]
    pairs: list[SameTextPair] = []
    for i, (mid_a, vec_a) in enumerate(candidates):
        norm_a = np.linalg.norm(vec_a)
        if norm_a == 0:
            continue
        for mid_b, vec_b in candidates[i + 1 :]:
            norm_b = np.linalg.norm(vec_b)
            if norm_b == 0:
                continue
            similarity = float(np.dot(vec_a, vec_b) / (norm_a * norm_b))
            if similarity >= settings.reuse_exact_duplicate_threshold:
                pairs.append(
                    SameTextPair(
                        manuscript_id=min(mid_a, mid_b),
                        other_manuscript_id=max(mid_a, mid_b),
                        similarity=round(similarity, 4),
                        same_account=owner[mid_a] == owner[mid_b],
                    )
                )

    corpus_ids = set(
        (
            await session.scalars(
                union(
                    select(ManuscriptArchive.manuscript_id),
                    select(ManuscriptChapterArchive.manuscript_id),
                    select(ManuscriptPassageArchive.manuscript_id),
                )
            )
        ).all()
    )
    return LibraryDuplicateReport(
        active_entries=len(rows),
        exact_copy_groups=tuple(sorted(groups, key=lambda g: g.keep_manuscript_id)),
        same_text_pairs=tuple(
            sorted(pairs, key=lambda p: (p.manuscript_id, p.other_manuscript_id))
        ),
        orphan_corpus_manuscript_ids=tuple(sorted(corpus_ids - owner.keys())),
        active_without_comparison_data=tuple(sorted(owner.keys() - has_vector)),
        threshold=settings.reuse_exact_duplicate_threshold,
        cross_account_copy_groups=tuple(sorted(cross_account)),
    )


async def _leave_library(
    session: AsyncSession, manuscript_id: int, *, kept_manuscript_id: int | None
) -> bool:
    async with advisory_lock(session, manuscript_id):
        entry = await session.scalar(
            select(LibraryEntry).where(
                LibraryEntry.manuscript_id == manuscript_id,
                LibraryEntry.status == LibraryEntryStatus.active,
            )
        )
        if entry is None:
            return False
        await delete_archive_rows(session, manuscript_id)
        entry.status = LibraryEntryStatus.removed
        entry.removed_at = datetime.now(UTC)
        entry.removal_reason = LibraryRemovalReason.duplicate_cleanup
        await write_audit_event(
            session,
            event_type="library_entry_removed",
            check_run_id=None,
            manuscript_id=manuscript_id,
            payload={
                "source": entry.source.value,
                "reason": LibraryRemovalReason.duplicate_cleanup.value,
                "kept_manuscript_id": kept_manuscript_id,
                "file_deleted": False,
            },
        )
        await session.commit()
        return True


async def apply_cleanup(
    session: AsyncSession,
    settings: Settings,
    *,
    remove_named: tuple[int, ...] = (),
) -> LibraryDuplicateReport:
    """Executes the certain part of the preview (exact copies, orphan corpus
    rows) plus any same-text duplicates the owner named explicitly. Re-reads
    the state first, so it acts on what is true now, not on a stale preview."""
    report = await preview_library_duplicates(session, settings)
    applied: list[int] = []
    for group in report.exact_copy_groups:
        for manuscript_id in group.remove_manuscript_ids:
            if await _leave_library(
                session, manuscript_id, kept_manuscript_id=group.keep_manuscript_id
            ):
                applied.append(manuscript_id)
    for manuscript_id in remove_named:
        if await _leave_library(session, manuscript_id, kept_manuscript_id=None):
            applied.append(manuscript_id)
    for manuscript_id in report.orphan_corpus_manuscript_ids:
        async with advisory_lock(session, manuscript_id):
            # Re-checked under the lock: an entry that became active since the
            # preview owns these rows now and must keep them.
            now_active = await session.scalar(
                select(LibraryEntry.id).where(
                    LibraryEntry.manuscript_id == manuscript_id,
                    LibraryEntry.status == LibraryEntryStatus.active,
                )
            )
            if now_active is None and await delete_archive_rows(session, manuscript_id):
                await write_audit_event(
                    session,
                    event_type="library_orphan_corpus_rows_removed",
                    check_run_id=None,
                    manuscript_id=manuscript_id,
                    payload={},
                )
            await session.commit()
    return LibraryDuplicateReport(
        active_entries=report.active_entries,
        exact_copy_groups=report.exact_copy_groups,
        same_text_pairs=report.same_text_pairs,
        orphan_corpus_manuscript_ids=report.orphan_corpus_manuscript_ids,
        active_without_comparison_data=report.active_without_comparison_data,
        threshold=report.threshold,
        cross_account_copy_groups=report.cross_account_copy_groups,
        applied_removals=tuple(applied),
    )
