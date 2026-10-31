"""Which part of a thesis a statement, quote or page belongs to.

Shared by the checks that must not take evidence from the wrong part of a
manuscript: internal agreement only pairs objectives with the thesis's own
results (a literature-review summary is not this study's finding), and the
grading support check needs a structure preview to come from the
introduction, not the table of contents.

A role (introduction, literature, methodology, results, conclusion,
references, ...) is read from chapter and heading titles; the titles each
role covers are data (`data/section_roles.json`). Chapters come from the
section tree when it has chapter structure, otherwise from chapter headings
in the text. When neither gives a single recognisable chapter, `identified`
is False and callers say so rather than guess.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.checks.agreement.context import Sentence, _locate, document_sentences
from app.ingest.schemas import ExtractionResult

_DEFAULT_FILE = Path(__file__).parent / "data" / "section_roles.json"
_NUMBERED_CHAPTER = re.compile(r"^\d+\s+\S")
_STARTS_WITH_NUMBER = re.compile(r"^\d")


@dataclass(frozen=True)
class SectionRoles:
    roles: tuple[tuple[str, tuple[re.Pattern, ...]], ...]
    chapter_patterns: tuple[str, ...]

    def role_of(self, title: str | None) -> str | None:
        """The role a title names, "other" when it names none, None for no title."""
        if not title:
            return None
        lowered = title.lower()
        for role, patterns in self.roles:
            if any(p.search(lowered) for p in patterns):
                return role
        return "other"


@lru_cache(maxsize=4)
def load_section_roles(path: Path | None = None) -> SectionRoles:
    raw = json.loads((path or _DEFAULT_FILE).read_text(encoding="utf-8"))
    return SectionRoles(
        roles=tuple(
            (item["role"], tuple(re.compile(p) for p in item["patterns"])) for item in raw["roles"]
        ),
        chapter_patterns=tuple(raw["chapter_heading_patterns"]),
    )


def tree_chapters(extraction: ExtractionResult, roles: SectionRoles) -> list[tuple[int, str]]:
    """(first page, title) of the document's chapters from its section tree,
    or nothing when the tree has no chapter structure.

    Titles that look like chapters ("CHAPTER 3: ...") come first; otherwise
    numbered top-level titles ("3 AirSpecs ..."). Heuristic trees also put
    numbered steps at the top level, so numbered titles are used only when
    nothing looks like a chapter. Unnumbered front and back matter (the
    abstract, a biography, the references) count beside them, but a tree of
    front and back matter alone is not a chapter structure: everything
    between would look like one long "Definition of Terms"."""
    nodes = [(n.page, n.title) for n in extraction.section_tree.nodes if n.page is not None]
    patterns = [re.compile(p) for p in roles.chapter_patterns]
    chapter_like = [(p, t) for p, t in nodes if any(r.search(t.lower()) for r in patterns)]
    numbered = [(p, t) for p, t in nodes if _NUMBERED_CHAPTER.match(t)]
    structural = chapter_like or numbered
    if not structural:
        return []
    candidates = set(chapter_like) | set(numbered)
    # A table of contents read as headings lists "Chapter 4 ..." pages before
    # the chapter itself; when one chapter number appears twice, the later page
    # is the chapter and the earlier entry is dropped.
    last_page: dict[int, int] = {}
    for page, title in structural:
        number = chapter_number(title)
        if number is not None:
            last_page[number] = max(page, last_page.get(number, page))
    structural = [
        (p, t)
        for p, t in structural
        if chapter_number(t) is None or last_page[chapter_number(t)] == p
    ]
    # Front and back matter: unnumbered titles that are not chapter entries, so a
    # contents entry dropped above does not come back in as matter.
    matter = [
        (p, t) for p, t in nodes if (p, t) not in candidates and not _STARTS_WITH_NUMBER.match(t)
    ]
    return sorted(structural + matter)


_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50}
_CHAPTER_NUMBER = re.compile(r"^(?:(?:chapter|part)\s+)?([0-9]+|[ivxl]+)\b[\s.:)-]", re.IGNORECASE)


def _roman(text: str) -> int | None:
    values = [_ROMAN.get(c) for c in text.lower()]
    if None in values:
        return None
    total = 0
    for value, after in zip(values, [*values[1:], 0], strict=True):
        total += -value if value < after else value
    return total


def chapter_number(title: str | None) -> int | None:
    """The chapter's ordinal ("CHAPTER 3: ..." or "3 AirSpecs ..." is 3,
    "Chapter IV" is 4), or None for an unnumbered title."""
    if not title:
        return None
    match = _CHAPTER_NUMBER.match(title.strip() + " ")
    if not match:
        return None
    token = match.group(1)
    if token.isdigit():
        return int(token)
    # A bare Roman numeral counts only after the word "chapter" or "part":
    # "I." alone is too often a list marker.
    return _roman(token) if re.match(r"^(chapter|part)\s", title.strip(), re.IGNORECASE) else None


def _chapter_at(chapters: list[tuple[int, str]], page: int | None) -> str | None:
    if page is None:
        return None
    title = None
    for first_page, chapter in chapters:
        if first_page <= page:
            title = chapter
    return title


@dataclass(frozen=True)
class Location:
    chapter: str | None
    chapter_role: str | None
    # The chapter's ordinal, None when unnumbered: a format's "introduction"
    # can be a thesis's "Chapter 1 The Problem and Its Background".
    chapter_number: int | None
    heading: str | None
    heading_role: str | None


class SectionMap:
    """Answers "where in the thesis is this?" for statements, quotes and pages."""

    def __init__(
        self,
        extraction: ExtractionResult,
        roles: SectionRoles,
        *,
        sentences: list[Sentence] | None = None,
        heading_max_words: int,
        locate_chars: int,
    ):
        self.roles, self.locate_chars = roles, locate_chars
        self.sentences = (
            sentences
            if sentences is not None
            else document_sentences(
                extraction,
                heading_max_words=heading_max_words,
                chapter_patterns=roles.chapter_patterns,
            )
        )
        self.tree = tree_chapters(extraction, roles)
        if any(roles.role_of(t) not in (None, "other") for _, t in self.tree):
            self.source = "section_tree"
        elif any(roles.role_of(s.chapter) not in (None, "other") for s in self.sentences):
            self.source = "inline_headings"
        else:
            self.source = "none"
        self.identified = self.source != "none"

    def locate(self, text: str, anchor: str, page: int | None) -> Location:
        """Where a statement or quote sits: its chapter and nearest heading, each
        with its role. A sentence at the given anchor wins over a repeat."""
        from app.checks.agreement.extract import Statement

        probe = Statement("outcome", text, anchor, page, None, "")
        found = _locate(probe, self.sentences, self.locate_chars)
        sentence = self.sentences[found] if found is not None else None
        if self.source == "section_tree":
            chapter = _chapter_at(self.tree, page)
        else:
            chapter = sentence.chapter if sentence else None
        heading = sentence.heading if sentence else None
        return Location(
            chapter,
            self.roles.role_of(chapter),
            chapter_number(chapter),
            heading,
            self.roles.role_of(heading),
        )

    def page_roles(self, pages: list[int]) -> dict[int, str | None]:
        """The role of the chapter each page belongs to."""
        if self.source == "section_tree":
            return {page: self.roles.role_of(_chapter_at(self.tree, page)) for page in pages}
        by_page: dict[int, str | None] = {}
        for sentence in self.sentences:
            match = re.search(r"\d+", sentence.anchor)
            if match:
                by_page.setdefault(int(match.group()), self.roles.role_of(sentence.chapter))
        return by_page
