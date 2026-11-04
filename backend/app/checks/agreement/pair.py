"""Intent<->outcome pairing + severity flagging (F4.3/F4.4, V-035, V-094) — the
second half of the Internal Agreement Check (F4), per proposal Fig 3.9.

**Cascade (D-011, re-measured 2026-08-08 addendum)**: candidate generation
is Tier 1, local (potion-base-8M cosine similarity, `app.ml.embeddings`) —
an outcome below `agreement_pairing_similarity_floor` never reaches the
judgment tier at all, which is what keeps this quota-bounded. Judgment is
Tier 2, Gemini — local NLI did not fit Render's free-tier memory (DECISIONS.md
D-011 addendum).

**Judging in context (V-094).** Each pair goes to the judge with the text
around the finding and the manuscript's own scope and limitation statements
(`context.py`), so a gap the manuscript explains is `consistent_in_context`,
not a contradiction. A contradiction must be shown, not asserted: it names a
reason and quotes both sides, the quotes are checked against the text that
was sent, and it is asked about again in a separate call before it becomes a
high finding. Any check that fails turns it into "could not determine",
never into a contradiction.

**Wording (charter rule 3)**: every flag describes a possible
inconsistency, never an accusation — "appear to contradict", never "lied
about". Silence means agreement, same convention as F5/F6. Pairs the judge
could not decide are coverage, not findings: they go in the result detail.
"""

import asyncio
import functools
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.checks.agreement.context import DocumentContext, PairContext
from app.checks.agreement.cues import AgreementCues, load_cues
from app.checks.agreement.evidence_policy import assertion_policy
from app.checks.agreement.extract import Statement, _sentence_spans
from app.checks.agreement.sections import gate_objectives, gate_results
from app.checks.agreement.source_explanations import reported_resource_explanation
from app.checks.injection import InjectionSignal, detect_injection_signal
from app.checks.section_roles import SectionMap, load_section_roles
from app.config import Settings
from app.errors import ApiDownError, QuotaExhaustedError, VeridicalError
from app.ingest.schemas import ExtractionResult
from app.llm.base import LLMClient
from app.llm.resolution import AIUnavailableError
from app.ml.embeddings import cosine_similarity, embed_texts
from app.models.enums import FlagSeverity

PROMPT_TYPE = "agreement_pairing"
PROMPT_VERSION = "v5"
_PROMPT_FILE = Path(__file__).parent / "prompts" / f"{PROMPT_TYPE}_{PROMPT_VERSION}.txt"

UNMATCHED_INTENT_WORDING = (
    'Objective "{intent}" does not appear to be addressed in the results, '
    "a possible gap or unstated change of scope. Please check manually."
)
CONTRADICTORY_WORDING = (
    "These reported claims may describe incompatible facts about the same subject and release "
    "or time. Compare both quoted claims and check whether their surrounding text reconciles them."
)
PARTIAL_WORDING = (
    "The selected result describes some progress toward this objective. "
    "Review which parts were completed and which remained unfinished."
)
UNMET_WORDING = (
    "The selected result reports that this objective was not achieved. "
    "Review the stated outcome and any explanation for the unmet objective."
)
# BUG-160: a "consistent" verdict is silence, and silence is exactly what must
# not happen when the text behind that verdict is suspect — a manipulated
# "consistent" would otherwise vanish with no trace. Both texts are the
# student's own manuscript, so this is a fact about the document, never an
# accusation.
AGREEMENT_INJECTION_SUSPECTED_WORDING = (
    "This comparison includes text that appears to address an automated grader. "
    "Read both statements and their surrounding text before relying on the recorded judgment."
)

Verdict = Literal[
    "consistent",
    "consistent_in_context",
    "partial",
    "unmet",
    "contradictory",
    "unrelated",
    "cannot_determine",
]
# Verdicts that are not findings: the judge saw no problem, or a problem the
# manuscript itself explains, or two statements about different things.
SILENT_VERDICTS = ("consistent", "consistent_in_context", "unrelated")
# A majority of three is the first answer plus at most two further readings.
MAJORITY_EXTRA_READINGS = 2
_QUOTE_CHARS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


class PairingError(VeridicalError):
    """The model's structured output didn't validate — never surfaced past
    this module (same discipline as `ClaimSupportError`)."""


class PairVerdict(BaseModel):
    index: int
    verdict: Verdict
    reasoning: str = Field(min_length=1)
    reason_category: str | None = None
    objective_quote: str | None = None
    finding_quote: str | None = None
    reported_attainment: Literal["achieved", "partial", "unmet", "unknown"] = "unknown"
    context_basis: Literal["scope", "limitation", "revision", "none", "unknown"] = "unknown"

    @model_validator(mode="after")
    def coherent_attainment(self) -> "PairVerdict":
        if self.verdict in ("unmet", "partial") and self.reported_attainment != self.verdict:
            raise ValueError("Attainment must agree with the recorded gap verdict")
        if self.verdict == "consistent" and self.reported_attainment in ("unmet", "partial"):
            raise ValueError("An achieved verdict cannot erase reported nonachievement")
        if self.verdict == "consistent_in_context" and (
            self.reported_attainment not in ("achieved", "unmet", "partial")
            or self.context_basis not in ("scope", "limitation", "revision")
        ):
            raise ValueError(
                "An explained difference needs known attainment and an explanation basis"
            )
        return self


