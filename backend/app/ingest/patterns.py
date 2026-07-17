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
class HeadingPatterns:
    chapter: re.Pattern[str]
    numbered: re.Pattern[str]
    top_numbered: re.Pattern[str]
    unnumbered_sections: frozenset[str]
    caption: re.Pattern[str]
    toc_titles: frozenset[str]
    furniture: tuple[re.Pattern[str], ...]
    toc_line_suffix: re.Pattern[str]


def load_patterns(path: Path | None = None) -> HeadingPatterns:
    raw = json.loads((path or _DEFAULT_FILE).read_text(encoding="utf-8"))
    ci = re.IGNORECASE
    return HeadingPatterns(
        chapter=re.compile(raw["chapter"], ci),
        numbered=re.compile(raw["numbered"]),
        # Case-sensitive: the uppercase-start requirement in the pattern is
        # what keeps "1. some list item" from reading as a chapter.
        top_numbered=re.compile(raw["top_numbered"]),
        unnumbered_sections=frozenset(s.casefold() for s in raw["unnumbered_sections"]),
        caption=re.compile(raw["caption"], ci),
        toc_titles=frozenset(s.casefold() for s in raw["toc_titles"]),
        furniture=tuple(re.compile(p, ci) for p in raw["furniture"]),
        toc_line_suffix=re.compile(raw["toc_line_suffix"]),
    )
