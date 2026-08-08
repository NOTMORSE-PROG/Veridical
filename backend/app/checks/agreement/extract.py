"""Intent/outcome statement extraction (F4.1/F4.2, V-034) — the first half
of the Internal Agreement Check (F4). Position-independent (ticket:
statements can appear anywhere in the manuscript, not just in a canonical
"Objectives" chapter), rule-based FIRST per D-011's cascade and the ticket's
own AC #2 ("rule-based extraction runs first; LLM only augments").

**No LLM augmentation pass exists in this module.** The ticket's own D-011
impact note gates it on measurement: "the bounded Gemini augmentation pass
runs only if rule recall <80% on the labeled fixture (measure first, like
V-006 did)." `tests/test_checks_agreement_extract.py` measures rule-only
recall against the owner's real 47-page proposal (4/4 of its stated
objectives, zero false positives) plus a hand-labeled synthetic "second
manuscript" fixture (5/5 intents, 4/4 outcomes, with two planted
distractors — a future-work and a scope-negation sentence — correctly
excluded). Both measured at 100%, well above the 80% floor, so (V-006's own
precedent: 17/17 regex -> skip the LLM fallback) augmentation was never
built. If a future real manuscript drops rule recall below the 80% floor,
add the Gemini pass here; don't build it speculatively against a floor
nothing has failed yet.

Two independent rule types, matching the ticket's own wording exactly
("rule-based cues (objectives lists, modal verbs)"):
1. **Objective/outcome LIST detection**: a numbered/bulleted list under a
   heading matching `cues.objective_heading_synonyms` /
   `outcome_heading_synonyms` (data file, ENGINEERING §8) — the strong
   signal real capstones use ("1.3.2 Specific Objectives" -> "1. To build
   a Rubric Parsing module that..."). PDF/DOCX extraction gives one
   TextBlock per LINE, not per paragraph, so a single list item is
   reassembled by merging continuation blocks until the next list marker
   or heading.
2. **Modal-phrase cues, anywhere in the document** ("the system will...",
   "results showed..."): sentence-level, position-independent, the part
   of this ticket general contradiction-detection scope explicitly does
   NOT require chapter placement.

Guards (ticket edge cases): a sentence containing a future-work phrase
("future researchers", "could later...") or a scope-negation phrase ("is
outside the scope of this study") is filtered out entirely, never emitted
as an intent — even if it also matches a modal cue.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from app.checks.agreement.cues import AgreementCues, load_cues
from app.config import Settings, get_settings
from app.ingest.schemas import ExtractionResult, TextBlock

StatementKind = Literal["intent", "outcome"]

# Multi-level section numbering ("1.3", "1.3.1", "2.4.5") — a heading, never
# a single-level list marker (format invariant, same convention as
# `app.checks.citations.extract`'s compiled patterns).
_SECTION_NUMBERING = re.compile(r"^\d+(\.\d+)+\s")
# Single-level list markers: "1. ", "(1) ", bullet glyphs, "- ".
_LIST_MARKER = re.compile(r"^(?:\d+\.\s|\(\d+\)\s|[•●▪‣]\s|-\s)")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'•●▪‣])")
_WHITESPACE = re.compile(r"\s+")
# Lowercase connector words allowed inside an otherwise Title Case heading
# ("Summary of Findings", "Statement of the Problem") — `str.istitle()`
# rejects these outright since it requires every word capitalized, which
# real headings don't follow.
_TITLE_CONNECTORS = frozenset({"of", "the", "and", "in", "for", "to", "on", "a", "an"})


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _word_count(text: str) -> int:
    return len(text.split())


def _strip_numbering(text: str) -> str:
    return _SECTION_NUMBERING.sub("", _LIST_MARKER.sub("", text)).strip()


def _looks_title_case(text: str) -> bool:
    words = re.findall(r"[A-Za-z']+", text)
    if not words:
        return False
    return all(w.lower() in _TITLE_CONNECTORS or w[:1].isupper() for w in words)


def _is_heading_like(text: str, *, max_words: int) -> bool:
    stripped = _strip_numbering(text)
    if not stripped or _word_count(stripped) > max_words:
        return False
    if stripped.endswith((".", "!", "?", ",")):
        return False
    return bool(_SECTION_NUMBERING.match(text)) or stripped.isupper() or _looks_title_case(stripped)


def _heading_section(text: str, cues: AgreementCues) -> Literal["objectives", "outcomes", "other"]:
    lowered = _strip_numbering(text).lower()
    if any(syn in lowered for syn in cues.objective_heading_synonyms):
        return "objectives"
    if any(syn in lowered for syn in cues.outcome_heading_synonyms):
        return "outcomes"
    return "other"


def _anchor_for(block: TextBlock) -> str:
    if block.page is not None:
        return f"p. {block.page}"
    if block.paragraph is not None:
        return f"¶{block.paragraph}"
    return "document"


@dataclass(frozen=True)
class Statement:
    """One extracted intent or outcome statement — V-035's pairing input."""

    kind: StatementKind
    text: str
    anchor: str
    page: int | None
    paragraph: int | None
    cue: str  # which rule matched: "objective_list" / "outcome_list" / a phrase cue
    other_anchors: tuple[str, ...] = ()  # de-duplicated restatements, kept for audit


