"""Which Library records are the checked manuscript's own work (BUG-662).

A team's thesis often reaches one instructor's Library more than once: a
re-upload, a revised version, the same file saved under "(1)", or a legacy
check upload carried into the Library under a different group label. Matched
against those copies, the thesis "copies itself" at every passage. Each such
record is left out of the comparison and reported once, as a low lead that
names it and changes nothing.

A record counts as the same work only when it belongs to the same instructor
and one of these holds:

- it is the same file (equal content hash);
- its whole text is a near-exact duplicate (`reuse_exact_duplicate_threshold`,
  the rule BUG-140 applied to the single best match, now applied to every
  record). When the two records name a different title, team or authors,
  the lead says so and asks the instructor to confirm;
- its whole text is at least `reuse_same_work_similarity_threshold` similar
  AND it carries the same name: original file name, title, team name or
  author list, compared after the normalisation in
  `data/same_work_names.json`. A name made only of generic words ("BSIT
  Capstone Manuscript", "Group 3") never counts.

The name rule does not apply when the two records name a title, a team or
authors that disagree, or when the record is dated to an earlier school year
than the checked upload: those stay ordinary leads.

The whole-document vector is read as stored. Unlike a reported match, this
test shows the instructor no text from the record, so it does not need the
record's source to be readable; whether it was is written to the audit.

Copies held by other instructors are left alone unless they are byte-identical
to one of these records, which is the same file the V-077 canonical-copy rule
already treats as one source.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path, PurePath
from typing import Literal

from sqlalchemy import literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.groups.service import DEFAULT_GROUP_LABEL
from app.models.enums import LibraryEntryStatus
from app.models.group import Group, GroupMember
from app.models.library import LibraryEntry
from app.models.manuscript import Manuscript, ManuscriptArchive

_DEFAULT_FILE = Path(__file__).parent / "data" / "same_work_names.json"
_SEPARATORS = re.compile(r"[\W_]+")

SameWorkBasis = Literal[
    "same_file",
    "near_identical_text",
    "near_identical_text_names_differ",
    "same_file_name",
    "same_title",
    "same_team",
    "same_authors",
]


@dataclass(frozen=True)
class NameRules:
    file_name_markers: tuple[re.Pattern[str], ...]
    generic_words: frozenset[str]
    generic_tokens: tuple[re.Pattern[str], ...]


@lru_cache(maxsize=8)
def _load_rules(path: Path) -> NameRules:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return NameRules(
        file_name_markers=tuple(
            re.compile(rf"(?:{pattern})$") for pattern in raw["file_name_markers"]
        ),
        generic_words=frozenset(word.casefold() for word in raw["generic_words"]),
        generic_tokens=tuple(re.compile(pattern) for pattern in raw["generic_token_patterns"]),
    )


def load_name_rules(settings: Settings) -> NameRules:
    return _load_rules(settings.reuse_same_work_names_file or _DEFAULT_FILE)


def name_rules_fingerprint(settings: Settings) -> dict:
    """Which name rules a decision used, for the audit log."""
    path = settings.reuse_same_work_names_file or _DEFAULT_FILE
    return {
        "file": "configured" if settings.reuse_same_work_names_file else "packaged",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _plain(text: str | None) -> str:
    return _SEPARATORS.sub(" ", (text or "").casefold()).strip()


def _is_generic_token(token: str, rules: NameRules) -> bool:
    return token in rules.generic_words or any(p.fullmatch(token) for p in rules.generic_tokens)


def comparable_name(text: str | None, rules: NameRules, *, file_name: bool = False) -> str | None:
    """The form two names are compared in, or None when the name cannot
    identify a work (missing, or made only of generic words). Re-save and
    revision markers are taken off file names only."""
    if file_name and text:
        stem = PurePath(text.replace("\\", "/")).stem.casefold().strip()
        changed = True
        while stem and changed:
            changed = False
            for marker in rules.file_name_markers:
                stripped = marker.sub("", stem).strip()
                if stripped != stem:
                    stem, changed = stripped, True
                    break
        text = stem
    name = _plain(text)
    if not name or all(_is_generic_token(token, rules) for token in name.split()):
        return None
    return name


def _identity_words(names: set[str], rules: NameRules) -> set[str]:
    return {
        token for name in names for token in name.split() if not _is_generic_token(token, rules)
    }


def _recorded_identity_disagrees(
    rules: NameRules,
    own: tuple[set[str], set[str], frozenset[str]],
    other: tuple[set[str], set[str], frozenset[str]],
    min_agreement: float,
) -> bool:
    """True when both records name a title or a team whose identifying words
    overlap less than `min_agreement` (shared words over all words), or both
    list authors and no author is on both lists. A retyped team label
    ("VERIDICAL" and "VERIDICAL Capstone Team") agrees; "Online Enrollment
    System" and "Online Attendance System" do not, because "online" and
    "system" are generic words."""
    for own_names, other_names in ((own[0], other[0]), (own[1], other[1])):
        own_words = _identity_words(own_names, rules)
        other_words = _identity_words(other_names, rules)
        if own_words and other_words:
            shared = len(own_words & other_words) / len(own_words | other_words)
            if shared < min_agreement:
                return True
    own_authors, other_authors = own[2], other[2]
    return bool(own_authors and other_authors and not own_authors & other_authors)


@dataclass(frozen=True)
class SameWorkSource:
    manuscript_id: int
    basis: SameWorkBasis
    similarity: float | None


@dataclass(frozen=True)
class SameWork:
    # The instructor's own records, strongest evidence first. These are named
    # in the lead.
    sources: list[SameWorkSource] = field(default_factory=list)
    # Everything left out of the comparison: the records above plus other
    # instructors' byte-identical copies of them.
    excluded_ids: set[int] = field(default_factory=set)


_BASIS_ORDER: dict[str, int] = {
    "same_file": 0,
    "near_identical_text": 1,
    "near_identical_text_names_differ": 2,
    "same_file_name": 3,
    "same_title": 4,
    "same_team": 5,
    "same_authors": 6,
}


async def find_same_work(
    session: AsyncSession,
    manuscript_id: int,
    *,
    instructor_id: int | None,
    content_hash: str | None,
    whole_document: list[float] | None,
    exclude_manuscript_ids: set[int],
    settings: Settings,
) -> SameWork:
    """Active Library records of the same instructor that are this
    manuscript's own work. `exclude_manuscript_ids` are records already left
    out of the comparison (the manuscript itself, its group siblings); they
    are not named again here."""
    if instructor_id is None:
        return SameWork()
    rules = load_name_rules(settings)

    own = (
        await session.execute(
            select(
                Manuscript.original_filename,
                Manuscript.created_at,
                Manuscript.group_id,
                Manuscript.group_label,
                Group.name.label("group_name"),
                Group.title.label("group_title"),
                LibraryEntry.title,
                LibraryEntry.team_name,
                LibraryEntry.authors,
            )
            .outerjoin(Group, Group.id == Manuscript.group_id)
            .outerjoin(LibraryEntry, LibraryEntry.manuscript_id == Manuscript.id)
            .where(Manuscript.id == manuscript_id)
        )
    ).first()
    if own is None:
        return SameWork()

    if whole_document is not None:
        similarity = (1 - ManuscriptArchive.embedding.cosine_distance(whole_document)).label(
            "similarity"
        )
    else:
        similarity = literal(None).label("similarity")
    candidates = (
        await session.execute(
            select(
                Manuscript.id,
                Manuscript.content_hash,
                Manuscript.original_filename,
                Manuscript.group_id,
                Manuscript.group_label,
                Group.name.label("group_name"),
                Group.title.label("group_title"),
                LibraryEntry.title,
                LibraryEntry.team_name,
                LibraryEntry.authors,
                LibraryEntry.school_year,
                similarity,
            )
            .join(LibraryEntry, LibraryEntry.manuscript_id == Manuscript.id)
            .outerjoin(Group, Group.id == Manuscript.group_id)
            .outerjoin(
                ManuscriptArchive,
                (ManuscriptArchive.manuscript_id == Manuscript.id)
                & (ManuscriptArchive.model_id == settings.embedding_model_id),
            )
            .where(
                Manuscript.instructor_id == instructor_id,
                Manuscript.id.notin_(exclude_manuscript_ids | {manuscript_id}),
                Manuscript.purged_at.is_(None),
                LibraryEntry.status == LibraryEntryStatus.active,
            )
            .order_by(Manuscript.id)
        )
    ).all()

    members = await _group_members(
        session, {own.group_id} | {row.group_id for row in candidates}, rules
    )
    own_file = comparable_name(own.original_filename, rules, file_name=True)
    own_identity = (
        _names(rules, own.title, own.group_title),
        _team_names(rules, own.group_name, own.group_label, own.team_name),
        _author_set(own.authors) or _member_set(members, own.group_id, own.group_name),
    )
    min_agreement = settings.reuse_same_work_name_agreement_min
    current_term = _term_start_year(own.created_at, settings)

    sources: list[SameWorkSource] = []
    for row in candidates:
        score = float(row.similarity) if row.similarity is not None else None
        identity = (
            _names(rules, row.title, row.group_title),
            _team_names(rules, row.group_label, row.team_name),
            _author_set(row.authors) or _member_set(members, row.group_id, row.group_name),
        )
        disagrees = _recorded_identity_disagrees(rules, own_identity, identity, min_agreement)
        basis: SameWorkBasis | None = None
        if content_hash is not None and row.content_hash == content_hash:
            # The same bytes are the same file whatever its records say.
            basis = "same_file"
        elif score is None:
            basis = None
        elif score >= settings.reuse_exact_duplicate_threshold:
            # BUG-140 collapses a near-exact duplicate into one lead. When the
            # records name another title, team or authors, the lead says so
            # and asks the instructor to confirm (a renamed team and a copied
            # past thesis look the same here); it is not a flood of leads.
            basis = "near_identical_text_names_differ" if disagrees else "near_identical_text"
        elif (
            score >= settings.reuse_same_work_similarity_threshold
            and not disagrees
            # A submission dated to an earlier school year than this upload
            # is another cohort's work; only the same file or near-exact
            # text can say otherwise.
            and not _earlier_school_year(row.school_year, current_term, settings)
        ):
            if own_file and own_file == comparable_name(
                row.original_filename, rules, file_name=True
            ):
                basis = "same_file_name"
            elif own_identity[0] & identity[0]:
                basis = "same_title"
            elif own_identity[1] & identity[1]:
                basis = "same_team"
            elif own_identity[2] and own_identity[2] == identity[2]:
                basis = "same_authors"
        if basis is not None:
            sources.append(SameWorkSource(row.id, basis, score))
    if not sources:
        return SameWork()
    sources.sort(key=lambda s: (_BASIS_ORDER[s.basis], -(s.similarity or 0.0), s.manuscript_id))

    excluded = {s.manuscript_id for s in sources}
    hashes = {row.content_hash for row in candidates if row.id in excluded and row.content_hash}
    if hashes:
        excluded |= set(
            (
                await session.scalars(
                    select(Manuscript.id)
                    .join(LibraryEntry, LibraryEntry.manuscript_id == Manuscript.id)
                    .where(
                        Manuscript.content_hash.in_(hashes),
                        Manuscript.id != manuscript_id,
                        LibraryEntry.status == LibraryEntryStatus.active,
                    )
                )
            ).all()
        )
    return SameWork(sources=sources, excluded_ids=excluded)


def _names(rules: NameRules, *names: str | None) -> set[str]:
    return {name for name in (comparable_name(n, rules) for n in names) if name}


def _team_names(rules: NameRules, *names: str | None) -> set[str]:
    """Team names that can identify a work. The shared "Ungrouped" bucket
    and generic labels never do."""
    found = set()
    for name in names:
        if _is_ungrouped(name):
            continue
        comparable = comparable_name(name, rules)
        if comparable:
            found.add(comparable)
    return found


def _author_set(authors: list[str] | None) -> frozenset[str]:
    return frozenset(name for name in (_plain(a) for a in authors or []) if name)


def _term_start_year(when: datetime | None, settings: Settings) -> int | None:
    """The calendar year the school year containing `when` began in."""
    if when is None:
        return None
    return when.year if when.month >= settings.reuse_school_year_start_month else when.year - 1


def _earlier_school_year(recorded: str | None, current: int | None, settings: Settings) -> bool:
    """True only when a recorded school year ("2024-2025") clearly began
    before the checked upload's. An unreadable or missing year is not taken
    as past-term."""
    if not recorded or current is None:
        return False
    found = re.search(settings.reuse_school_year_pattern, recorded)
    return found is not None and int(found.group(0)) < current


def _is_ungrouped(name: str | None) -> bool:
    return _plain(name) == _plain(DEFAULT_GROUP_LABEL)


async def _group_members(
    session: AsyncSession, group_ids: set[int | None], rules: NameRules
) -> dict[int, frozenset[str]]:
    """Names read from each group's title pages, standing in for an author
    list where no Library entry records one."""
    ids = {gid for gid in group_ids if gid is not None}
    if not ids:
        return {}
    found: dict[int, set[str]] = {}
    for group_id, name in (
        await session.execute(
            select(GroupMember.group_id, GroupMember.name).where(GroupMember.group_id.in_(ids))
        )
    ).all():
        plain = _plain(name)
        if plain:
            found.setdefault(group_id, set()).add(plain)
    return {gid: frozenset(names) for gid, names in found.items()}


def _member_set(
    members: dict[int, frozenset[str]], group_id: int | None, group_name: str | None
) -> frozenset[str]:
    # The shared "Ungrouped" bucket collects names from unrelated uploads.
    if group_id is None or _is_ungrouped(group_name):
        return frozenset()
    return members.get(group_id, frozenset())
