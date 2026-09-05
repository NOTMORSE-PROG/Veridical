"""Intent<->outcome pairing + severity flagging (F4.3/F4.4, V-035) — the
second half of the Internal Agreement Check (F4), per proposal Fig 3.9.

**Cascade (D-011, re-measured 2026-08-08 addendum)**: candidate generation
is Tier 1, local (potion-base-8M cosine similarity, `app.ml.embeddings`) —
an outcome below `agreement_pairing_similarity_floor` never reaches the
judgment tier at all, which is what keeps this quota-bounded (an intent
with zero candidates is an unmatched-intent flag with NO Gemini call
spent). Judgment (entail/contradict/partial) is Tier 2, Gemini — local NLI
was measured combined with the real app for the first time and came back
with only 5.6MB of headroom under Render's free-tier ceiling (DECISIONS.md
D-011 addendum), the same "doesn't fit" conclusion V-030 reached for its
own use case, now confirmed for this one too.

**Wording (charter rule 3)**: every flag describes a possible
inconsistency, never an accusation — "does not appear to be addressed",
never "was not done"; "appear to contradict", never "lied about". Silence
means agreement (`consistent` -> no flag), same convention as every other
integrity check in this codebase (F5/F6).
"""

import asyncio
import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.checks.agreement.extract import Statement
from app.checks.injection import InjectionSignal, detect_injection_signal
from app.config import Settings
from app.errors import ApiDownError, QuotaExhaustedError, VeridicalError
from app.llm.base import LLMClient
from app.ml.embeddings import cosine_similarity, embed_texts
from app.models.enums import FlagSeverity

PROMPT_TYPE = "agreement_pairing"
PROMPT_VERSION = "v1"
_PROMPT_FILE = Path(__file__).parent / "prompts" / f"{PROMPT_TYPE}_{PROMPT_VERSION}.txt"

UNMATCHED_INTENT_WORDING = (
    'Objective "{intent}" does not appear to be addressed in the results, '
    "a possible gap or unstated change of scope. Please check manually."
)
CONTRADICTORY_WORDING = (
    "This objective and this result appear to contradict each other, a possible "
    'inconsistency, please check manually. Objective: "{intent}" | Result: "{outcome}"'
)
PARTIAL_WORDING = (
    "This objective appears to be only partially addressed in the results, "
    'a possible incomplete implementation, please check manually. Objective: "{intent}" '
    '| Result: "{outcome}"'
)
CANNOT_DETERMINE_WORDING = (
    "Could not determine whether this result addresses this objective. Please "
    "check manually if this matters for your review."
)
# BUG-160: F4's judgment is a single Gemini call per pair, not a two-pass
# vote (D-006) — there is no "agreement" here for an injected instruction to
# spoil, so `consistency.py`'s "override the vote's outcome" response doesn't
# apply. What DOES apply: `_candidate_to_flag` returns None (silence) on a
# "consistent" verdict, and silence is exactly what must not happen when the
# text behind that verdict is suspect — a manipulated "consistent" would
# otherwise vanish with no trace. Both intent and outcome are the student's
# own manuscript text (unlike F5's fetched abstracts below), so this is
# worded the same way as `INJECTION_SUSPECTED_REASON` in consistency.py: a
# fact about the document, never an accusation.
AGREEMENT_INJECTION_SUSPECTED_WORDING = (
    "This objective and this result appear consistent, but this part of the "
    "document also contains text that appears to address an automated "
    "grader rather than the reader, so that verdict should not be trusted "
    'without a direct look. Objective: "{intent}" | Result: "{outcome}"'
)


class PairingError(VeridicalError):
    """The model's structured output didn't validate — never surfaced past
    this module (same discipline as `ClaimSupportError`)."""


class PairVerdict(BaseModel):
    index: int
    verdict: Literal["consistent", "contradictory", "partial", "cannot_determine"]
    reasoning: str = Field(min_length=1)


class PairVerdictBatchResponse(BaseModel):
    verdicts: list[PairVerdict] = Field(min_length=1)