class PairVerdictBatchResponse(BaseModel):
    verdicts: list[PairVerdict] = Field(min_length=1)


@dataclass(frozen=True)
class Candidate:
    intent: Statement
    outcome: Statement
    similarity: float
    context: PairContext = field(default_factory=PairContext)
    forced: bool = False


@dataclass(frozen=True)
class AgreementFlagDraft:
    """Flag-ready shape (severity + honest wording already applied) — same
    convention as `CitationFlagDraft`/`ForensicsFlagDraft`."""

    severity: FlagSeverity
    evidence_excerpt: str
    page_anchor: str
    detail: dict


@dataclass(frozen=True)
class PairingResult:
    flags: list[AgreementFlagDraft]
    n_unmatched_outcomes: int
    # BUG-072: three distinct causes, three distinct counters.
    n_skipped_quota: int
    n_skipped_api_down: int
    n_skipped_parse_failure: int
    n_skipped_ai_not_configured: int = 0
    n_skipped_ai_key_rejected: int = 0
    # Coverage, not findings: pairs the judge could not decide, and how
    # every judged pair was answered, for the result detail and the audit.
    undetermined: list[dict] = field(default_factory=list)
    n_compared: int = 0
    verdict_counts: dict[str, int] = field(default_factory=dict)
    downgrades: dict[str, int] = field(default_factory=dict)
    explained_in_context: list[dict] = field(default_factory=list)
    # Which results were kept as the thesis's own, which were set aside and
    # why, or the note that no results section could be identified.
    results_sections: dict | None = None
    # Objectives no result was matched to, so the report can say how many
    # of the objectives found were compared at all.
    n_unmatched_intents: int | None = None
    # The same for objectives: aims stated outside the thesis's own objectives
    # (another paper's aim in the literature review, a process step).
    objective_sections: dict | None = None
    n_locally_unassessed: int = 0
    assessment_ledger: dict = field(default_factory=dict)

    @property
    def n_skipped_total(self) -> int:
        return (
            self.n_skipped_quota
            + self.n_skipped_api_down
            + self.n_skipped_parse_failure
            + self.n_skipped_ai_not_configured
            + self.n_skipped_ai_key_rejected
        )


# --- candidate generation (Tier 1, local) -------------------------------------


def generate_candidates(
    intents: list[Statement],
    outcomes: list[Statement],
    *,
    similarity_floor: float,
    max_candidates_per_intent: int,
    settings: Settings | None = None,
    status_patterns: tuple[str, ...] = (),
    cues: AgreementCues | None = None,
    subjects: tuple[list[str], list[str]] | None = None,
) -> tuple[list[Candidate], list[Statement], set[int]]:
    """Returns (candidates, unmatched_intents, matched_outcome_indexes).

    Each objective is paired with its most similar results, up to the
    configured number. A result that says outright that an objective was
    not met (`status_patterns`) is also paired with the objective it is most
    similar to even when it is not among that objective's top matches, so
    the plainest statement of a gap is never left unjudged. With `cues`, a
    result about a differently named system is never paired with the
    objective at all."""
    if not intents or not outcomes:
        return [], list(intents), set()

    intent_vectors = embed_texts([s.text for s in intents], settings)
    outcome_vectors = embed_texts([s.text for s in outcomes], settings)
    scores = [
        [cosine_similarity(intent_vectors[i], outcome_vectors[j]) for j in range(len(outcomes))]
        for i in range(len(intents))
    ]

    candidates: list[Candidate] = []
    unmatched_intents: list[Statement] = []
    matched_outcome_indexes: set[int] = set()
    # A result about another named system is about a different thing however
    # similar the wording; such pairs are never formed.
    intent_subjects, outcome_subjects = subjects or (
        [s.text for s in intents],
        [o.text for o in outcomes],
    )
    conflicts: set[tuple[int, int]] = {
        (i, j)
        for i in range(len(intents))
        for j in range(len(outcomes))
        if cues is not None
        and different_named_systems(intent_subjects[i], outcome_subjects[j], cues)
    }
    taken: set[tuple[int, int]] = set()

    for i, intent in enumerate(intents):
        ranked = sorted(((scores[i][j], j) for j in range(len(outcomes))), reverse=True)
        above_floor = [
            (sim, j) for sim, j in ranked if sim >= similarity_floor and (i, j) not in conflicts
        ]
        if not above_floor:
            unmatched_intents.append(intent)
            continue
        for sim, j in above_floor[:max_candidates_per_intent]:
            candidates.append(Candidate(intent=intent, outcome=outcomes[j], similarity=sim))
            matched_outcome_indexes.add(j)
            taken.add((i, j))

    compiled = [re.compile(p) for p in status_patterns]
    for j, outcome in enumerate(outcomes):
        if not any(p.search(outcome.text.lower()) for p in compiled):
            continue
        allowed = [(scores[i][j], i) for i in range(len(intents)) if (i, j) not in conflicts]
        if not allowed:
            continue
        sim, i = max(allowed)
        if sim < similarity_floor or (i, j) in taken:
            continue
        candidates.append(
            Candidate(intent=intents[i], outcome=outcome, similarity=sim, forced=True)
        )
        matched_outcome_indexes.add(j)
        taken.add((i, j))
        if intents[i] in unmatched_intents:
            unmatched_intents.remove(intents[i])

    return candidates, unmatched_intents, matched_outcome_indexes


