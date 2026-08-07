"""Statistics extraction (F6.1, V-031): inferential test results (t/F/
chi2/r/Z/Q) from body text via `statcheck_python`'s own extraction
regexes (D-003 — reuse a validated library, never reimplement; GPL-3.0
compatibility recorded in DECISIONS.md D-022), and descriptive stats
(n/mean/SD/%) from table columns, which statcheck doesn't provide at all.

Called PER TEXT BLOCK (not once on the whole joined document) so every
extracted stat carries a real page/paragraph anchor — statcheck's own
`extract_stats` returns character offsets into whatever string you gave
it, but doesn't carry them through to its output rows, so per-block calls
are the simplest way to get real anchors without re-deriving its offset
bookkeeping.

**Two real normalization gaps found and fixed via live testing against
statcheck's own extractor (not assumed from its docs)**: (1) it expects
chi-square as a literal "X" (its own `file_to_txt.py` does the same
replacement for HTML chi entities — extended here for the raw Unicode χ/²
characters PyMuPDF actually extracts, which its HTML-entity replacement
never covers); (2) a thousands-separator comma inside a number (`N =
1,000`) breaks its df/n parsing entirely — stripped here with a regex
narrow enough to never touch the ordinary APA comma between clauses
("t(28) = 2.45, p = .021" is unaffected, verified).
"""

import contextlib
import io
import re
import warnings
from dataclasses import dataclass
from typing import Literal

from statcheck.extract_stats import extract_stats

from app.checks.forensics.keywords import ForensicsKeywords, load_keywords
from app.ingest.schemas import ExtractionResult, TableBlock, TextBlock

_STAT_TYPES = ["t", "F", "r", "Chi2", "Z", "Q"]

# statcheck's own file_to_txt.py replaces HTML chi-entities with "X" for
# the same reason — its RGX_CHI2 pattern is keyed on a literal X, not the
# Unicode character. PyMuPDF/python-docx extraction gives us the raw
# Unicode glyphs directly (no HTML), so this covers what that replacement
# doesn't.
_CHI_UNICODE = re.compile("[χΧ]")
_SUPERSCRIPT_TWO = "²"
# Only a comma directly between digits, followed by exactly a 3-digit
# group (thousands separator shape) — NOT the ordinary APA comma between
# a test statistic and its p-value ("t(28) = 2.45, p = .021" must survive
# untouched; verified live).
_THOUSANDS_COMMA = re.compile(r"(?<=\d),(?=\d{3}\b)")


def normalize_for_statcheck(text: str) -> str:
    text = _CHI_UNICODE.sub("X", text)
    text = text.replace(_SUPERSCRIPT_TWO, "2")
    return _THOUSANDS_COMMA.sub("", text)


@dataclass(frozen=True)
class ReportedStat:
    """One extracted statistic, typed and anchored — the shared input
    contract V-032 (GRIM/GRIMMER/SPRITE) and V-033 (statcheck p-recalc)
    both consume."""

    kind: Literal["inferential", "descriptive"]
    anchor: str
    page: int | None
    paragraph: int | None
    source: Literal["text", "table", "image_table"]
    raw_text: str
    low_confidence: bool = False
    # --- inferential (from body text) ---
    test_type: str | None = None  # "t" | "F" | "r" | "Chi2" | "Z" | "Q" | "Qb" | "Qw"
    df1: float | None = None
    df2: float | None = None
    test_value: float | None = None
    test_comparison: str | None = None  # "=" (statcheck only ever reports this)
    p_value: float | None = None
    p_comparison: str | None = None  # "=" | "<" | ">"
    # Decimal-place counts statcheck's own extractor already computes
    # (its `test_dec`/`dec` columns) — needed by V-033's p-recalculation
    # (rounding tolerance depends on reported precision), not by V-032's
    # GRIM/GRIMMER (descriptive-stat precision only, read from raw_text).
    test_value_precision: int | None = None
    p_value_precision: int | None = None
    # --- descriptive (from table columns) ---
    stat_name: Literal["n", "mean", "sd", "percentage"] | None = None
    value: float | None = None
    group_label: str | None = None  # the row's own first-column label, if any


