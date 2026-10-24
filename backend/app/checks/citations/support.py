"""Claim-support check (F5.4, V-030): does a cited source's own abstract
actually support the sentence the manuscript uses it to back up?

**D-011 impact note, MEASURED not assumed (2026-08-06, DECISIONS.md D-011
follow-up)**: the ticket's own primary design (local NLI — quantized
nli-deberta-v3-xsmall + potion-base-8M) was actually loaded and measured
in a bare process: peak working set 487MB, essentially the entire Render
512MB free-tier ceiling for the ML stack ALONE, before FastAPI/asyncpg/
PyMuPDF. D-011 itself named this exact outcome as the reason to fall back
to Tier 2 (Gemini) for NLI specifically — this module does that. No
search/ranking step exists here to make the embedding tier worth its
footprint either (V-027 already links one claim sentence per citation,
V-029 already resolves one verified source per citation — there is no
candidate set to prefilter).

Batched into as few Gemini calls as possible per manuscript (D-001/D-011
quota discipline) — same pattern as `app.checks.semantic`'s grading
batches, generalized: one call for every claim/abstract pair this
check_run admits, bounded by `claim_support_max_pairs_per_run` and chunked by
`claim_support_max_pairs_per_call`. Every input pair retains a terminal record.
"""

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.checks.citations.extract import CitationFlagDraft
from app.checks.injection import InjectionSignal, detect_injection_signal
from app.config import Settings
from app.errors import ApiDownError, QuotaExhaustedError, VeridicalError
from app.llm.base import LLMClient
from app.llm.resolution import AIUnavailableError
from app.models.citation import Citation
from app.models.enums import FlagSeverity

PROMPT_TYPE = "claim_support"
PROMPT_VERSION = "v1"
_PROMPT_FILE = Path(__file__).parent / "prompts" / f"{PROMPT_TYPE}_{PROMPT_VERSION}.txt"

_WHITESPACE = re.compile(r"\s+")

POSSIBLY_UNSUPPORTED_WORDING = (
    "This citation may not support the claim it's attached to, please "
    'review. Claim: "{claim}" | Source abstract says: "{excerpt}"'
)
CANNOT_DETERMINE_WORDING = (
    "Could not determine whether this source supports the claim it's "
    "attached to (the abstract may not cover the specific point cited), "
    "please check manually if this matters for your review."
)
# BUG-160: like F4, this is a single judgment call with no vote to distrust,
# so the response is the same as pair.py's — don't let a manipulated
# "supported" verdict vanish as silence. But F5 has a wrinkle F4 doesn't:
# the abstract is fetched automatically from CrossRef/Semantic Scholar, not
# authored by the student, so a match there is not even a fact about the
# manuscript. Two wordings, chosen by where the match actually was, so the
# instructor isn't pointed at the student's own claim for a third party's
# text (ground rule 3 cuts both ways: don't accuse the wrong party either).
#
# `ABSTRACT_INJECTION_SUSPECTED_WORDING` names the matched abstract snippet
# directly (backend review finding, BUG-160 review): `evidence_excerpt` on
# this check family is always `claim_sentence`, never the abstract, so
# without the snippet embedded here an instructor would be told a fetched
# abstract "contains suspicious language" with nothing they could actually
# check without independently refetching and re-reading it themselves — an
# unverifiable claim (charter judgment-heuristic 1). The claim-side wording
# doesn't need this: `evidence_excerpt`/`Claim: "{claim}"` already show the
# full claim sentence the match came from.
CLAIM_INJECTION_SUSPECTED_WORDING = (
    "This citation appears to support the claim, but the manuscript text "
    "here also contains language that appears to address an automated "
    "grader rather than the reader, so that verdict should not be trusted "
    'without a direct look. Claim: "{claim}"'
)
ABSTRACT_INJECTION_SUSPECTED_WORDING = (
    "This citation appears to support the claim, but the source's own "
    "abstract (fetched automatically from its indexed record, not written "
    "by the student) contains language that appears to address an "
    "automated grader rather than the reader, so that verdict should not "
    'be trusted without a direct look. Claim: "{claim}" | Abstract text: '
    '"{snippet}"'
)