@dataclass(frozen=True)
class PreparedPairs:
    candidates: list[Candidate]
    unmatched_intents: list[Statement]
    matched_outcome_indexes: set[int]
    n_results: int
    results_sections: dict | None = None
    objective_sections: dict | None = None
    unselected_outcomes: list[dict] = field(default_factory=list)


def prepare_pairs(
    intents: list[Statement],
    outcomes: list[Statement],
    *,
    extraction: ExtractionResult | None,
    cues: AgreementCues,
    settings: Settings,
) -> PreparedPairs:
    """The local tier on its own, no model call: keep the results that come
    from the thesis's own results sections, pair them with objectives, and
    attach each pair's context."""
    roles = load_section_roles(settings.section_roles_file)
    document = (
        DocumentContext.build(extraction, cues, settings, roles.chapter_patterns)
        if extraction
        else None
    )
    sections = (
        SectionMap(
            extraction,
            roles,
            sentences=document.sentences,
            heading_max_words=settings.agreement_heading_max_words,
            locate_chars=settings.agreement_context_locate_chars,
        )
        if document is not None
        else None
    )
    long_enough = [
        o for o in outcomes if len(o.text.split()) >= settings.agreement_min_result_words
    ]
    too_short = len(outcomes) - len(long_enough)
    gate = gate_results(long_enough, sections) if sections is not None else None
    results = gate.kept if gate is not None else long_enough
    objective_gate = gate_objectives(intents, sections) if sections is not None else None
    if objective_gate is not None:
        intents = objective_gate.kept
    # Each side is read with its chapter title, so an objective introduced in
    # the "4 SoundSHROOM & BuzzCam" chapter is not paired with a result from
    # the "3 AirSpecs" chapter.
    subjects = None
    if gate is not None and sections.identified:
        subjects = (
            [
                f"{s.text} {sections.locate(s.text, s.anchor, s.page).chapter or ''}"
                for s in intents
            ],
            [f"{o.text} {c or ''}" for o, c in zip(gate.kept, gate.kept_chapters, strict=True)],
        )
    candidates, unmatched, matched = generate_candidates(
        intents,
        results,
        similarity_floor=settings.agreement_pairing_similarity_floor,
        max_candidates_per_intent=settings.agreement_pairing_max_candidates_per_intent,
        settings=settings,
        status_patterns=cues.objective_status_patterns,
        cues=cues,
        subjects=subjects,
    )
    return PreparedPairs(
        candidates=with_context(candidates, document),
        unmatched_intents=unmatched,
        matched_outcome_indexes=matched,
        n_results=len(results),
        results_sections={
            **(gate.detail() if gate is not None else {}),
            "n_results_too_short": too_short,
        },
        objective_sections=objective_gate.detail() if objective_gate is not None else None,
        unselected_outcomes=[
            {"text": item.text, "anchor": item.anchor, "reason": "below_result_word_minimum"}
            for item in outcomes
            if len(item.text.split()) < settings.agreement_min_result_words
        ]
        + [
            {"text": item.text, "anchor": item.anchor, "reason": "no_selected_objective"}
            for index, item in enumerate(results)
            if index not in matched
        ],
    )


def with_context(candidates: list[Candidate], document: DocumentContext | None) -> list[Candidate]:
    """Attach each pair's surrounding text and the manuscript's own scope
    statements. Without a document the pairs go as they are."""
    if document is None or not candidates:
        return candidates
    return [
        Candidate(
            intent=c.intent,
            outcome=c.outcome,
            similarity=c.similarity,
            context=document.for_pair(c.intent, c.outcome),
            forced=c.forced,
        )
        for c in candidates
    ]


# --- judgment (Tier 2, Gemini) -------------------------------------------------


@functools.lru_cache(maxsize=1)
def contradiction_reasons() -> dict[str, str]:
    return assertion_policy()["reasons"]


def _quoted(text: str) -> str:
    return text.replace('"', "'")


SECOND_READING_NOTE = (
    "SECOND READING: these pairs are being checked again, independently. Judge each one "
    "afresh from its own texts; do not assume any earlier answer."
)


def _build_prompt(pairs: list[Candidate], *, reading: int = 0) -> str:
    lines = []
    if reading == 1:
        lines.append(SECOND_READING_NOTE)
    elif reading > 1:
        lines.append(f"{SECOND_READING_NOTE} (reading {reading + 1})")
    for i, pair in enumerate(pairs):
        context = pair.context
        objective_where = pair.intent.anchor + (
            f', under "{context.objective_heading}"' if context.objective_heading else ""
        )
        finding_where = pair.outcome.anchor + (
            f', under "{context.finding_heading}"' if context.finding_heading else ""
        )
        lines.append(f'[{i}] OBJECTIVE ({objective_where}): "{_quoted(pair.intent.text)}"')
        lines.append(f'[{i}] FINDING ({finding_where}): "{_quoted(pair.outcome.text)}"')
        if context.before or context.after:
            lines.append(
                f'[{i}] TEXT AROUND THE FINDING: "{_quoted(context.before)} [FINDING] '
                f'{_quoted(context.after)}"'
            )
        for statement in context.scope:
            lines.append(
                f"[{i}] MANUSCRIPT'S OWN SCOPE/LIMITATION STATEMENT ({statement.anchor}): "
                f'"{_quoted(statement.text)}"'
            )
    reasons = ", ".join(f'"{key}" ({text})' for key, text in contradiction_reasons().items())
    template = _PROMPT_FILE.read_text(encoding="utf-8")
    return template.replace("{reason_categories}", reasons).replace(
        "{pairs_list}", "\n".join(lines)
    )