def _anchor_for(*, page: int | None, paragraph: int | None) -> str:
    if page is not None:
        return f"p. {page}"
    if paragraph is not None:
        return f"¶{paragraph}"
    return "document"


def _as_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_inferential_stats(blocks: list[TextBlock]) -> list[ReportedStat]:
    out: list[ReportedStat] = []
    for block in blocks:
        if block.is_furniture or not block.text.strip():
            continue
        normalized = normalize_for_statcheck(block.text)
        try:
            # statcheck's own extract_stats() `print()`s a snippet of the
            # matched text on certain internal parse failures (read in its
            # source, not just docs) — never let that reach real logs
            # (charter rule: no manuscript text in logs), whatever else
            # happens to this call.
            with (
                warnings.catch_warnings(),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                warnings.simplefilter("ignore")
                df = extract_stats(normalized, stat=_STAT_TYPES, name="block")
        except Exception:  # noqa: BLE001 — QA property: never crash on arbitrary text
            continue
        if df.empty:
            continue
        anchor = _anchor_for(page=block.page, paragraph=block.paragraph)
        for row in df.to_dict("records"):
            out.append(
                ReportedStat(
                    kind="inferential",
                    anchor=anchor,
                    page=block.page,
                    paragraph=block.paragraph,
                    source="text",
                    raw_text=str(row.get("Raw", "")),
                    test_type=row.get("Statistic"),
                    df1=_as_float(row.get("df1")),
                    df2=_as_float(row.get("df2")),
                    test_value=_as_float(row.get("Value")),
                    test_comparison=row.get("Test_Comparison"),
                    p_value=_as_float(row.get("Reported_P_Value")),
                    p_comparison=row.get("Reported_Comparison"),
                    test_value_precision=_as_int(row.get("test_dec")),
                    p_value_precision=_as_int(row.get("dec")),
                )
            )
    return out


def _match_columns(header: list[str], keywords: ForensicsKeywords) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(header):
        normalized = cell.strip().casefold()
        if normalized in keywords.n_headers:
            mapping.setdefault("n", idx)
        elif normalized in keywords.mean_headers:
            mapping.setdefault("mean", idx)
        elif normalized in keywords.sd_headers:
            mapping.setdefault("sd", idx)
        elif normalized in keywords.percentage_headers:
            mapping.setdefault("percentage", idx)
    return mapping


def _parse_number(text: str) -> float | None:
    cleaned = _THOUSANDS_COMMA.sub("", text.strip()).rstrip("%").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_descriptive_stats(
    tables: list[TableBlock], keywords: ForensicsKeywords | None = None
) -> list[ReportedStat]:
    keywords = keywords or load_keywords()
    out: list[ReportedStat] = []
    for table in tables:
        if table.low_confidence or not table.rows:
            # V-007 contract: a vision read the model itself wasn't sure
            # about must never feed a forensics check (ticket AC).
            continue
        header = table.rows[0]
        col_map = _match_columns(header, keywords)
        if not col_map:
            continue
        anchor = _anchor_for(page=table.page, paragraph=table.paragraph)
        source: Literal["table", "image_table"] = (
            "image_table" if table.source == "vision" else "table"
        )
        for row in table.rows[1:]:
            group_label = row[0].strip() if row and row[0].strip() else None
            for stat_name, col_idx in col_map.items():
                if col_idx >= len(row):
                    continue
                value = _parse_number(row[col_idx])
                if value is None:
                    continue
                out.append(
                    ReportedStat(
                        kind="descriptive",
                        anchor=anchor,
                        page=table.page,
                        paragraph=table.paragraph,
                        source=source,
                        raw_text=row[col_idx],
                        low_confidence=table.low_confidence,
                        stat_name=stat_name,  # type: ignore[arg-type]
                        value=value,
                        group_label=group_label,
                    )
                )
    return out


def extract_all_stats(extraction: ExtractionResult) -> list[ReportedStat]:
    body_blocks = [b for b in extraction.blocks if not b.is_furniture]
    return [
        *extract_inferential_stats(body_blocks),
        *extract_descriptive_stats(extraction.tables),
    ]
