"""Conservative F5.1 bracketed-number linkage with source-number provenance.

This does not infer a reference number from its list position. It only maps a
complete inventory of unique, explicitly labelled entries. Body marks
retain their exact source offsets; uncertain marks never prove a reference is
unused. Parenthetical numbers and superscripts are deliberately unsupported.
"""

import json
import re
from bisect import bisect_right
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Literal, Protocol

from app.ingest.schemas import TextBlock
from app.ingest.source_slices import SourceFragment

_DEFAULT_CONTEXT_FILE = Path(__file__).parent / "data" / "numeric_context.json"
_REFERENCE_LABEL = re.compile(
    r"^\s*(?P<label>\[(?P<bracket>[1-9]\d*)\]|(?P<plain>[1-9]\d*)[.)])"
    r"(?:\s|(?=[A-Z])|$)"
)
_ANY_NUMBERED_REFERENCE = re.compile(r"^\s*(?:\[\d+\]|\(\d+\)|\d+[.)])(?:\s|(?=[A-Z])|$)")
_SECOND_BRACKET_LABEL = re.compile(r"\[[1-9]\d*\]")
_SECOND_PLAIN_LABEL = re.compile(r"(?<!\w)([1-9]\d*)[.)](?=\s+[^\W\d_])")
# The atomic repeated-number group prevents retrying the same comma/range
# segments when a long malformed mark has no closing bracket.
_BODY_BRACKET = re.compile(r"(?<![\w\[])\[\s*[1-9]\d*(?>(?:\s*[,;\-\u2013]\s*[1-9]\d*)*)\s*\]")
_SINGLE_BRACKET = re.compile(r"\[\s*[1-9]\d*\s*\]")
# Marks that may cite a source but cannot carry a finding (2026-10-07, found
# on real theses): glued to the preceding word as LaTeX output often prints
# them ("software[25]", "GPT Codex[4]"), or carrying a locator
# ("[13, Introduction]", "[7, p. 12]"). Their numbers are never "unused".
_GLUED_BRACKET = re.compile(r"(?<=[\w)])\[\s*[1-9]\d*(?:\s*[,;\-–]\s*[1-9]\d*)*\s*\]")
# The numbers before a locator may be a list or a range ("[7-10, pp. 3-9]").
_LOCATOR_BRACKET = re.compile(
    r"\[\s*(?P<numbers>[1-9]\d*(?:\s*[-–]\s*[1-9]\d*)?"
    r"(?:\s*[,;]\s*[1-9]\d*(?:\s*[-–]\s*[1-9]\d*)?)*)"
    r"\s*[,;]\s*[^\]\d\s][^\]]{0,80}\]"
)
_BETWEEN_RANGE_MARKS = re.compile(r"\s*[-–—]\s*")
_RANGE = re.compile(r"([1-9]\d*)\s*[-\u2013]\s*([1-9]\d*)")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])")


class ReferenceLike(Protocol):
    order_index: int
    raw_text: str


def explicit_reference_label(raw_text: str) -> str | None:
    """The printed leading marker, independent of row position."""
    match = _REFERENCE_LABEL.match(raw_text)
    return match.group("label") if match else None


def reference_anchor(raw_text: str, order_index: int) -> str:
    """Use a printed source number when present, otherwise the row ordinal."""
    match = _REFERENCE_LABEL.match(raw_text)
    number = (match.group("bracket") or match.group("plain")) if match else None
    return f"reference #{number or order_index + 1}"


@dataclass(frozen=True)
class NumericContext:
    lookback_chars: int
    max_number_digits: int
    evidence_excerpt_max_chars: int
    max_mark_chars: int
    max_list_items: int
    max_body_scan_chars: int
    max_body_marks: int
    noncitation_prefix_patterns: tuple[re.Pattern[str], ...]
    citation_prefix_patterns: tuple[re.Pattern[str], ...]
    # A mark is a STRONG citation when it follows running prose (or another
    # mark) and ends a clause or continues the sentence: "approaches [3]."
    # Optional in older data files: absent lists keep the older behaviour.
    prose_prefix_patterns: tuple[re.Pattern[str], ...] = ()
    citation_suffix_patterns: tuple[re.Pattern[str], ...] = ()
    # Before an "unused reference" can be asserted, at least this share of the
    # numbered list must be linked by strong marks; otherwise the paper may
    # cite in a form this parser does not read (superscripts).
    min_linked_fraction: float = 0.0