def parse_verdict_batch(response: object, n_pairs: int) -> dict[int, PairVerdict]:
    """Accept either JSON envelope while validating the complete answer set."""
    try:
        payload = {"verdicts": response} if isinstance(response, list) else response
        parsed = PairVerdictBatchResponse.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 — bad shape degrades honestly, never crashes the run
        raise PairingError(f"agreement_pairing response failed validation: {exc}") from exc
    indexes = [v.index for v in parsed.verdicts]
    if len(indexes) != len(set(indexes)) or set(indexes) != set(range(n_pairs)):
        raise PairingError("agreement_pairing returned duplicate, missing, or invalid pair indexes")
    return {v.index: v for v in parsed.verdicts}


async def _judge_batch(
    llm: LLMClient,
    pairs: list[Candidate],
    *,
    check_run_id: int | None,
    reading: int = 0,
) -> dict[int, PairVerdict]:
    prompt = _build_prompt(pairs, reading=reading)
    response = await llm.complete(
        PROMPT_TYPE, prompt, prompt_version=PROMPT_VERSION, check_run_id=check_run_id
    )
    return parse_verdict_batch(response, len(pairs))


def _normalized_with_positions(text: str) -> tuple[str, list[int]]:
    """The text lower-cased, with curly quotes straightened, words broken
    across PDF lines ("envi- ronmental") joined and runs of whitespace made
    one space, plus where each kept character sits in the original.

    Punctuation, signs and decimal points are kept: "9.5%" is not "95%", "<"
    is not ">", and "notable" is not "not able"."""
    source = text.translate(_QUOTE_CHARS)
    kept: list[str] = []
    where: list[int] = []
    i = 0
    while i < len(source):
        char = source[i]
        if char == "-" and kept and kept[-1].isalpha():
            j = i + 1
            while j < len(source) and source[j].isspace():
                j += 1
            if j > i + 1 and j < len(source) and source[j].islower():
                i = j  # a line-break hyphen: the word continues on the next line
                continue
        if char.isspace():
            if kept and kept[-1] != " ":
                kept.append(" ")
                where.append(i)
            i += 1
            continue
        kept.append(char.lower())
        where.append(i)
        i += 1
    return "".join(kept), where


def _quoted_span(quote: str | None, sources: tuple[str, ...], *, min_chars: int):
    """The manuscript's own words a quote matches, and which source they are
    in, or None. The quote must sit on word boundaries, so it cannot start or
    end inside a word or join two words into one."""
    needle, _ = _normalized_with_positions(quote or "")
    needle = needle.strip(" .,;:'\"")
    if len(re.sub(r"[^0-9a-z]", "", needle)) < min_chars:
        return None
    pattern = re.compile(r"(?<![0-9a-z])" + re.escape(needle) + r"(?![0-9a-z])")
    for index, source in enumerate(sources):
        haystack, where = _normalized_with_positions(source)
        found = pattern.search(haystack)
        if found:
            return index, source[where[found.start()] : where[found.end() - 1] + 1]
    return None


def _named_systems(text: str, cues: AgreementCues) -> set[str]:
    if not cues.named_system_pattern:
        return set()
    return {
        name
        for name in re.findall(cues.named_system_pattern, text)
        if name not in cues.named_system_ignore
    }


def different_named_systems(objective: str, finding: str, cues: AgreementCues) -> bool:
    """True when the objective names a system (a "CollarID"), the finding names
    another ("AirSpecs"), and the finding never mentions the objective's."""
    objective_names = _named_systems(objective, cues)
    finding_names = _named_systems(finding, cues)
    return bool(
        objective_names
        and finding_names
        and not objective_names & finding_names
        and not any(name in finding for name in objective_names)
    )


def different_reported_phases(objective: str, finding: str, cues: AgreementCues) -> bool:
    """Decline attainment inference across explicitly different release labels."""

    def qualifiers(text: str) -> dict[str, set[str]]:
        found: dict[str, set[str]] = {}
        for pattern in cues.phase_qualifier_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                if cues.phase_action_prefix_pattern and re.search(
                    cues.phase_action_prefix_pattern, text[: match.start("kind")], re.IGNORECASE
                ):
                    continue
                label = match.group("label").casefold()
                if label in cues.phase_qualifier_ignored:
                    continue
                label = cues.phase_label_aliases.get(label, label)
                if all(part.isdecimal() for part in label.split(".")):
                    label = ".".join(part.lstrip("0") or "0" for part in label.split("."))
                found.setdefault(match.group("kind").casefold(), set()).add(label)
        return found

    left, right = qualifiers(objective), qualifiers(finding)
    return any(left[kind].isdisjoint(right[kind]) for kind in left.keys() & right.keys())


