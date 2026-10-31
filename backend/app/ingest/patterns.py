"""Loader for the heading-detection pattern data file.

The patterns are data on purpose (ENGINEERING.md §8): section-name synonyms
and numbering styles are exactly the kind of format assumption an instructor
could disagree with. Code here only compiles and validates the file.
"""

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from app.ingest.normalize import normalize

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
    # These headings alone do not establish substantive chapter spans (F7).
    # Exact normalized titles only; never substring-based exclusions.
    reuse_ancillary_titles: frozenset[str]
    # Numbering preceding a reference title, if this document uses it.
    reference_heading_prefix: re.Pattern[str]
    # Later headings that end a bare reference list when no section tree
    # exists (F1.5). Document conventions belong in the data file.
    reference_end_headings: tuple[re.Pattern[str], ...]
    reference_history_lines: tuple[re.Pattern[str], ...]
    reference_end_min_bold_ratio: float
    reference_end_min_font_scale: float
    reference_url_geometry_tolerance_pts: float
    standalone_heading_label: re.Pattern[str]
    # Chapter-title keywords whose images get vision priority (F1.3):
    # where result tables/statistics live. Substring match, casefolded.
    vision_priority_sections: tuple[str, ...]
    # Cues that an image may hold a table or an equation (F1.3).
    vision_table_caption: re.Pattern[str]
    vision_equation_number: re.Pattern[str]
    vision_caption_gap_pts: float
    vision_column_slack_pts: float
    furniture: tuple[re.Pattern[str], ...]
    toc_line_suffix: re.Pattern[str]
    title_page: TitlePagePatterns

    def is_reference_heading(self, text: str, *, allow_numbered: bool = True) -> bool:
        title = normalize(text).casefold()
        if title in self.reference_titles:
            return True
        if not allow_numbered:
            return False
        prefix = self.reference_heading_prefix.match(title)
        return prefix is not None and normalize(title[prefix.end() :]) in self.reference_titles


def _non_negative(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"vision_cues.{name} must be a non-negative number")
    return float(value)


def load_patterns(path: Path | None = None) -> HeadingPatterns:
    raw = json.loads((path or _DEFAULT_FILE).read_text(encoding="utf-8"))
    ci = re.IGNORECASE
    title_page_raw = raw["title_page"]
    reference_end_headings = raw.get("reference_end_headings")
    if reference_end_headings is None:
        # Older custom heading files predate this boundary field. Preserve
        # the generic default instead of silently restoring EOF absorption.
        defaults = json.loads(_DEFAULT_FILE.read_text(encoding="utf-8"))
        reference_end_headings = defaults["reference_end_headings"]
    default_prefix = json.loads(_DEFAULT_FILE.read_text(encoding="utf-8"))[
        "reference_heading_prefix"
    ]
    prefix_text = raw.get("reference_heading_prefix", default_prefix)
    if not isinstance(prefix_text, str) or not prefix_text.startswith("^"):
        raise ValueError("Reference heading prefix must be an anchored regex")
    reference_heading_prefix = re.compile(prefix_text, ci)
    if reference_heading_prefix.match(""):
        raise ValueError("Reference heading prefix must not match empty text")
    default_end_style = json.loads(_DEFAULT_FILE.read_text(encoding="utf-8"))[
        "reference_end_heading_style"
    ]
    end_style = raw.get("reference_end_heading_style", default_end_style)
    min_bold_ratio = float(end_style["min_bold_ratio"])
    min_font_scale = float(end_style["min_font_scale"])
    if (
        not math.isfinite(min_bold_ratio)
        or not 0 < min_bold_ratio <= 1
        or not math.isfinite(min_font_scale)
        or min_font_scale <= 1
    ):
        raise ValueError("Reference end-heading style thresholds must require positive contrast")
    default_url_tolerance = json.loads(_DEFAULT_FILE.read_text(encoding="utf-8"))[
        "reference_url_geometry_tolerance_pts"
    ]
    url_tolerance = float(raw.get("reference_url_geometry_tolerance_pts", default_url_tolerance))
    if not math.isfinite(url_tolerance) or url_tolerance < 0:
        raise ValueError("Reference URL geometry tolerance must be finite and nonnegative")
    label_pattern = raw.get("standalone_heading_label")
    if label_pattern is None:
        label_pattern = json.loads(_DEFAULT_FILE.read_text(encoding="utf-8"))[
            "standalone_heading_label"
        ]
    ancillary_titles = raw.get(
        "reuse_ancillary_titles",
        json.loads(_DEFAULT_FILE.read_text(encoding="utf-8"))["reuse_ancillary_titles"],
    )
    if not isinstance(ancillary_titles, list) or any(
        not isinstance(title, str) or not normalize(title) for title in ancillary_titles
    ):
        raise ValueError("Reuse ancillary titles must be a list of nonempty strings")
    # A custom patterns file written before these cues existed keeps working.
    cues = raw.get(
        "vision_cues", json.loads(_DEFAULT_FILE.read_text(encoding="utf-8"))["vision_cues"]
    )
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
        reuse_ancillary_titles=frozenset(normalize(s).casefold() for s in ancillary_titles),
        reference_heading_prefix=reference_heading_prefix,
        reference_end_headings=tuple(re.compile(pattern, ci) for pattern in reference_end_headings),
        reference_history_lines=tuple(
            re.compile(pattern, ci)
            for pattern in raw.get(
                "reference_history_lines",
                json.loads(_DEFAULT_FILE.read_text(encoding="utf-8"))["reference_history_lines"],
            )
        ),
        reference_end_min_bold_ratio=min_bold_ratio,
        reference_end_min_font_scale=min_font_scale,
        reference_url_geometry_tolerance_pts=url_tolerance,
        standalone_heading_label=re.compile(label_pattern, ci),
        vision_priority_sections=tuple(s.casefold() for s in raw["vision_priority_sections"]),
        vision_table_caption=re.compile(cues["table_caption"], ci),
        vision_equation_number=re.compile(cues["equation_number"], ci),
        vision_caption_gap_pts=_non_negative(cues["caption_gap_pts"], "caption_gap_pts"),
        vision_column_slack_pts=_non_negative(cues["column_slack_pts"], "column_slack_pts"),
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