@lru_cache(maxsize=8)
def load_numeric_context(path: Path | None = None) -> NumericContext:
    raw = json.loads((path or _DEFAULT_CONTEXT_FILE).read_text(encoding="utf-8"))
    if any(
        int(raw[field]) < 1
        for field in (
            "lookback_chars",
            "max_number_digits",
            "evidence_excerpt_max_chars",
            "max_mark_chars",
            "max_list_items",
            "max_body_scan_chars",
            "max_body_marks",
        )
    ):
        raise ValueError("numeric citation context bounds must be positive")
    max_mark_chars = int(raw["max_mark_chars"])
    if max_mark_chars < 3:
        raise ValueError("numeric citation max_mark_chars must fit brackets and a digit")
    return NumericContext(
        lookback_chars=int(raw["lookback_chars"]),
        max_number_digits=int(raw["max_number_digits"]),
        evidence_excerpt_max_chars=int(raw["evidence_excerpt_max_chars"]),
        max_mark_chars=max_mark_chars,
        max_list_items=int(raw["max_list_items"]),
        max_body_scan_chars=int(raw["max_body_scan_chars"]),
        max_body_marks=int(raw["max_body_marks"]),
        noncitation_prefix_patterns=tuple(
            re.compile(pattern, re.IGNORECASE) for pattern in raw["noncitation_prefix_patterns"]
        ),
        citation_prefix_patterns=tuple(
            re.compile(pattern, re.IGNORECASE) for pattern in raw["citation_prefix_patterns"]
        ),
        prose_prefix_patterns=tuple(
            re.compile(pattern) for pattern in raw.get("prose_prefix_patterns", ())
        ),
        citation_suffix_patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in raw.get("citation_suffix_patterns", ())
        ),
        min_linked_fraction=float(raw.get("min_linked_fraction", 0.0)),
    )


@dataclass(frozen=True)
class NumericMark:
    raw_text: str
    numbers: tuple[int, ...] | None
    classification: Literal["citation", "ambiguous", "noncitation"]
    strong_citation_context: bool
    anchor: str
    page: int | None
    paragraph: int | None
    block_index: int
    start: int
    claim_sentence: str
    excerpt_cropped: bool


@dataclass(frozen=True)
class NumberedCitationAssessment:
    n_numbered_reference_entries: int
    n_numeric_body_candidates: int
    inventory_complete: bool
    marks: list[NumericMark]
    linked: list[tuple[NumericMark, list[ReferenceLike]]]
    orphan_marks: list[NumericMark]
    uncited: list[ReferenceLike]
    unresolved_marks: list[NumericMark]
    n_linkage_unassessed: int
    scan_limited: bool = False
    confidently_uncited: list[ReferenceLike] = field(default_factory=list)
    absence_unassessed_references: list[ReferenceLike] = field(default_factory=list)
    absence_assessment_complete: bool = False

    @property
    def linkage_unassessed(self) -> bool:
        return self.scan_limited or self.n_linkage_unassessed > 0 or bool(self.unresolved_marks)

    @property
    def n_credible_body_marks(self) -> int:
        return sum(mark.classification != "noncitation" for mark in self.marks)


def _numbers_in_mark(
    raw_text: str, *, max_range_span: int, context: NumericContext
) -> tuple[int, ...] | None:
    if len(raw_text) > context.max_mark_chars:
        return None
    numbers: list[int] = []
    items = re.split(r"[,;]", raw_text[1:-1])
    if len(items) > context.max_list_items:
        return None
    for item in items:
        item = item.strip()
        range_match = _RANGE.fullmatch(item)
        if range_match:
            if any(len(part) > context.max_number_digits for part in range_match.groups()):
                return None
            start, end = (int(part) for part in range_match.groups())
            if end < start or end - start + 1 > max_range_span:
                return None
            numbers.extend(range(start, end + 1))
        elif re.fullmatch(r"[1-9]\d*", item):
            if len(item) > context.max_number_digits:
                return None
            numbers.append(int(item))
        else:
            return None
    return tuple(dict.fromkeys(numbers)) if numbers else None