FINDING_QUOTE_SOURCES = ("finding", "text_before", "text_after")


@dataclass(frozen=True)
class VerifiedQuotes:
    """The manuscript's own words a contradiction rests on, as found in the
    text that was sent, and where the finding side came from."""

    objective: str
    finding: str
    finding_source: str


def _assertion_parent(source: str, quote: str, policy: dict) -> str | None:
    """A source clause must assert the quoted words, rather than ask or posit them."""
    spans = []
    for start, end in _sentence_spans(source):
        if spans and re.search(
            policy["nonterminal_period_pattern"], source[spans[-1][0] : spans[-1][1]], re.IGNORECASE
        ):
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))
    parents = [source[start:end] for start, end in spans if quote in source[start:end]]
    if len(parents) != 1 or any(
        re.search(pattern, parents[0], re.IGNORECASE)
        for pattern in policy["factual_scope_veto_patterns"]
    ):
        return None
    return parents[0]


def check_contradiction(
    candidate: Candidate, verdict: PairVerdict, cues: AgreementCues, *, min_quote_chars: int
) -> tuple[str | None, VerifiedQuotes | None]:
    """Why a contradictory verdict cannot stand as a finding (or None), and
    the quoted words found in the manuscript when it can.

    Checked against the text that was actually sent, so a contradiction is
    never raised on words the manuscript does not contain."""
    if verdict.reason_category not in contradiction_reasons():
        return "reason_not_named", None
    objective = _quoted_span(
        verdict.objective_quote, (candidate.intent.text,), min_chars=min_quote_chars
    )
    if objective is None:
        return "objective_quote_not_found", None
    finding_side = (candidate.outcome.text, candidate.context.before, candidate.context.after)
    finding = _quoted_span(verdict.finding_quote, finding_side, min_chars=min_quote_chars)
    if finding is None:
        return "finding_quote_not_found", None
    if finding[0] != 0:
        return "finding_quote_outside_selected_statement", None
    # Two different named systems (a "CollarID" objective against an
    # "AirSpecs" result) are two different things, whatever the wording. The
    # finding side is read from the sentence the quote came from as well.
    finding_text = " ".join({candidate.outcome.text, finding_side[finding[0]]})
    if different_named_systems(candidate.intent.text, finding_text, cues):
        return "different_named_system", None
    policy = assertion_policy()
    # A cropped quote can hide 'whether' or 'if' in the surrounding goal.
    # Inspect its complete source sentence, retaining abbreviation continuations.
    objective_assertion = _assertion_parent(candidate.intent.text, objective[1], policy)
    if objective_assertion is None:
        return "objective_assertion_scope_unassessed", None
    finding_assertion = _assertion_parent(candidate.outcome.text, finding[1], policy)
    if finding_assertion is None:
        return "finding_assertion_scope_unassessed", None
    if different_reported_phases(objective_assertion, finding_assertion, cues):
        return "different_quoted_phases", None
    if not any(
        re.search(pattern, objective[1], re.IGNORECASE)
        for pattern in policy["factual_assertion_patterns"]
    ):
        return "objective_is_not_descriptive_assertion", None
    return None, VerifiedQuotes(objective[1], finding[1], FINDING_QUOTE_SOURCES[finding[0]])


@dataclass
class Judged:
    candidate: Candidate
    verdict: PairVerdict | None = None
    final: str | None = None
    downgrade: str | None = None
    quotes: VerifiedQuotes | None = None
    # What each re-ask answered, kept so a withheld contradiction can be traced.
    confirmations: list[str] = field(default_factory=list)
    confirmation_readings: list[dict] = field(default_factory=list)
    unassessed_reason: str | None = None
    confirmation_deferred_reason: str | None = None
    locally_unassessed: bool = False
    source_explanation: dict | None = None


async def _judge_all(
    llm: LLMClient,
    judged: list[Judged],
    *,
    check_run_id: int | None,
    chunk_size: int,
    counters: dict[str, int],
    reading: int = 0,
) -> None:
    """Judge every pair in batches; fills `verdict`, counts skips by cause."""
    for start in range(0, len(judged), chunk_size):
        chunk = judged[start : start + chunk_size]
        try:
            verdicts = await _judge_batch(
                llm,
                [j.candidate for j in chunk],
                check_run_id=check_run_id,
                reading=reading,
            )
        except AIUnavailableError as exc:
            counters[exc.cause] += len(judged) - start
            for item in judged[start:]:
                item.unassessed_reason = exc.cause
            return
        except QuotaExhaustedError:
            # BUG-080: terminal for the whole run — every remaining pair would
            # fail the same way, so count them all and stop.
            counters["quota"] += len(judged) - start
            for item in judged[start:]:
                item.unassessed_reason = "quota"
            return
        except ApiDownError:
            counters["api_down"] += len(chunk)
            for item in chunk:
                item.unassessed_reason = "api_down"
            continue
        except PairingError:
            counters["parse_failure"] += len(chunk)
            for item in chunk:
                item.unassessed_reason = "parse_failure"
            continue
        for i, item in enumerate(chunk):
            verdict = verdicts.get(i)
            if verdict is None:
                counters["parse_failure"] += 1
                item.unassessed_reason = "parse_failure"
                continue
            item.verdict = verdict


