"""Loader for the heading-detection pattern data file.

The patterns are data on purpose (ENGINEERING.md §8): section-name synonyms
and numbering styles are exactly the kind of format assumption an instructor
could disagree with. Code here only compiles and validates the file.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_FILE = Path(__file__).parent / "data" / "heading_patterns.json"


@dataclass(frozen=True)
class TitlePagePatterns:
    """V-063: what to look for on a manuscript's own title page. Data,
    not code, for the same reason every other field in `HeadingPatterns`
    is — an instructor-facing document convention, not an engine rule
    (ground rule 7)."""

    by_markers: frozenset[str]
    adviser_markers: frozenset[str]
    degree_prefix: str
    # Casefolded degree-text substring -> Program.name. A key with no
    # matching row in the `program` table is simply never matched
    # (checked at use time, not here) -- this file can propose a program
    # name that doesn't exist yet without crashing; the proposal just
    # won't resolve to a real Program until one is added.
    degree_program_map: dict[str, str]
    title_separators: tuple[str, ...]


@dataclass(frozen=True)
class HeadingPatterns:
    chapter: re.Pattern[str]
    numbered: re.Pattern[str]
    top_numbered: re.Pattern[str]
    unnumbered_sections: frozenset[str]
    caption: re.Pattern[str]
    toc_titles: frozenset[str]
    # Word paragraph-style name prefixes that mark TOC/list-of-figures
    # content (DOCX path) — a listing of headings, not structure.
    toc_style_prefixes: tuple[str, ...]
    # Section titles that hold the reference list (F1.5).
    reference_titles: frozenset[str]
    # Chapter-title keywords whose images get vision priority (F1.3):
    # where result tables/statistics live. Substring match, casefolded.
    vision_priority_sections: tuple[str, ...]
    furniture: tuple[re.Pattern[str], ...]
    toc_line_suffix: re.Pattern[str]
    title_page: TitlePagePatterns


def load_patterns(path: Path | None = None) -> HeadingPatterns:
    raw = json.loads((path or _DEFAULT_FILE).read_text(encoding="utf-8"))
    ci = re.IGNORECASE
    title_page_raw = raw["title_page"]
    return HeadingPatterns(
        chapter=re.compile(raw["chapter"], ci),
        numbered=re.compile(raw["numbered"]),
        # Case-sensitive: the uppercase-start requirement in the pattern is
        # what keeps "1. some list item" from reading as a chapter.
        top_numbered=re.compile(raw["top_numbered"]),
        unnumbered_sections=frozenset(s.casefold() for s in raw["unnumbered_sections"]),
        caption=re.compile(raw["caption"], ci),
        toc_titles=frozenset(s.casefold() for s in raw["toc_titles"]),
        toc_style_prefixes=tuple(s.casefold() for s in raw["toc_style_prefixes"]),
        reference_titles=frozenset(s.casefold() for s in raw["reference_titles"]),
        vision_priority_sections=tuple(s.casefold() for s in raw["vision_priority_sections"]),
        furniture=tuple(re.compile(p, ci) for p in raw["furniture"]),
        toc_line_suffix=re.compile(raw["toc_line_suffix"]),
        title_page=TitlePagePatterns(
            by_markers=frozenset(s.casefold() for s in title_page_raw["by_markers"]),
            adviser_markers=frozenset(s.casefold() for s in title_page_raw["adviser_markers"]),
            degree_prefix=title_page_raw["degree_prefix"].casefold(),
            degree_program_map={
                k.casefold(): v for k, v in title_page_raw["degree_program_map"].items()
            },
            title_separators=tuple(title_page_raw["title_separators"]),
        ),
    )