def bracket_numbers_in_text(
    text: str, *, max_range_span: int, context: NumericContext
) -> tuple[set[int], bool]:
    """Every reference number a bracket mark in `text` could cite, read the way
    the body scan reads them: lists, ranges ("[7-10]" is 7 to 10) and IEEE
    bridges ("[3]-[6]" is 3 to 6). Also says whether some mark could not be
    read, so a caller that only withholds can treat that text as citing
    anything (BUG-619)."""
    numbers, unreadable_kinds = _read_marks(text, max_range_span=max_range_span, context=context)
    return numbers, bool(unreadable_kinds)


def _read_marks(
    text: str, *, max_range_span: int, context: NumericContext
) -> tuple[set[int], set[str]]:
    """The numbers every bracket mark in `text` could cite, body, glued
    ("compared[7-10]") and locator ("[8, p. 3]") marks alike, read in text
    order so a bridge works whichever kind starts it ("compared[7]-[10]" is 7
    to 10). Also the kinds of mark that could not be read."""
    marks = [(m.start(), m.end(), m.group(), "body") for m in _BODY_BRACKET.finditer(text)] + [
        (m.start(), m.end(), m.group(), "glued") for m in _GLUED_BRACKET.finditer(text)
    ]
    marks += [
        (m.start(), m.end(), f"[{m.group('numbers')}]", "locator")
        for m in _LOCATOR_BRACKET.finditer(text)
    ]
    numbers: set[int] = set()
    unreadable: set[str] = set()
    prior: tuple[int, ...] | None = None
    prior_end = -1
    for start, end, raw, kind in sorted(marks):
        found = _numbers_in_mark(raw, max_range_span=max_range_span, context=context)
        if found is None:
            # An unreadable mark in a non-citation context ("Table[1-99]") is
            # not a citation the scan missed, as for a body mark.
            prefix = text[max(0, start - context.lookback_chars) : start]
            if not any(p.search(prefix) for p in context.noncitation_prefix_patterns):
                unreadable.add(kind)
            prior = None
            continue
        if prior and _BETWEEN_RANGE_MARKS.fullmatch(text[prior_end:start]):
            low, high = prior[-1], found[0]
            if low < high and high - low <= max_range_span:
                numbers.update(range(low + 1, high))
        numbers.update(found)
        prior, prior_end = found, end
    return numbers, unreadable