async def judge_pairs(
    llm: LLMClient,
    candidates: list[Candidate],
    *,
    check_run_id: int | None,
    settings: Settings,
    cues: AgreementCues,
) -> tuple[list[Judged], dict[str, int]]:
    """The judgment tier on its own: every pair judged, contradictions checked
    against the sent text and asked about again before they stand."""
    counters = {
        "quota": 0,
        "api_down": 0,
        "parse_failure": 0,
        "ai_not_configured": 0,
        "ai_key_rejected": 0,
    }
    chunk_size = max(1, settings.agreement_pairing_max_pairs_per_call)
    judged = [Judged(candidate) for candidate in candidates]
    for item in judged:
        if different_reported_phases(item.candidate.intent.text, item.candidate.outcome.text, cues):
            item.verdict = PairVerdict(
                index=0,
                verdict="cannot_determine",
                reasoning=(
                    "The selected statements name different releases, versions or phases. "
                    "The selected result does not establish attainment for the intended phase."
                ),
                reported_attainment="unknown",
                context_basis="unknown",
            )
            item.final = "cannot_determine"
            item.downgrade = "different_reported_phases"
            item.locally_unassessed = True
    await _judge_all(
        llm,
        [item for item in judged if not item.locally_unassessed],
        check_run_id=check_run_id,
        chunk_size=chunk_size,
        counters=counters,
    )
    for item in judged:
        if item.verdict is None:
            continue
        item.final = item.verdict.verdict
        item.source_explanation = reported_resource_explanation(
            item.candidate.outcome,
            item.candidate.context,
            cues,
            verdict=item.verdict.verdict,
            attainment=item.verdict.reported_attainment,
            intent_text=item.candidate.intent.text,
        )
        if item.source_explanation is not None:
            item.final = "consistent_in_context"
        if item.final == "contradictory":
            problem, item.quotes = check_contradiction(
                item.candidate,
                item.verdict,
                cues,
                min_quote_chars=settings.agreement_quote_min_chars,
            )
            if problem:
                item.final, item.downgrade = "cannot_determine", problem

    majority = settings.agreement_contradiction_majority_of_three
    readings = (
        MAJORITY_EXTRA_READINGS if majority else settings.agreement_contradiction_confirmations
    )
    standing = [j for j in judged if j.final == "contradictory"]
    for reading in range(1, readings + 1):
        terminal_cause = next(
            (
                cause
                for cause in ("quota", "ai_not_configured", "ai_key_rejected")
                if counters[cause]
            ),
            None,
        )
        if terminal_cause is not None:
            for original in standing:
                if not majority or original.confirmations.count("contradictory") == 0:
                    original.confirmation_deferred_reason = terminal_cause
                    _withhold(original)
            break
        if majority:
            # A majority of three: a pair whose first re-ask already agreed has
            # two answers of three and needs no third.
            standing = [j for j in standing if j.confirmations.count("contradictory") == 0]
        if not standing:
            break
        # Each further reading is a different request (its own numbered header,
        # pairs in reverse order), so it can never be answered from a cached
        # copy of an earlier one.
        again = [Judged(j.candidate) for j in reversed(standing)]
        await _judge_all(
            llm,
            again,
            check_run_id=check_run_id,
            chunk_size=chunk_size,
            counters=counters,
            reading=reading,
        )
        for original, repeat in zip(standing, reversed(again), strict=True):
            problem = None
            if repeat.verdict and repeat.verdict.verdict == "contradictory":
                problem, _ = check_contradiction(
                    repeat.candidate,
                    repeat.verdict,
                    cues,
                    min_quote_chars=settings.agreement_quote_min_chars,
                )
            original.confirmation_readings.append(
                {
                    "reading": reading,
                    "response": repeat.verdict.model_dump() if repeat.verdict else None,
                    "reported_attainment": repeat.verdict.reported_attainment
                    if repeat.verdict
                    else None,
                    "source_evidence_verified": repeat.verdict is not None
                    and repeat.verdict.verdict == "contradictory"
                    and problem is None,
                    "validation_reason": problem,
                    "unassessed_reason": repeat.unassessed_reason,
                }
            )
            original.confirmations.append(
                "cannot_determine"
                if problem
                else repeat.verdict.verdict
                if repeat.verdict
                else "none"
            )
        if not majority:
            # Unanimity: the first answer's reason and quotes were already
            # checked against the sent text, and every re-ask has to reach the
            # same verdict. An answer that changes is a reason to send the pair
            # to the instructor, not to accuse.
            for original in standing:
                if original.confirmations[-1] != "contradictory":
                    _withhold(original)
            standing = [j for j in standing if j.final == "contradictory"]
    if majority:
        for original in (j for j in judged if j.final == "contradictory"):
            if 1 + original.confirmations.count("contradictory") < 2:
                _withhold(original)
    return judged, counters


