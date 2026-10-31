"""What the agreement judge sees around each objective/finding pair (F4, V-094).

A finding read on its own can look like a contradiction that the manuscript
has already explained: "the system was not yet able to sync offline in the
pilot" next to an objective about the whole system, when the scope section
says only the pilot was evaluated. Document-level consistency checkers judge
a claim against about a paragraph of source rather than one sentence, so
each pair carries:

- the sentences just before and after the finding (a configured window);
- the heading each statement sits under;
- the manuscript's own scope, delimitation, limitation and revision
  sentences, ranked by similarity to the pair so only the relevant few are
  sent.

Which sentences count as scope statements is data (`cues.json`), not code.
Ranking uses the same local embedding as pairing, so it spends no quota.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from app.checks.agreement.cues import AgreementCues
from app.checks.agreement.extract import (
    Statement,
    _anchor_for,
    _block_at,
    _heading_section,
    _is_heading_like,
    _joined_with_offsets,
    _normalize,
    _sentence_spans,
    _strip_numbering,
)
from app.config import Settings
from app.ingest.schemas import ExtractionResult
from app.ml.embeddings import cosine_similarity, embed_texts

# How much of a statement is compared when locating it among the document's
# sentences: list items can be longer than one sentence.
_LOCATE_PREFIX = 80
# Words this short ("the", "of", "to") carry no topic.
_MIN_WORD = 3
_WORD = re.compile(r"[a-z0-9]+")


def _grams(text: str) -> set[str]:
    """Content words and adjacent word pairs, for shared-phrase ranking."""
    words = [w for w in _WORD.findall(text.lower()) if len(w) >= _MIN_WORD]
    return set(words) | {f"{a} {b}" for a, b in zip(words, words[1:], strict=False)}


@dataclass(frozen=True)
class Sentence:
    text: str
    anchor: str
    heading: str | None


@dataclass(frozen=True)
class PairContext:
    before: str = ""
    after: str = ""
    objective_heading: str | None = None
    finding_heading: str | None = None
    scope: tuple[Sentence, ...] = ()

    def text(self) -> str:
        """Everything sent besides the two statements, for quote checks."""
        return " ".join([self.before, self.after, *(s.text for s in self.scope)])


def document_sentences(extraction: ExtractionResult, *, heading_max_words: int) -> list[Sentence]:
    """Prose sentences in reading order, each with the heading above it."""
    from app.ingest.analytical_source import analytical_source

    sentences: list[Sentence] = []
    for scope in analytical_source(extraction).scopes:
        heading: str | None = None
        run: list = []

        def flush(run=run, heading_now=None) -> None:
            # PDF blocks are often single lines, so the prose under one heading
            # is joined before it is split, or a sentence would be cut at every
            # line break.
            joined, ranges = _joined_with_offsets(run)
            for start, end in _sentence_spans(joined):
                text = _normalize(joined[start:end])
                block = _block_at(start, ranges)
                if text and block is not None:
                    sentences.append(Sentence(text, _anchor_for(block), heading_now))
            run.clear()

        for block in scope:
            if block.is_furniture or not block.text.strip():
                continue
            if _is_heading_like(block.text.strip(), max_words=heading_max_words):
                flush(heading_now=heading)
                heading = _strip_numbering(_normalize(block.text))
                continue
            run.append(block)
        flush(heading_now=heading)
    return sentences


def _locate(statement: Statement, sentences: list[Sentence]) -> int | None:
    key = _normalize(statement.text).lower()[:_LOCATE_PREFIX]
    for index, sentence in enumerate(sentences):
        own = sentence.text.lower()
        if key in own or own[:_LOCATE_PREFIX] in key:
            return index
    return None


def _clip_left(text: str, limit: int) -> str:
    return text if len(text) <= limit else "…" + text[len(text) - limit :].lstrip()


def _clip_right(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


@dataclass
class DocumentContext:
    """Built once per manuscript; answers `for_pair` for each candidate."""

    sentences: list[Sentence]
    scope_statements: list[Sentence]
    settings: Settings
    _scope_vectors: list | None = field(default=None, repr=False)

    @classmethod
    def build(
        cls, extraction: ExtractionResult, cues: AgreementCues, settings: Settings
    ) -> DocumentContext:
        sentences = document_sentences(
            extraction, heading_max_words=settings.agreement_heading_max_words
        )
        patterns = [re.compile(p) for p in cues.scope_statement_patterns]
        phrases = (*cues.scope_negation_phrases, *cues.future_work_guard_phrases)
        scope, seen = [], set()
        for sentence in sentences:
            lowered = sentence.text.lower()
            heading = (sentence.heading or "").lower()
            under_scope_heading = any(syn in heading for syn in cues.scope_heading_synonyms) and (
                _heading_section(sentence.heading or "", cues) == "other"
            )
            if (
                under_scope_heading
                or any(p.search(lowered) for p in patterns)
                or any(phrase in lowered for phrase in phrases)
            ) and lowered not in seen:
                seen.add(lowered)
                scope.append(sentence)
        return cls(sentences=sentences, scope_statements=scope, settings=settings)

    def _ranked_scope(
        self, intent: Statement, outcome: Statement, exclude: set[str]
    ) -> tuple[Sentence, ...]:
        """A scope statement that explains a finding usually repeats the words
        the finding adds to its objective ("in the pilot", "the offline
        mode"), so those shared words rank first; the finding's embedding
        breaks ties. Ranking by the embedding alone put the explaining
        sentence in the top three for 11 of 33 development cases, against 32
        of 33 this way."""
        limit = self.settings.agreement_scope_statements_per_pair
        candidates = [s for s in self.scope_statements if s.text.lower() not in exclude]
        if not limit or not candidates:
            return ()
        if self._scope_vectors is None:
            self._scope_vectors = embed_texts(
                [s.text for s in self.scope_statements], self.settings
            )
        vectors = {
            s.text: v for s, v in zip(self.scope_statements, self._scope_vectors, strict=True)
        }
        finding_vector = embed_texts([outcome.text], self.settings)[0]
        added = _grams(outcome.text) - _grams(intent.text)

        def score(sentence: Sentence) -> float:
            own = _grams(sentence.text)
            lexical = len(added & own) / math.sqrt(1 + len(own))
            return lexical + cosine_similarity(finding_vector, vectors[sentence.text])

        ranked = sorted(candidates, key=score, reverse=True)
        cap = self.settings.agreement_scope_statement_max_chars
        return tuple(
            Sentence(_clip_right(s.text, cap), s.anchor, s.heading) for s in ranked[:limit]
        )

    def for_pair(self, intent: Statement, outcome: Statement) -> PairContext:
        settings = self.settings
        found = _locate(outcome, self.sentences)
        before = after = ""
        finding_heading = None
        exclude = {_normalize(intent.text).lower(), _normalize(outcome.text).lower()}
        if found is not None:
            finding_heading = self.sentences[found].heading
            lo = max(0, found - settings.agreement_context_sentences_before)
            hi = found + 1 + settings.agreement_context_sentences_after
            budget = settings.agreement_context_max_chars // 2
            # The window stays under the finding's own heading: text from
            # another section is not "around" the finding.
            before_sentences = [s for s in self.sentences[lo:found] if s.heading == finding_heading]
            after_sentences = [
                s for s in self.sentences[found + 1 : hi] if s.heading == finding_heading
            ]
            before = _clip_left(" ".join(s.text for s in before_sentences), budget)
            after = _clip_right(" ".join(s.text for s in after_sentences), budget)
            exclude |= {s.text.lower() for s in (*before_sentences, *after_sentences)}
        located_intent = _locate(intent, self.sentences)
        return PairContext(
            before=before,
            after=after,
            objective_heading=self.sentences[located_intent].heading
            if located_intent is not None
            else None,
            finding_heading=finding_heading,
            scope=self._ranked_scope(intent, outcome, exclude),
        )