def _sentence_at(
    text: str,
    start: int,
    end: int,
    boundaries: list[tuple[int, int]],
    boundary_ends: list[int],
    *,
    max_chars: int,
) -> tuple[str, bool]:
    index = bisect_right(boundary_ends, start)
    sentence_start = boundaries[index - 1][1] if index else 0
    sentence_end = boundaries[index][0] if index < len(boundaries) else len(text)
    if sentence_end - sentence_start <= max_chars:
        return text[sentence_start:sentence_end].strip(), False
    # Keep an EXACT source substring so the highlighter can locate it.
    # Cropping is disclosed separately, never by synthetic ellipsis bytes.
    window_start = max(sentence_start, start - max_chars // 2)
    window_end = min(sentence_end, window_start + max_chars)
    window_start = max(sentence_start, window_end - max_chars)
    if end > window_end:
        window_end = min(sentence_end, end)
        window_start = max(sentence_start, window_end - max_chars)
    return text[window_start:window_end].strip(), True


def _body_marks(
    blocks: list[TextBlock],
    *,
    max_range_span: int,
    context: NumericContext,
    source_block_index_by_id: dict[int, int] | None,
) -> tuple[list[NumericMark], bool]:
    marks: list[NumericMark] = []
    scanned_chars = 0
    scan_limited = False
    for block_index, block in enumerate(blocks):
        source_text = block.text or ""
        remaining_chars = context.max_body_scan_chars - scanned_chars
        if remaining_chars <= 0:
            if source_text:
                scan_limited = True
                break
            continue
        text = source_text[:remaining_chars]
        scanned_chars += len(text)
        if len(text) < len(source_text):
            scan_limited = True
        prior_mark: NumericMark | None = None
        boundaries = [(match.start(), match.end()) for match in _SENTENCE_BOUNDARY.finditer(text)]
        boundary_ends = [after for _before, after in boundaries]
        scan_pos = 0
        bracket_depth = 0
        for match in _BODY_BRACKET.finditer(text):
            prefix = text[max(0, match.start() - context.lookback_chars) : match.start()]
            for character in text[scan_pos : match.start()]:
                if character == "[":
                    bracket_depth += 1
                elif character == "]":
                    bracket_depth = max(0, bracket_depth - 1)
            nested = bracket_depth > 0
            for character in match.group():
                if character == "[":
                    bracket_depth += 1
                elif character == "]":
                    bracket_depth = max(0, bracket_depth - 1)
            scan_pos = match.end()
            if nested or match.start() == 0:
                classification: Literal["citation", "ambiguous", "noncitation"] = "ambiguous"
                strong_context = False
            elif any(pattern.search(prefix) for pattern in context.noncitation_prefix_patterns):
                classification = "noncitation"
                strong_context = False
            elif any(pattern.search(prefix) for pattern in context.citation_prefix_patterns) or (
                prior_mark is not None
                and prior_mark.start + len(prior_mark.raw_text) == match.start()
                and prior_mark.classification == "citation"
            ):
                classification = "citation"
                strong_context = any(
                    pattern.search(prefix) for pattern in context.citation_prefix_patterns
                ) or (prior_mark is not None and prior_mark.strong_citation_context)
            elif any(pattern.search(prefix) for pattern in context.prose_prefix_patterns) and any(
                pattern.search(text[match.end() : match.end() + context.lookback_chars])
                for pattern in context.citation_suffix_patterns
            ):
                # Running prose before the mark and a clause boundary or
                # continuing sentence after it: "used similar approaches [3]."
                # Mathematical, figure and scale contexts were excluded above.
                classification = "citation"
                strong_context = False
            elif _SINGLE_BRACKET.fullmatch(match.group()):
                # A lone [n] following prose is a useful possible link to
                # an explicit [n] entry. Shape alone cannot justify an
                # out-of-range or unused-reference finding.
                classification = "citation"
                strong_context = False
            else:
                classification = "ambiguous"
                strong_context = False
            numbers = _numbers_in_mark(
                match.group(),
                max_range_span=max_range_span,
                context=context,
            )
            if numbers is None and classification != "noncitation":
                classification = "ambiguous"
                strong_context = False
            if (
                numbers is not None
                and prior_mark is not None
                and prior_mark.numbers
                and prior_mark.classification == "citation"
                and _BETWEEN_RANGE_MARKS.fullmatch(
                    text[prior_mark.start + len(prior_mark.raw_text) : match.start()]
                )
            ):
                # IEEE "[3]-[6]" cites 3, 4, 5 and 6. Without the bridge, 4 and
                # 5 would look unused.
                low, high = prior_mark.numbers[-1], numbers[0]
                if low < high and high - low <= max_range_span:
                    numbers = tuple(dict.fromkeys([*range(low + 1, high), *numbers]))
                    if classification == "citation":
                        strong_context = strong_context or prior_mark.strong_citation_context
            anchor = (
                f"p. {block.page}"
                if block.page is not None
                else f"¶{block.paragraph}"
                if block.paragraph is not None
                else "document"
            )
            excerpt, excerpt_cropped = _sentence_at(
                text,
                match.start(),
                match.end(),
                boundaries,
                boundary_ends,
                max_chars=context.evidence_excerpt_max_chars,
            )
            prior_mark = NumericMark(
                raw_text=match.group(),
                numbers=numbers,
                classification=classification,
                strong_citation_context=strong_context,
                anchor=anchor,
                page=block.page,
                paragraph=block.paragraph,
                block_index=(
                    source_block_index_by_id.get(id(block), block_index)
                    if source_block_index_by_id is not None
                    else block_index
                ),
                start=match.start(),
                claim_sentence=excerpt,
                excerpt_cropped=excerpt_cropped,
            )
            # Keep prior_mark local for adjacency and range bridging above;
            # only the emitted evidence uses original raw-store coordinates.
            marks.append(
                replace(
                    prior_mark,
                    block_index=block.source_block_index,
                    start=block.source_start + prior_mark.start,
                )
                if isinstance(block, SourceFragment)
                else prior_mark
            )
            if len(marks) >= context.max_body_marks:
                scan_limited = True
                break
        if scan_limited:
            break
    return marks, scan_limited


def _possibly_cited_numbers(
    blocks: list[TextBlock], *, max_range_span: int, context: NumericContext
) -> tuple[set[int], bool]:
    """Numbers a mark may cite outside the main scan's reading, bounded like
    it: glued and locator marks, and the bridges any mark starts
    ("compared[7]-[10]" also covers 8 and 9). Also whether a glued or locator
    mark in a citation context could not be read, which leaves every entry
    possibly cited, as an unreadable body mark in a citation context does
    (BUG-654)."""
    numbers: set[int] = set()
    unreadable = False
    remaining = context.max_body_scan_chars
    for block in blocks:
        text = (block.text or "")[: max(0, remaining)]
        remaining -= len(text)
        found, unreadable_kinds = _read_marks(text, max_range_span=max_range_span, context=context)
        numbers.update(found)
        unreadable = unreadable or bool(unreadable_kinds & {"glued", "locator"})
        if remaining <= 0:
            break
    return numbers, unreadable


def _reference_inventory(
    references: list[ReferenceLike],
    *,
    max_number_digits: int,
) -> tuple[dict[int, ReferenceLike], int, bool]:
    by_number: dict[int, ReferenceLike] = {}
    n_numbered = 0
    valid = bool(references)
    for ref in references:
        raw = ref.raw_text or ""
        if _ANY_NUMBERED_REFERENCE.match(raw):
            n_numbered += 1
        marker = _REFERENCE_LABEL.match(raw)
        if marker is None:
            valid = False
            continue
        number_text = marker.group("bracket") or marker.group("plain")
        if len(number_text) > max_number_digits:
            valid = False
            continue
        number = int(number_text)
        tail = raw[marker.end() :]
        # A trailing page-range endpoint can look like "25. PMID" after PDF
        # text normalization. A second consecutive printed number is stronger
        # evidence of an accidentally merged reference than an arbitrary
        # number-period token in the bibliography's metadata.
        merged_plain = any(
            int(candidate.group(1)) == number + 1
            for candidate in _SECOND_PLAIN_LABEL.finditer(tail)
        )
        if number in by_number or _SECOND_BRACKET_LABEL.search(tail) or merged_plain:
            valid = False
        by_number[number] = ref
    if sorted(by_number) != list(range(1, len(references) + 1)):
        valid = False
    return by_number, n_numbered, valid


def analyze_numbered_citations(
    blocks: list[TextBlock],
    references: list[ReferenceLike],
    *,
    max_range_span: int,
    context_file: Path | None = None,
    source_block_index_by_id: dict[int, int] | None = None,
) -> NumberedCitationAssessment:
    """Map citation-like marks only behind a complete printed-number inventory.

    An unresolved mark with parseable numbers shields just those references
    from an absence claim; an unparseable one shields the whole inventory.
    """
    if max_range_span < 1:
        raise ValueError("max_range_span must be positive")
    context = load_numeric_context(context_file)
    by_number, n_numbered, complete = _reference_inventory(
        references, max_number_digits=context.max_number_digits
    )
    marks, scan_limited = _body_marks(
        blocks,
        max_range_span=max_range_span,
        context=context,
        source_block_index_by_id=source_block_index_by_id,
    )
    credible = [mark for mark in marks if mark.classification != "noncitation"]
    # Uncertainty never becomes an absence claim: a number seen in a
    # non-citation context ("Gini Index [42]"), a glued mark or a locator
    # mark may still be cited, so it is never reported as unused.
    outside_scan, outside_unreadable = _possibly_cited_numbers(
        blocks, max_range_span=max_range_span, context=context
    )
    possibly_cited = {
        number
        for mark in marks
        if mark.classification == "noncitation" and mark.numbers
        for number in mark.numbers
    } | outside_scan
    if not complete or scan_limited:
        return NumberedCitationAssessment(
            n_numbered_reference_entries=n_numbered,
            n_numeric_body_candidates=len(marks),
            inventory_complete=complete,
            marks=marks,
            linked=[],
            orphan_marks=[],
            uncited=[],
            unresolved_marks=credible,
            n_linkage_unassessed=len(references),
            scan_limited=scan_limited,
            absence_unassessed_references=list(references),
        )

    linked: list[tuple[NumericMark, list[ReferenceLike]]] = []
    orphan_marks: list[NumericMark] = []
    unresolved: list[NumericMark] = []
    linked_numbers: set[int] = set()
    shielded_numbers: set[int] = set()
    shield_all = False
    strong_links = 0
    strong_linked_numbers: set[int] = set()
    for mark in credible:
        if mark.classification == "ambiguous":
            unresolved.append(mark)
            if mark.numbers is None:
                shield_all = True
            else:
                shielded_numbers.update(mark.numbers)
            continue
        if mark.numbers is None:
            unresolved.append(mark)
            shield_all = True
            continue
        matched = [by_number[number] for number in mark.numbers if number in by_number]
        if matched:
            linked.append((mark, matched))
            linked_numbers.update(number for number in mark.numbers if number in by_number)
            if mark.strong_citation_context:
                strong_links += 1
                strong_linked_numbers.update(n for n in mark.numbers if n in by_number)
        if not mark.strong_citation_context:
            unresolved.append(mark)
            shielded_numbers.update(mark.numbers)
        if mark.strong_citation_context and any(number not in by_number for number in mark.numbers):
            orphan_marks.append(mark)

    # A bibliography with no confirmed bracketed body citation may instead
    # use a style this parser does not recognize. Do not assert absence.
    if strong_links == 0:
        shield_all = True
    # A glued or locator mark that cannot be read could cite any entry.
    if outside_unreadable:
        shield_all = True
    # A list mostly linked by strong marks shows the paper cites in brackets;
    # a sparse one may cite in a form this parser does not read.
    absence_limited = shield_all or (
        bool(by_number)
        and len(strong_linked_numbers) / len(by_number) < context.min_linked_fraction
    )
    uncited = (
        []
        if shield_all
        else [
            ref
            for number, ref in by_number.items()
            if number not in linked_numbers and number not in shielded_numbers
        ]
    )
    confidently_uncited = (
        []
        if absence_limited
        else [
            ref
            for number, ref in by_number.items()
            if ref in uncited and number not in possibly_cited
        ]
    )
    absence_unassessed = [
        ref
        for number, ref in by_number.items()
        if number not in strong_linked_numbers and ref not in confidently_uncited
    ]
    n_unassessed = (
        len(references)
        if shield_all
        else len(set(by_number) & (shielded_numbers - strong_linked_numbers))
    )
    return NumberedCitationAssessment(
        n_numbered_reference_entries=n_numbered,
        n_numeric_body_candidates=len(marks),
        inventory_complete=True,
        marks=marks,
        linked=linked,
        orphan_marks=orphan_marks,
        uncited=uncited,
        unresolved_marks=unresolved,
        n_linkage_unassessed=n_unassessed,
        confidently_uncited=confidently_uncited,
        absence_unassessed_references=absence_unassessed,
        absence_assessment_complete=not absence_unassessed,
    )