@dataclass(frozen=True)
class Candidate:
    intent: Statement
    outcome: Statement
    similarity: float


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
    # BUG-072: three distinct causes, three distinct counters -- the
    # original single `n_skipped_for_quota` reported a genuine
    # PairingError (the model's structured output didn't validate, D-017's
    # exact defect class) as ordinary budget exhaustion, making a real
    # regression permanently indistinguishable from "we ran out of quota."
    n_skipped_quota: int
    n_skipped_api_down: int
    n_skipped_parse_failure: int

    @property
    def n_skipped_total(self) -> int:
        return self.n_skipped_quota + self.n_skipped_api_down + self.n_skipped_parse_failure


# --- candidate generation (Tier 1, local) -------------------------------------


def generate_candidates(
    intents: list[Statement],
    outcomes: list[Statement],
    *,
    similarity_floor: float,
    max_candidates_per_intent: int,
    settings: Settings | None = None,
) -> tuple[list[Candidate], list[Statement], set[int]]:
    """Returns (candidates, unmatched_intents, matched_outcome_indexes).

    `matched_outcome_indexes` tracks every outcome that appears in AT LEAST
    ONE candidate pair — the complement is what V-037/the caller reports as
    unmatched outcomes (ticket AC: an info-level note, never a flag)."""
    if not intents or not outcomes:
        return [], list(intents), set()

    intent_vectors = embed_texts([s.text for s in intents], settings)
    outcome_vectors = embed_texts([s.text for s in outcomes], settings)

    candidates: list[Candidate] = []
    unmatched_intents: list[Statement] = []
    matched_outcome_indexes: set[int] = set()

    for i, intent in enumerate(intents):
        scored = [
            (cosine_similarity(intent_vectors[i], outcome_vectors[j]), j)
            for j in range(len(outcomes))
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        above_floor = [(sim, j) for sim, j in scored if sim >= similarity_floor]
        if not above_floor:
            unmatched_intents.append(intent)
            continue
        for sim, j in above_floor[:max_candidates_per_intent]:
            candidates.append(Candidate(intent=intent, outcome=outcomes[j], similarity=sim))
            matched_outcome_indexes.add(j)

    return candidates, unmatched_intents, matched_outcome_indexes


# --- judgment (Tier 2, Gemini) -------------------------------------------------


def _build_prompt(pairs: list[Candidate]) -> str:
    lines = []
    for i, pair in enumerate(pairs):
        lines.append(f'[{i}] OBJECTIVE: "{pair.intent.text}"')
        lines.append(f'[{i}] FINDING: "{pair.outcome.text}"')
    template = _PROMPT_FILE.read_text(encoding="utf-8")
    return template.replace("{pairs_list}", "\n".join(lines))


async def _judge_batch(
    llm: LLMClient, pairs: list[Candidate], *, check_run_id: int | None
) -> dict[int, PairVerdict]:
    prompt = _build_prompt(pairs)
    response = await llm.complete(
        PROMPT_TYPE, prompt, prompt_version=PROMPT_VERSION, check_run_id=check_run_id
    )
    try:
        parsed = PairVerdictBatchResponse.model_validate(response)
    except Exception as exc:  # noqa: BLE001 — bad shape degrades honestly, never crashes the run
        raise PairingError(f"agreement_pairing response failed validation: {exc}") from exc
    return {v.index: v for v in parsed.verdicts}


def _candidate_to_flag(
    candidate: Candidate, verdict: PairVerdict, injection: InjectionSignal
) -> AgreementFlagDraft | None:
    anchor = candidate.intent.anchor
    common_detail = {
        "similarity": round(candidate.similarity, 3),
        "intent_text": candidate.intent.text,
        "intent_anchor": candidate.intent.anchor,
        "outcome_text": candidate.outcome.text,
        "outcome_anchor": candidate.outcome.anchor,
        "gemini_reasoning": verdict.reasoning,
    }
    if injection.suspected:
        # Traceable regardless of verdict (ground rule 4) — a
        # contradictory/partial/cannot_determine verdict is already a flag,
        # so this only ADDS evidence; "consistent" is the branch below where
        # it changes the outcome (silence -> a flag).
        common_detail["injection_suspected"] = True
        common_detail["injection_matched_pattern"] = injection.matched_pattern_id
        common_detail["injection_matched_snippet"] = injection.matched_snippet
    if verdict.verdict == "consistent":
        if not injection.suspected:
            return None  # confirmed = silence, same convention as F5/F6
        return AgreementFlagDraft(
            severity=FlagSeverity.low,
            evidence_excerpt=candidate.intent.text,
            page_anchor=anchor,
            detail={
                "kind": "agreement_injection_suspected",
                "reason": AGREEMENT_INJECTION_SUSPECTED_WORDING.format(
                    intent=candidate.intent.text, outcome=candidate.outcome.text
                ),
                **common_detail,
            },
        )
    if verdict.verdict == "contradictory":
        return AgreementFlagDraft(
            severity=FlagSeverity.high,
            evidence_excerpt=candidate.intent.text,
            page_anchor=anchor,
            detail={
                "kind": "agreement_contradictory",
                "reason": CONTRADICTORY_WORDING.format(
                    intent=candidate.intent.text, outcome=candidate.outcome.text
                ),
                **common_detail,
            },
        )
    if verdict.verdict == "partial":
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
    return AgreementFlagDraft(  # cannot_determine
        severity=FlagSeverity.low,
        evidence_excerpt=candidate.intent.text,
        page_anchor=anchor,
        detail={
            "kind": "agreement_cannot_determine",
            "reason": CANNOT_DETERMINE_WORDING,
            **common_detail,
        },
    )


def _unmatched_intent_flag(intent: Statement) -> AgreementFlagDraft:
    return AgreementFlagDraft(
        severity=FlagSeverity.low,
        evidence_excerpt=intent.text,
        page_anchor=intent.anchor,
        detail={
            "kind": "agreement_unmatched_intent",
            "reason": UNMATCHED_INTENT_WORDING.format(intent=intent.text),
        },
    )


async def run_agreement_pairing(
    llm: LLMClient,
    intents: list[Statement],
    outcomes: list[Statement],
    *,
    check_run_id: int | None,
    settings: Settings,
) -> PairingResult:
    """Returns flags + the unmatched-outcome count (info-level, never a
    flag — ticket AC) + how many candidate pairs were skipped for quota
    (V-050's availability-floor pattern: a degraded run still finishes
    honestly, the judged pairs keep their real verdicts)."""
    # BUG-152: off the event loop -- embeds every intent/outcome statement
    # locally (model2vec), real CPU work with no I/O, same reasoning as
    # the sibling fix in `checks/reuse/service.py`.
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
        ),
    )

    flags = [_unmatched_intent_flag(intent) for intent in unmatched_intents]
    n_quota = n_api_down = n_parse_failure = 0

    chunk_size = max(1, settings.agreement_pairing_max_pairs_per_call)
    for start in range(0, len(candidates), chunk_size):
        chunk = candidates[start : start + chunk_size]
        try:
            verdicts = await _judge_batch(llm, chunk, check_run_id=check_run_id)
        except QuotaExhaustedError:
            # BUG-080: quota exhaustion is terminal for the whole run, not
            # just this chunk -- every remaining candidate would fail
            # identically, so count them all as skipped and stop instead of
            # retrying.
            n_quota += len(candidates) - start
            break
        except ApiDownError:
            n_api_down += len(chunk)
            continue
        except PairingError:
            n_parse_failure += len(chunk)
            continue
        for i, candidate in enumerate(chunk):
            verdict = verdicts.get(i)
            if verdict is None:
                # Same D-017 defect class as the batch-level PairingError
                # above (the model silently omitted one pair's verdict
                # from an otherwise-valid batch), not a quota/availability
                # issue.
                n_parse_failure += 1
                continue
            # Cheap regex, no LLM call — computed per candidate, not per
            # chunk, since each pair draws on a different, unrelated part
            # of the manuscript (unlike consistency.py's batch.context_text,
            # which several criteria genuinely share).
            injection = detect_injection_signal(f"{candidate.intent.text} {candidate.outcome.text}")
            draft = _candidate_to_flag(candidate, verdict, injection)
            if draft is not None:
                flags.append(draft)

    n_unmatched_outcomes = len(outcomes) - len(matched_outcome_indexes)
    return PairingResult(
        flags=flags,
        n_unmatched_outcomes=n_unmatched_outcomes,
        n_skipped_quota=n_quota,
        n_skipped_api_down=n_api_down,
        n_skipped_parse_failure=n_parse_failure,
    )