class ClaimSupportError(VeridicalError):
    """The model's structured output didn't validate, or an excerpt failed
    the containment check — never surfaced past this module (same
    discipline as `app.checks.semantic.SemanticGradeError`)."""


class ClaimSupportVerdict(BaseModel):
    index: int = Field(strict=True, ge=0)
    verdict: Literal["supported", "possibly_unsupported", "cannot_determine"]
    reasoning: str = Field(min_length=1)
    abstract_excerpt: str | None = None

    @field_validator("abstract_excerpt")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value else None


class ClaimSupportBatchResponse(BaseModel):
    verdicts: list[ClaimSupportVerdict] = Field(min_length=1)


@dataclass(frozen=True)
class ClaimSupportInput:
    citation: Citation
    claim_sentence: str
    abstract: str
    claim_anchor: str | None = None


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().lower()


def _excerpt_in_abstract(excerpt: str, abstract: str) -> bool:
    normalized = _normalize(excerpt)
    return bool(normalized) and normalized in _normalize(abstract)


def _build_prompt(pairs: list[ClaimSupportInput]) -> str:
    # `.replace()`, not `.format()` — the template's own JSON example
    # contains literal `{`/`}` that `.format()` would misparse as
    # placeholders (same convention as `app.checks.semantic._build_prompt`).
    lines = []
    for i, pair in enumerate(pairs):
        lines.append(f'[{i}] CLAIM: "{pair.claim_sentence}"')
        lines.append(f'[{i}] ABSTRACT: "{pair.abstract}"')
    template = _PROMPT_FILE.read_text(encoding="utf-8")
    return template.replace("{pairs_list}", "\n".join(lines))


async def _judge_batch(
    llm: LLMClient, pairs: list[ClaimSupportInput], *, check_run_id: int | None
) -> dict[int, ClaimSupportVerdict]:
    prompt = _build_prompt(pairs)
    response = await llm.complete(
        PROMPT_TYPE, prompt, prompt_version=PROMPT_VERSION, check_run_id=check_run_id
    )
    try:
        parsed = ClaimSupportBatchResponse.model_validate(response)
    except Exception as exc:  # noqa: BLE001 — any bad shape degrades honestly, never crashes the run
        raise ClaimSupportError(f"claim_support response failed validation: {exc}") from exc
    verdicts = {}
    for verdict in parsed.verdicts:
        if verdict.index >= len(pairs) or verdict.index in verdicts:
            # A conflicting or extraneous mapping cannot identify trusted work.
            raise ClaimSupportError("claim_support response has duplicate or out-of-range indexes")
        verdicts[verdict.index] = verdict
    if len(verdicts) != len(pairs):
        raise ClaimSupportError("claim_support response omits an input index")
    return verdicts