def _withhold(item: Judged) -> None:
    item.final = "cannot_determine"
    item.downgrade = (
        "confirmation_evidence_unverified"
        if any(reading["validation_reason"] for reading in item.confirmation_readings)
        else "confirmation_not_answered"
        if all(answer == "none" for answer in item.confirmations)
        else "verdict_not_stable"
    )


def _assessment_ledger(judged: list[Judged], prepared: "PreparedPairs") -> dict:
    """Keep initial coverage separate from re-asks and unmatched statements.

    A completed comparison only describes its selected pair. It cannot establish
    that lexical recognition found every eligible statement in the manuscript.
    """
    pairs = []
    for item in judged:
        if item.locally_unassessed:
            disposition = "locally_unassessed"
        elif item.verdict is None:
            disposition = "provider_unassessed"
        elif item.final == "cannot_determine":
            disposition = "compared_unresolved"
        else:
            disposition = "compared_resolved"
        attainments = (
            [item.verdict.reported_attainment]
            if item.verdict is not None and not item.locally_unassessed
            else []
        )
        attainments.extend(
            reading["reported_attainment"]
            for reading in item.confirmation_readings
            if reading["response"] is not None
        )
        pairs.append(
            {
                **_pair_record(item),
                "disposition": disposition,
                "unassessed_reason": item.unassessed_reason or item.downgrade,
                "initial_response": item.verdict.model_dump()
                if item.verdict is not None and not item.locally_unassessed
                else None,
                "confirmation_readings": item.confirmation_readings,
                "confirmation_deferred_reason": item.confirmation_deferred_reason,
                "attainment_disagreement": len(set(attainments)) > 1,
            }
        )
    return {
        "version": "v1",
        "recognition_complete": False,
        "recognition_reason": "statement_roles_unverified",
        "selected_pair_count": len(pairs),
        "disposition_counts": {
            disposition: sum(pair["disposition"] == disposition for pair in pairs)
            for disposition in (
                "compared_resolved",
                "compared_unresolved",
                "locally_unassessed",
                "provider_unassessed",
            )
        },
        "pairs": pairs,
        "unmatched_objectives": [
            {"text": item.text, "anchor": item.anchor, "reason": "no_selected_result"}
            for item in prepared.unmatched_intents
        ],
        "unselected_outcomes": prepared.unselected_outcomes,
        "objective_sections": prepared.objective_sections,
        "results_sections": prepared.results_sections,
    }


def _common_detail(candidate: Candidate, verdict: PairVerdict) -> dict:
    return {
        "affects_readiness": False,
        "coverage_reason": "statement_roles_unverified",
        "similarity": round(candidate.similarity, 3),
        "intent_text": candidate.intent.text,
        "intent_anchor": candidate.intent.anchor,
        "outcome_text": candidate.outcome.text,
        "outcome_anchor": candidate.outcome.anchor,
        "gemini_reasoning": verdict.reasoning,
        "prompt_version": PROMPT_VERSION,
        "reported_attainment": verdict.reported_attainment,
        "context_basis": verdict.context_basis,
    }


def _candidate_to_flag(
    candidate: Candidate,
    verdict: PairVerdict,
    final: str,
    injection: InjectionSignal,
    quotes: VerifiedQuotes | None = None,
) -> AgreementFlagDraft | None:
    anchor = candidate.intent.anchor
    common_detail = _common_detail(candidate, verdict)
    if injection.suspected:
        # Traceable regardless of verdict (ground rule 4); for a silent
        # verdict it is the only trace, so it becomes a flag below.
        common_detail["injection_suspected"] = True
        common_detail["injection_matched_pattern"] = injection.matched_pattern_id
        common_detail["injection_matched_snippet"] = injection.matched_snippet
    # A suspect text is a finding whatever the verdict was: a silent or
    # undecided answer must not hide it (BUG-160).
    if final in (*SILENT_VERDICTS, "cannot_determine"):
        if not injection.suspected:
            return None
        return AgreementFlagDraft(
            severity=FlagSeverity.low,
            evidence_excerpt=candidate.intent.text,
            page_anchor=anchor,
            detail={
                "kind": "agreement_injection_suspected",
                "reason": AGREEMENT_INJECTION_SUSPECTED_WORDING,
                "judged_as": final,
                **common_detail,
            },
        )
    if final == "contradictory" and quotes is not None:
        reason_label = contradiction_reasons()[verdict.reason_category]
        # The instructor sees the manuscript's own words, not the model's copy.
        return AgreementFlagDraft(
            severity=FlagSeverity.high,
            evidence_excerpt=candidate.intent.text,
            page_anchor=anchor,
            detail={
                "kind": "agreement_factual_conflict",
                "reason": CONTRADICTORY_WORDING,
                "reason_category": verdict.reason_category,
                "reason_category_label": reason_label,
                "objective_quote": quotes.objective,
                "finding_quote": quotes.finding,
                "finding_quote_source": quotes.finding_source,
                **common_detail,
            },
        )
    if final in ("partial", "unmet"):
        return AgreementFlagDraft(
            severity=FlagSeverity.low,
            evidence_excerpt=candidate.intent.text,
            page_anchor=anchor,
            detail={
                "kind": "agreement_partial" if final == "partial" else "agreement_unmet",
                "reason": PARTIAL_WORDING if final == "partial" else UNMET_WORDING,
                **common_detail,
            },
        )
    # cannot_determine is coverage, not a finding; it goes in the undecided list.
    return None


