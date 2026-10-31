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
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.checks.agreement.context import DocumentContext, PairContext
from app.checks.agreement.cues import AgreementCues, load_cues
from app.checks.agreement.extract import Statement
from app.checks.injection import InjectionSignal, detect_injection_signal
from app.config import Settings
from app.errors import ApiDownError, QuotaExhaustedError, VeridicalError
from app.ingest.schemas import ExtractionResult
from app.llm.base import LLMClient
from app.llm.resolution import AIUnavailableError
from app.ml.embeddings import cosine_similarity, embed_texts
from app.models.enums import FlagSeverity

PROMPT_TYPE = "agreement_pairing"
PROMPT_VERSION = "v2"
_PROMPT_FILE = Path(__file__).parent / "prompts" / f"{PROMPT_TYPE}_{PROMPT_VERSION}.txt"
_REASONS_FILE = Path(__file__).parent / "data" / "contradiction_reasons.json"

UNMATCHED_INTENT_WORDING = (
    'Objective "{intent}" does not appear to be addressed in the results, '
    "a possible gap or unstated change of scope. Please check manually."
)
CONTRADICTORY_WORDING = (
    "This result appears to contradict this objective ({reason}), a possible "
    'inconsistency, please check manually. Objective: "{intent}" | Result: "{outcome}"'
)
PARTIAL_WORDING = (
    "This objective appears to be only partially addressed in the results, "
    'a possible incomplete implementation, please check manually. Objective: "{intent}" '
    '| Result: "{outcome}"'
)
# BUG-160: a "consistent" verdict is silence, and silence is exactly what must
# not happen when the text behind that verdict is suspect — a manipulated
# "consistent" would otherwise vanish with no trace. Both texts are the
# student's own manuscript, so this is a fact about the document, never an
# accusation.
AGREEMENT_INJECTION_SUSPECTED_WORDING = (
    "This objective and this result appear consistent, but this part of the "
    "document also contains text that appears to address an automated "
    "grader rather than the reader, so that verdict should not be trusted "
    'without a direct look. Objective: "{intent}" | Result: "{outcome}"'
)

Verdict = Literal[
    "consistent",
    "consistent_in_context",
    "partial",
    "contradictory",
    "unrelated",
    "cannot_determine",
]
# Verdicts that are not findings: the judge saw no problem, or a problem the
# manuscript itself explains, or two statements about different things.
SILENT_VERDICTS = ("consistent", "consistent_in_context", "unrelated")
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
) -> tuple[list[Candidate], list[Statement], set[int]]:
    """Returns (candidates, unmatched_intents, matched_outcome_indexes).

    Each objective is paired with its most similar results, up to the
    configured number. A result that says outright that an objective was
    not met (`status_patterns`) is also paired with the objective it is most
    similar to even when it is not among that objective's top matches, so
    the plainest statement of a gap is never left unjudged."""
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
    taken: set[tuple[int, int]] = set()

    for i, intent in enumerate(intents):
        ranked = sorted(((scores[i][j], j) for j in range(len(outcomes))), reverse=True)
        above_floor = [(sim, j) for sim, j in ranked if sim >= similarity_floor]
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
        sim, i = max((scores[i][j], i) for i in range(len(intents)))
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


def with_context(
    candidates: list[Candidate], extraction: ExtractionResult | None, cues, settings
) -> list[Candidate]:
    """Attach each pair's surrounding text and the manuscript's own scope
    statements. Without an extraction the pairs go as they are."""
    if extraction is None or not candidates:
        return candidates
    document = DocumentContext.build(extraction, cues, settings)
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
    return json.loads(_REASONS_FILE.read_text(encoding="utf-8"))["reasons"]


def _quoted(text: str) -> str:
    return text.replace('"', "'")


SECOND_READING_NOTE = (
    "SECOND READING: these pairs are being checked again, independently. Judge each one "
    "afresh from its own texts; do not assume any earlier answer."
)


def _build_prompt(pairs: list[Candidate], *, second_reading: bool = False) -> str:
    lines = [SECOND_READING_NOTE] if second_reading else []
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


async def _judge_batch(
    llm: LLMClient,
    pairs: list[Candidate],
    *,
    check_run_id: int | None,
    second_reading: bool = False,
) -> dict[int, PairVerdict]:
    prompt = _build_prompt(pairs, second_reading=second_reading)
    response = await llm.complete(
        PROMPT_TYPE, prompt, prompt_version=PROMPT_VERSION, check_run_id=check_run_id
    )
    try:
        parsed = PairVerdictBatchResponse.model_validate(response)
    except Exception as exc:  # noqa: BLE001 — bad shape degrades honestly, never crashes the run
        raise PairingError(f"agreement_pairing response failed validation: {exc}") from exc
    indexes = [v.index for v in parsed.verdicts]
    if len(indexes) != len(set(indexes)) or set(indexes) != set(range(len(pairs))):
        raise PairingError("agreement_pairing returned duplicate, missing, or invalid pair indexes")
    return {v.index: v for v in parsed.verdicts}


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


FINDING_QUOTE_SOURCES = ("finding", "text_before", "text_after")