def _verdict_to_flag(
    pair: ClaimSupportInput,
    verdict: ClaimSupportVerdict,
    claim_injection: InjectionSignal,
    abstract_injection: InjectionSignal,
) -> CitationFlagDraft | None:
    anchor = pair.claim_anchor or f"reference #{pair.citation.order_index + 1}"
    injection_detail: dict = {}
    if claim_injection.suspected or abstract_injection.suspected:
        # Traceable regardless of verdict (ground rule 4); which side
        # matched is recorded so the instructor can tell manuscript text
        # from third-party abstract text at a glance.
        matched = abstract_injection if abstract_injection.suspected else claim_injection
        injection_detail = {
            "injection_suspected": True,
            "injection_source": "abstract" if abstract_injection.suspected else "claim",
            "injection_matched_pattern": matched.matched_pattern_id,
            "injection_matched_snippet": matched.matched_snippet,
        }
    if verdict.verdict == "supported":
        if not injection_detail:
            return None  # matches the rest of this check family: confirmed = silence
        if abstract_injection.suspected:
            reason = ABSTRACT_INJECTION_SUSPECTED_WORDING.format(
                claim=pair.claim_sentence, snippet=abstract_injection.matched_snippet
            )
        else:
            reason = CLAIM_INJECTION_SUSPECTED_WORDING.format(claim=pair.claim_sentence)
        return CitationFlagDraft(
            severity=FlagSeverity.low,
            evidence_excerpt=pair.claim_sentence,
            page_anchor=anchor,
            detail={
                "kind": "claim_support_injection_suspected",
                "reason": reason,
                **injection_detail,
            },
        )
    if verdict.verdict == "possibly_unsupported":
        excerpt = verdict.abstract_excerpt
        if not excerpt or not _excerpt_in_abstract(excerpt, pair.abstract):
            # Charter rule 1: never show unverifiable evidence. Downgrade to
            # the honest "can't determine" wording rather than presenting a
            # possibly-hallucinated quote as the reason for a flag.
            return CitationFlagDraft(
                severity=FlagSeverity.low,
                evidence_excerpt=pair.claim_sentence,
                page_anchor=anchor,
                detail={
                    "kind": "claim_support_cannot_determine",
                    "reason": CANNOT_DETERMINE_WORDING,
                    **injection_detail,
                },
            )
        return CitationFlagDraft(
            severity=FlagSeverity.med,
            evidence_excerpt=pair.claim_sentence,
            page_anchor=anchor,
            detail={
                "kind": "claim_possibly_unsupported",
                "reason": POSSIBLY_UNSUPPORTED_WORDING.format(
                    claim=pair.claim_sentence, excerpt=excerpt
                ),
                "abstract_excerpt": excerpt,
                **injection_detail,
            },
        )
    return CitationFlagDraft(  # cannot_determine
        severity=FlagSeverity.low,
        evidence_excerpt=pair.claim_sentence,
        page_anchor=anchor,
        detail={
            "kind": "claim_support_cannot_determine",
            "reason": CANNOT_DETERMINE_WORDING,
            **injection_detail,
        },
    )


@dataclass(frozen=True)
class ClaimSupportPairResult:
    pair_index: int
    status: Literal["assessed", "unassessed"]
    unassessed_reason: str | None = None
    batch_index: int | None = None
    batch_local_index: int | None = None
    model_verdict: str | None = None
    model_reasoning: str | None = None
    abstract_excerpt: str | None = None
    effective_verdict: str | None = None
    flag_kind: str | None = None


@dataclass(frozen=True)
class ClaimSupportResult:
    flags: list[CitationFlagDraft]
    # BUG-072: three distinct causes, three distinct counters -- collapsing
    # them into one "skipped for quota" counter (the pre-fix shape) reported
    # a genuine ClaimSupportError (the model's structured output didn't
    # validate, D-017's exact defect class) as if it were ordinary budget
    # exhaustion, which would make a real regression permanently
    # indistinguishable from "we ran out of quota today."
    n_skipped_quota: int
    n_skipped_api_down: int
    n_skipped_parse_failure: int
    n_skipped_limit: int = 0
    pair_results: list[ClaimSupportPairResult] = field(default_factory=list)
    n_skipped_ai_not_configured: int = 0
    n_skipped_ai_key_rejected: int = 0

    @property
    def n_skipped_total(self) -> int:
        return (
            self.n_skipped_quota
            + self.n_skipped_api_down
            + self.n_skipped_parse_failure
            + self.n_skipped_limit
            + self.n_skipped_ai_not_configured
            + self.n_skipped_ai_key_rejected
        )