def _unmatched_intent_flag(intent: Statement) -> AgreementFlagDraft:
    return AgreementFlagDraft(
        severity=FlagSeverity.low,
        evidence_excerpt=intent.text,
        page_anchor=intent.anchor,
        detail={
            "kind": "agreement_unmatched_intent",
            "affects_readiness": False,
            "coverage_reason": "statement_roles_unverified",
            "reason": UNMATCHED_INTENT_WORDING.format(intent=intent.text),
        },
    )


def _pair_record(item: Judged) -> dict:
    candidate = item.candidate
    return {
        "intent_text": candidate.intent.text,
        "intent_anchor": candidate.intent.anchor,
        "outcome_text": candidate.outcome.text,
        "outcome_anchor": candidate.outcome.anchor,
        "reason": item.source_explanation["label"]
        if item.source_explanation
        else item.verdict.reasoning
        if item.verdict
        else None,
        "model_reasoning": item.verdict.reasoning
        if item.verdict and not item.locally_unassessed
        else None,
        "reported_attainment": item.verdict.reported_attainment if item.verdict else "unknown",
        "context_basis": item.source_explanation["basis"]
        if item.source_explanation
        else item.verdict.context_basis
        if item.verdict
        else "unknown",
        "model_verdict": item.verdict.verdict
        if item.verdict and not item.locally_unassessed
        else None,
        "model_context_basis": item.verdict.context_basis
        if item.verdict and not item.locally_unassessed
        else None,
        "explanation_method": item.source_explanation["method"]
        if item.source_explanation
        else "generation"
        if item.verdict and not item.locally_unassessed
        else "not_assessed",
        "source_explanation": item.source_explanation,
        "verdict": item.final,
        "comparison_method": "phase_guard" if item.locally_unassessed else "generation",
        **({"downgrade": item.downgrade} if item.downgrade else {}),
    }


async def run_agreement_pairing(
    llm: LLMClient,
    intents: list[Statement],
    outcomes: list[Statement],
    *,
    check_run_id: int | None,
    settings: Settings,
    allow_absence_leads: bool = True,
    extraction: ExtractionResult | None = None,
    cues: AgreementCues | None = None,
) -> PairingResult:
    """Returns flags, the coverage records the report shows beside them, and
    how many pairs were skipped and why (V-050's availability-floor pattern:
    a degraded run still finishes honestly)."""
    cues = cues or load_cues(settings.agreement_cues_file)
    # BUG-152: off the event loop — local embedding work with no I/O.
    prepared = await asyncio.get_running_loop().run_in_executor(
        None,
        functools.partial(
            prepare_pairs,
            intents,
            outcomes,
            extraction=extraction,
            cues=cues,
            settings=settings,
        ),
    )
    candidates = prepared.candidates
    unmatched_intents = prepared.unmatched_intents
    matched_outcome_indexes = prepared.matched_outcome_indexes

    flags = (
        [_unmatched_intent_flag(intent) for intent in unmatched_intents]
        if allow_absence_leads
        else []
    )
    judged, counters = await judge_pairs(
        llm, candidates, check_run_id=check_run_id, settings=settings, cues=cues
    )
    verdict_counts: dict[str, int] = {}
    downgrades: dict[str, int] = {}
    undetermined, explained = [], []
    for item in judged:
        if item.verdict is None:
            continue
        verdict_counts[item.final] = verdict_counts.get(item.final, 0) + 1
        if item.downgrade:
            downgrades[item.downgrade] = downgrades.get(item.downgrade, 0) + 1
        # Cheap regex, no LLM call — each pair draws on a different part of
        # the manuscript.
        injection = detect_injection_signal(
            f"{item.candidate.intent.text} {item.candidate.outcome.text}"
        )
        draft = _candidate_to_flag(item.candidate, item.verdict, item.final, injection, item.quotes)
        if draft is not None:
            flags.append(draft)
        elif item.final == "cannot_determine":
            undetermined.append(_pair_record(item))
        elif item.final == "consistent_in_context":
            explained.append(_pair_record(item))

    return PairingResult(
        flags=flags,
        n_unmatched_outcomes=prepared.n_results - len(matched_outcome_indexes),
        n_unmatched_intents=len(prepared.unmatched_intents),
        results_sections=prepared.results_sections,
        objective_sections=prepared.objective_sections,
        n_skipped_quota=counters["quota"],
        n_skipped_api_down=counters["api_down"],
        n_skipped_parse_failure=counters["parse_failure"],
        n_skipped_ai_not_configured=counters["ai_not_configured"],
        n_skipped_ai_key_rejected=counters["ai_key_rejected"],
        undetermined=undetermined,
        n_compared=sum(
            1 for item in judged if item.verdict is not None and not item.locally_unassessed
        ),
        n_locally_unassessed=sum(1 for item in judged if item.locally_unassessed),
        verdict_counts=verdict_counts,
        downgrades=downgrades,
        explained_in_context=explained,
        assessment_ledger=_assessment_ledger(judged, prepared),
    )