@dataclass(frozen=True)
class VerifiedQuotes:
    """The manuscript's own words a contradiction rests on, as found in the
    text that was sent, and where the finding side came from."""

    objective: str
    finding: str
    finding_source: str


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
    # Two different named systems (a "CollarID" objective against an
    # "AirSpecs" result) are two different things, whatever the wording. The
    # finding side is read from the sentence the quote came from as well.
    objective_names = _named_systems(candidate.intent.text, cues)
    finding_text = " ".join({candidate.outcome.text, finding_side[finding[0]]})
    finding_names = _named_systems(finding_text, cues)
    if (
        objective_names
        and finding_names
        and not objective_names & finding_names
        and not any(name in finding_text for name in objective_names)
    ):
        return "different_named_system", None
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


async def _judge_all(
    llm: LLMClient,
    judged: list[Judged],
    *,
    check_run_id: int | None,
    chunk_size: int,
    counters: dict[str, int],
    second_reading: bool = False,
) -> None:
    """Judge every pair in batches; fills `verdict`, counts skips by cause."""
    for start in range(0, len(judged), chunk_size):
        chunk = judged[start : start + chunk_size]
        try:
            verdicts = await _judge_batch(
                llm,
                [j.candidate for j in chunk],
                check_run_id=check_run_id,
                second_reading=second_reading,
            )
        except AIUnavailableError as exc:
            counters[exc.cause] += len(judged) - start
            return
        except QuotaExhaustedError:
            # BUG-080: terminal for the whole run — every remaining pair would
            # fail the same way, so count them all and stop.
            counters["quota"] += len(judged) - start
            return
        except ApiDownError:
            counters["api_down"] += len(chunk)
            continue
        except PairingError:
            counters["parse_failure"] += len(chunk)
            continue
        for i, item in enumerate(chunk):
            verdict = verdicts.get(i)
            if verdict is None:
                counters["parse_failure"] += 1
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
    await _judge_all(
        llm, judged, check_run_id=check_run_id, chunk_size=chunk_size, counters=counters
    )
    for item in judged:
        if item.verdict is None:
            continue
        item.final = item.verdict.verdict
        if item.final == "contradictory":
            problem, item.quotes = check_contradiction(
                item.candidate,
                item.verdict,
                cues,
                min_quote_chars=settings.agreement_quote_min_chars,
            )
            if problem:
                item.final, item.downgrade = "cannot_determine", problem

    for _ in range(settings.agreement_contradiction_confirmations):
        standing = [j for j in judged if j.final == "contradictory"]
        if not standing:
            break
        # The second reading is a different request (its own header, pairs in
        # reverse order), so it can never be answered from a cached copy of
        # the first one.
        again = [Judged(j.candidate) for j in reversed(standing)]
        await _judge_all(
            llm,
            again,
            check_run_id=check_run_id,
            chunk_size=chunk_size,
            counters=counters,
            second_reading=True,
        )
        for original, repeat in zip(standing, reversed(again), strict=True):
            original.confirmations.append(repeat.verdict.verdict if repeat.verdict else "none")
            # The first answer's reason and quotes were already checked against
            # the sent text; the re-ask only has to reach the same verdict.
            confirmed = repeat.verdict is not None and repeat.verdict.verdict == "contradictory"
            if not confirmed:
                # An answer that changes when asked again is a reason to send
                # the pair to the instructor, not to accuse.
                original.final = "cannot_determine"
                original.downgrade = (
                    "confirmation_not_answered" if repeat.verdict is None else "verdict_not_stable"
                )
    return judged, counters


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
                "reason": AGREEMENT_INJECTION_SUSPECTED_WORDING.format(
                    intent=candidate.intent.text, outcome=candidate.outcome.text
                ),
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
                "kind": "agreement_contradictory",
                "reason": CONTRADICTORY_WORDING.format(
                    reason=reason_label, intent=quotes.objective, outcome=quotes.finding
                ),
                "reason_category": verdict.reason_category,
                "objective_quote": quotes.objective,
                "finding_quote": quotes.finding,
                "finding_quote_source": quotes.finding_source,
                **common_detail,
            },
        )
    if final == "partial":
        return AgreementFlagDraft(
            severity=FlagSeverity.low,
            evidence_excerpt=candidate.intent.text,
            page_anchor=anchor,
            detail={
                "kind": "agreement_partial",
                "reason": PARTIAL_WORDING.format(
                    intent=candidate.intent.text, outcome=candidate.outcome.text
                ),
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
        "reason": item.verdict.reasoning if item.verdict else None,
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
    (
        candidates,
        unmatched_intents,
        matched_outcome_indexes,
    ) = await asyncio.get_running_loop().run_in_executor(
        None,
        functools.partial(
            generate_candidates,
            intents,
            outcomes,
            similarity_floor=settings.agreement_pairing_similarity_floor,
            max_candidates_per_intent=settings.agreement_pairing_max_candidates_per_intent,
            settings=settings,
            status_patterns=cues.objective_status_patterns,
        ),
    )
    candidates = await asyncio.get_running_loop().run_in_executor(
        None, functools.partial(with_context, candidates, extraction, cues, settings)
    )

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
        n_unmatched_outcomes=len(outcomes) - len(matched_outcome_indexes),
        n_skipped_quota=counters["quota"],
        n_skipped_api_down=counters["api_down"],
        n_skipped_parse_failure=counters["parse_failure"],
        n_skipped_ai_not_configured=counters["ai_not_configured"],
        n_skipped_ai_key_rejected=counters["ai_key_rejected"],
        undetermined=undetermined,
        n_compared=sum(1 for item in judged if item.verdict is not None),
        verdict_counts=verdict_counts,
        downgrades=downgrades,
        explained_in_context=explained,
    )
