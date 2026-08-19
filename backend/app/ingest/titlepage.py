"""V-063: deterministic title-page extraction (group name, members,
program) -- front-matter parsing off the already-extracted blocks, no LLM
call (Q1 DECIDED: quota is the currency, D-001; a data-entry convenience
doesn't justify a recurring Gemini call).

Proposes only -- never applies. The instructor confirms every field
(charter rule 1, this ticket's own AC2); this module has no DB access and
no side effects.
"""

from dataclasses import dataclass, field

from app.ingest.patterns import HeadingPatterns
from app.ingest.schemas import ExtractionResult, TextBlock


@dataclass(frozen=True)
class AnchoredValue:
    """A proposed field plus WHERE it came from (AC3: the instructor must
    be able to check it, the same evidence standard the rest of the
    product is held to)."""

    value: str
    anchor: str


@dataclass(frozen=True)
class TitlePageProposal:
    title: AnchoredValue | None
    short_name: AnchoredValue | None
    members: list[AnchoredValue] = field(default_factory=list)
    program: AnchoredValue | None = None
    adviser: AnchoredValue | None = None
    # True only when NOTHING usable was found at all (e.g. an image-only
    # scan with zero extractable text on the first page) -- AC5's
    # explicit "couldn't read the title page" state. A partial proposal
    # (title found, no members) is NOT a failure; it's shown as-is with
    # the gaps visible, never fabricated.
    extraction_failed: bool = False


def _anchor_for(block: TextBlock) -> str:
    if block.page is not None:
        return f"p. {block.page}"
    if block.paragraph is not None:
        return f"¶{block.paragraph}"
    return "document"


def _front_matter_blocks(result: ExtractionResult) -> list[TextBlock]:
    """The title page is always the very first page (PDF) or the first
    handful of paragraphs before any real content (DOCX, which has no
    page concept before rendering) -- never scanned past that, so a
    manuscript's actual Chapter 1 text can never be mistaken for
    front-matter fields."""
    if result.anchor_kind == "page":
        return [b for b in result.blocks if b.page == 1 and not b.is_furniture and b.text.strip()]
    return [b for b in result.blocks[:40] if not b.is_furniture and b.text.strip()]


def _split_marker_prefix(text: str, markers: frozenset[str]) -> tuple[bool, str]:
    """True plus whatever text follows the marker, when `text` starts
    with one of `markers` -- covers both title-page conventions this
    codebase has actually seen: the marker alone on its own line
    ("Adviser:" / "By:", value on the next line) AND the marker inline
    with its value ("Adviser: Prof. Ana Lopez", one line). ux-critic
    (V-063 review) reproduced live why this must handle both: the old
    whole-block-equality check never matched the inline form at all, so
    the dedicated Adviser field silently stayed empty AND the member-
    collection loop's own stop condition never fired -- it kept sweeping
    every later front-matter block (the adviser line itself, the
    trailing date) into the Members list as fabricated, falsely-anchored
    "members"."""
    stripped = text.strip()
    lowered = stripped.casefold()
    for marker in markers:
        bare = marker.rstrip(":")
        if lowered == bare:
            return True, ""
        prefix = f"{bare}:"
        if lowered.startswith(prefix):
            return True, stripped[len(prefix) :].strip()
    return False, ""


def extract_title_page(result: ExtractionResult, patterns: HeadingPatterns) -> TitlePageProposal:
    tp = patterns.title_page
    blocks = _front_matter_blocks(result)
    if not blocks:
        return TitlePageProposal(title=None, short_name=None, extraction_failed=True)

    title_block = blocks[0]
    title_text = title_block.text.strip()
    short_name_text = title_text
    # Earliest-occurring separator wins, matching the DECIDED rule exactly
    # ("text before the FIRST `:` or ` - `") -- not whichever separator
    # happens to be listed first in the data file.
    earliest_index: int | None = None
    for sep in tp.title_separators:
        idx = title_text.find(sep)
        if idx > 0 and (earliest_index is None or idx < earliest_index):
            earliest_index = idx
    if earliest_index is not None:
        short_name_text = title_text[:earliest_index].strip()

    program: AnchoredValue | None = None
    for block in blocks:
        lowered = block.text.strip().casefold()
        if not lowered.startswith(tp.degree_prefix):
            continue
        suffix = lowered[len(tp.degree_prefix) :].strip()
        for degree_text, program_name in tp.degree_program_map.items():
            if degree_text in suffix:
                program = AnchoredValue(program_name, _anchor_for(block))
                break
        break  # the degree line appears once; stop looking once found

    members: list[AnchoredValue] = []
    in_members = False
    for block in blocks:
        matched_by, by_remainder = _split_marker_prefix(block.text, tp.by_markers)
        if matched_by:
            in_members = True
            if by_remainder:
                members.append(AnchoredValue(by_remainder, _anchor_for(block)))
            continue
        matched_adviser, _ = _split_marker_prefix(block.text, tp.adviser_markers)
        if matched_adviser:
            break
        if in_members:
            stripped = block.text.strip()
            if stripped:
                members.append(AnchoredValue(stripped, _anchor_for(block)))

    adviser: AnchoredValue | None = None
    for i, block in enumerate(blocks):
        matched, remainder = _split_marker_prefix(block.text, tp.adviser_markers)
        if not matched:
            continue
        if remainder:
            adviser = AnchoredValue(remainder, _anchor_for(block))
        else:
            for nxt in blocks[i + 1 :]:
                stripped = nxt.text.strip()
                if stripped:
                    adviser = AnchoredValue(stripped, _anchor_for(nxt))
                break
        break

    return TitlePageProposal(
        title=AnchoredValue(title_text, _anchor_for(title_block)),
        short_name=AnchoredValue(short_name_text, _anchor_for(title_block)),
        members=members,
        program=program,
        adviser=adviser,
        extraction_failed=False,
    )