async def run_claim_support_check(
    llm: LLMClient, pairs: list[ClaimSupportInput], *, check_run_id: int | None, settings: Settings
) -> ClaimSupportResult:
    """A degraded run still finishes honestly (V-050's availability-floor
    pattern, applied here): whichever pairs got judged keep their real
    verdicts, and the rest are counted -- by real cause, not lumped
    together -- never silently dropped or faked as clean."""
    if not pairs:
        return ClaimSupportResult([], 0, 0, 0)

    flags: list[CitationFlagDraft] = []
    n_quota = n_api_down = n_parse_failure = 0
    unavailable = {"ai_not_configured": 0, "ai_key_rejected": 0}
    admitted_count = min(len(pairs), settings.claim_support_max_pairs_per_run)
    n_limit = len(pairs) - admitted_count
    pair_results = [
        ClaimSupportPairResult(index, "unassessed", "limit")
        for index in range(admitted_count, len(pairs))
    ]
    chunk_size = max(1, settings.claim_support_max_pairs_per_call)
    for start in range(0, admitted_count, chunk_size):
        chunk = pairs[start : min(start + chunk_size, admitted_count)]
        batch_index = start // chunk_size

        def skipped(
            index: int,
            reason: str,
            *,
            batch_start: int = start,
            batch_size: int = len(chunk),
            attempted_batch: int = batch_index,
        ) -> ClaimSupportPairResult:
            attempted = index < batch_start + batch_size
            return ClaimSupportPairResult(
                index,
                "unassessed",
                reason,
                batch_index=attempted_batch if attempted else None,
                batch_local_index=index - batch_start if attempted else None,
            )

        try:
            verdicts = await _judge_batch(llm, chunk, check_run_id=check_run_id)
        except AIUnavailableError as exc:
            unavailable[exc.cause] += admitted_count - start
            pair_results.extend(
                skipped(index, exc.cause)
                if exc.request_attempted
                else ClaimSupportPairResult(index, "unassessed", exc.cause)
                for index in range(start, admitted_count)
            )
            break
        except QuotaExhaustedError:
            # BUG-080: quota exhaustion is terminal for the whole run, not
            # just this chunk -- every remaining pair would fail identically,
            # so count them all as skipped and stop instead of retrying.
            n_quota += admitted_count - start
            pair_results.extend(skipped(index, "quota") for index in range(start, admitted_count))
            break
        except ApiDownError:
            n_api_down += len(chunk)
            pair_results.extend(skipped(start + i, "api_down") for i in range(len(chunk)))
            continue
        except ClaimSupportError:
            n_parse_failure += len(chunk)
            pair_results.extend(skipped(start + i, "parse_failure") for i in range(len(chunk)))
            continue
        for i, pair in enumerate(chunk):
            verdict = verdicts.get(i)
            if verdict is None:
                # A single pair's structured output failed validation
                # inside an otherwise-successful batch (`_judge_batch`
                # only omits the index, it doesn't raise) -- same D-017
                # defect class as the batch-level ClaimSupportError above,
                # not a quota or availability issue.
                n_parse_failure += 1
                pair_results.append(skipped(start + i, "parse_failure"))
                continue
            # Cheap regex, no LLM call — checked separately so the flag can
            # name which side matched (the student's claim sentence, or the
            # third-party abstract fetched for this citation).
            draft = _verdict_to_flag(
                pair,
                verdict,
                detect_injection_signal(pair.claim_sentence),
                detect_injection_signal(pair.abstract),
            )
            if draft is not None:
                draft = replace(
                    draft,
                    detail={
                        **draft.detail,
                        "reference_order_index": pair.citation.order_index,
                        "claim_support_pair_index": start + i,
                    },
                )
                flags.append(draft)
            kind = draft.detail.get("kind") if draft else None
            pair_results.append(
                ClaimSupportPairResult(
                    pair_index=start + i,
                    status="assessed",
                    batch_index=batch_index,
                    batch_local_index=i,
                    model_verdict=verdict.verdict,
                    model_reasoning=verdict.reasoning,
                    abstract_excerpt=verdict.abstract_excerpt
                    if verdict.abstract_excerpt
                    and _excerpt_in_abstract(verdict.abstract_excerpt, pair.abstract)
                    else None,
                    effective_verdict="cannot_determine"
                    if kind == "claim_support_cannot_determine"
                    else verdict.verdict,
                    flag_kind=kind,
                )
            )
    return ClaimSupportResult(
        flags,
        n_quota,
        n_api_down,
        n_parse_failure,
        n_limit,
        sorted(pair_results, key=lambda result: result.pair_index),
        n_skipped_ai_not_configured=unavailable["ai_not_configured"],
        n_skipped_ai_key_rejected=unavailable["ai_key_rejected"],
    )
