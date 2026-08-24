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
check_run has, chunked only if the pair count exceeds
`claim_support_max_pairs_per_call`.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.checks.citations.extract import CitationFlagDraft
from app.config import Settings
from app.errors import ApiDownError, QuotaExhaustedError, VeridicalError
from app.llm.base import LLMClient
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


class ClaimSupportError(VeridicalError):
    """The model's structured output didn't validate, or an excerpt failed
    the containment check — never surfaced past this module (same
    discipline as `app.checks.semantic.SemanticGradeError`)."""


class ClaimSupportVerdict(BaseModel):
    index: int
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
    return {v.index: v for v in parsed.verdicts}


def _verdict_to_flag(
    pair: ClaimSupportInput, verdict: ClaimSupportVerdict
) -> CitationFlagDraft | None:
    anchor = f"reference #{pair.citation.order_index + 1}"
    if verdict.verdict == "supported":
        return None  # matches the rest of this check family: confirmed = silence
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
            },
        )
    return CitationFlagDraft(  # cannot_determine
        severity=FlagSeverity.low,
        evidence_excerpt=pair.claim_sentence,
        page_anchor=anchor,
        detail={"kind": "claim_support_cannot_determine", "reason": CANNOT_DETERMINE_WORDING},
    )


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

    @property
    def n_skipped_total(self) -> int:
        return self.n_skipped_quota + self.n_skipped_api_down + self.n_skipped_parse_failure


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
    chunk_size = max(1, settings.claim_support_max_pairs_per_call)
    for start in range(0, len(pairs), chunk_size):
        chunk = pairs[start : start + chunk_size]
        try:
            verdicts = await _judge_batch(llm, chunk, check_run_id=check_run_id)
        except QuotaExhaustedError:
            # BUG-080: quota exhaustion is terminal for the whole run, not
            # just this chunk -- every remaining pair would fail identically,
            # so count them all as skipped and stop instead of retrying.
            n_quota += len(pairs) - start
            break
        except ApiDownError:
            n_api_down += len(chunk)
            continue
        except ClaimSupportError:
            n_parse_failure += len(chunk)
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
                continue
            draft = _verdict_to_flag(pair, verdict)
            if draft is not None:
                flags.append(draft)
    return ClaimSupportResult(flags, n_quota, n_api_down, n_parse_failure)