@dataclass(frozen=True)
class ExtractionOutcome:
    intents: list[Statement]
    outcomes: list[Statement]


# --- rule 1: objective/outcome list detection -------------------------------


def _extract_list_statements(
    blocks: list[TextBlock], cues: AgreementCues, *, heading_max_words: int
) -> list[Statement]:
    out: list[Statement] = []
    section: Literal["objectives", "outcomes", "other"] = "other"
    current_kind: StatementKind | None = None
    current_parts: list[str] = []
    current_block: TextBlock | None = None

    def _flush() -> None:
        if current_kind is not None and current_block is not None and current_parts:
            text = _normalize(" ".join(current_parts))
            if text:
                out.append(
                    Statement(
                        kind=current_kind,
                        text=text,
                        anchor=_anchor_for(current_block),
                        page=current_block.page,
                        paragraph=current_block.paragraph,
                        cue=f"{current_kind}_list",
                    )
                )

    for block in blocks:
        if block.is_furniture or not block.text.strip():
            continue
        text = block.text.strip()

        if _is_heading_like(text, max_words=heading_max_words):
            _flush()
            current_kind, current_parts, current_block = None, [], None
            new_section = _heading_section(text, cues)
            section = new_section
            continue

        if section == "other":
            continue

        if _LIST_MARKER.match(text):
            _flush()
            current_kind = "intent" if section == "objectives" else "outcome"
            current_parts = [_LIST_MARKER.sub("", text, count=1)]
            current_block = block
        elif (
            current_kind is not None
            and current_parts
            and not current_parts[-1].rstrip().endswith((".", "!", "?"))
        ):
            # A continuation line ONLY if the item hasn't reached its own
            # terminal punctuation yet (PDF line-wrap: "1. To build a Rubric
            # Parsing module that reads any required format the Capstone
            # Instructor" + "uploads and breaks it down..."). Once an item's
            # sentence is complete, ordinary prose that follows the list
            # (real docs: a findings LIST followed by a discussion
            # paragraph, still under the same heading) must NOT keep
            # merging into it — a real bug found live via this module's own
            # test suite (a "Results showed..." sentence three blocks later
            # was silently swallowed into the previous list item).
            current_parts.append(text)

    _flush()
    return out


# --- rule 2: modal-phrase cues, position-independent -------------------------


def _joined_with_offsets(
    blocks: list[TextBlock],
) -> tuple[str, list[tuple[int, int, TextBlock]]]:
    parts: list[str] = []
    ranges: list[tuple[int, int, TextBlock]] = []
    pos = 0
    for block in blocks:
        if block.is_furniture or not block.text.strip():
            continue
        start = pos
        parts.append(block.text)
        pos += len(block.text)
        ranges.append((start, pos, block))
        parts.append(" ")
        pos += 1
    return "".join(parts), ranges


def _block_at(offset: int, ranges: list[tuple[int, int, TextBlock]]) -> TextBlock | None:
    for start, end, block in ranges:
        if start <= offset <= end:
            return block
    return ranges[-1][2] if ranges else None


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for boundary in _SENTENCE_BOUNDARY.finditer(text):
        spans.append((start, boundary.start()))
        start = boundary.end()
    spans.append((start, len(text)))
    return spans


def _leading_words(text: str, n: int) -> str:
    return " ".join(text.split()[:n])


def _extract_modal_statements(
    blocks: list[TextBlock], cues: AgreementCues, *, cue_max_lead_words: int, heading_max_words: int
) -> list[Statement]:
    # Heading lines carry no terminal punctuation, so joining them straight
    # into the surrounding text would silently merge a heading into the
    # NEXT sentence (no boundary to split on) and push a real cue phrase
    # out of the lead-word window — a real bug found live via this
    # module's own test suite. Headings aren't prose to scan for cues
    # anyway, so they're excluded here (the list-detection rule above
    # still sees every block, headings included).
    prose_blocks = [
        b for b in blocks if not _is_heading_like(b.text.strip(), max_words=heading_max_words)
    ]
    joined, ranges = _joined_with_offsets(prose_blocks)
    if not joined:
        return []
    out: list[Statement] = []
    for start, end in _sentence_spans(joined):
        sentence = _normalize(joined[start:end])
        if not sentence:
            continue
        lowered = sentence.lower()
        if any(g in lowered for g in cues.future_work_guard_phrases):
            continue
        if any(g in lowered for g in cues.scope_negation_phrases):
            continue

        # Real intent/outcome openers name their subject early ("This study
        # aims to...", "Results showed..."); a cue phrase buried deep in a
        # long sentence is usually a nested clause TALKING ABOUT intent as a
        # concept, not stating one (a real false positive found live against
        # the owner's own proposal — its glossary defines "Internal
        # Agreement" using the words "it intends to do" seven words in).
        lead = _leading_words(lowered, cue_max_lead_words)
        matched_intent = next((c for c in cues.intent_phrase_cues if c in lead), None)
        matched_outcome = next((c for c in cues.outcome_phrase_cues if c in lead), None)
        if matched_intent is None and matched_outcome is None:
            continue
        block = _block_at(start, ranges)
        if block is None:
            continue
        kind: StatementKind = "intent" if matched_intent else "outcome"
        cue = matched_intent or matched_outcome or ""
        out.append(
            Statement(
                kind=kind,
                text=sentence,
                anchor=_anchor_for(block),
                page=block.page,
                paragraph=block.paragraph,
                cue=cue,
            )
        )
    return out


# --- dedup --------------------------------------------------------------------


def _dedupe(statements: list[Statement], *, threshold: float) -> list[Statement]:
    """Merge near-duplicate restatements (ticket edge case: Ch1 vs Ch3
    wording drift on the same objective) by normalized-text similarity —
    stdlib `difflib`, no embedding needed for exact/near-exact restatement
    (V-035 owns the harder semantic-similarity pairing, D-011)."""
    kept: list[Statement] = []
    for stmt in statements:
        norm = stmt.text.lower()
        merged = False
        for i, existing in enumerate(kept):
            if SequenceMatcher(None, norm, existing.text.lower()).ratio() >= threshold:
                kept[i] = Statement(
                    kind=existing.kind,
                    text=existing.text,
                    anchor=existing.anchor,
                    page=existing.page,
                    paragraph=existing.paragraph,
                    cue=existing.cue,
                    other_anchors=(*existing.other_anchors, stmt.anchor),
                )
                merged = True
                break
        if not merged:
            kept.append(stmt)
    return kept


# --- entry point ---------------------------------------------------------------


def extract_statements(
    extraction: ExtractionResult,
    *,
    cues: AgreementCues | None = None,
    settings: Settings | None = None,
) -> ExtractionOutcome:
    settings = settings or get_settings()
    cues = cues or load_cues(settings.agreement_cues_file)
    blocks = extraction.blocks

    statements = [
        *_extract_list_statements(
            blocks, cues, heading_max_words=settings.agreement_heading_max_words
        ),
        *_extract_modal_statements(
            blocks,
            cues,
            cue_max_lead_words=settings.agreement_cue_max_lead_words,
            heading_max_words=settings.agreement_heading_max_words,
        ),
    ]
    threshold = settings.agreement_dedup_similarity_threshold
    # Deduped per-kind (not across the combined list) — an intent and an
    # outcome should never merge into each other even if their wording
    # happens to overlap.
    intents = _dedupe([s for s in statements if s.kind == "intent"], threshold=threshold)
    outcomes = _dedupe([s for s in statements if s.kind == "outcome"], threshold=threshold)

    return ExtractionOutcome(intents=intents, outcomes=outcomes)
