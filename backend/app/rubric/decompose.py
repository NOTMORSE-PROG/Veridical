"""Rubric decomposition (F2.1): raw extracted text -> typed criteria.

One Gemini call per rubric parse (D-011: the cheapest LLM use in the
system, cached by response hash so a re-run costs nothing). This module
owns the prompt and deterministic schema-level validation only; the
retry/coverage/review-fallback loop around a failed parse is V-011's job
— it catches `RubricParseError` from here.
"""

from pathlib import Path

from pydantic import ValidationError

from app.errors import VeridicalError
from app.ingest.schemas import TextBlock
from app.llm.base import LLMClient
from app.rubric.schemas import ParsedCriterion, RubricDecomposition

PROMPT_TYPE = "rubric_decomposition"
# V-069: v3 adds an optional "levels" field so a graded performance scale
# (BEGINNER 1/ACCEPTABLE 2/PROFICIENT 3/EXEMPLARY 4, or any institution's
# own scale) is captured as structured data instead of being squashed into
# "evidence_needed" prose (v1's prompt already told the model never to
# collapse a scale — v1/v2 just had nowhere structured to put it). BUG-092:
# v2 adds a third "not_assessable" type for defense-day/physical
# requirements no document could ever settle (v1 only knew
# structural/semantic, so these had nowhere to go but AI grading).
PROMPT_VERSION = "v3"
_PROMPT_FILE = Path(__file__).parent / "prompts" / f"{PROMPT_TYPE}_{PROMPT_VERSION}.txt"
_PLACEHOLDER = "{rubric_text}"


class RubricParseError(VeridicalError):
    """The model's structured output didn't validate against the
    criteria contract (V-011 catches this to drive the retry loop)."""

    code = "parse_failed"


def raw_text_for_decomposition(blocks: list[TextBlock]) -> str:
    """Furniture-stripped, reading-order text — the same shredded-table
    reality every rubric PDF/DOCX arrives in (V-010 research: no table
    structure survives generic extraction, the prompt must cope, not a
    preprocessing hack)."""
    lines = [b.text.strip() for b in blocks if not b.is_furniture and b.text.strip()]
    return "\n".join(lines)


def _normalize_weights(criteria: list[ParsedCriterion]) -> list[ParsedCriterion]:
    """Weights become a percentage split (sum to 100) regardless of the
    scale the source document or the model used — ENGINEERING §5's
    composite score assumes weights are comparable across criteria."""
    total = sum(c.weight for c in criteria)
    for c in criteria:
        c.weight = round(c.weight / total * 100, 3)
    return criteria


async def decompose_rubric(
    raw_text: str, llm: LLMClient, *, feedback: str | None = None
) -> list[ParsedCriterion]:
    """Format-agnostic by construction: the prompt carries no assumption
    about section names, numbering, or scoring style (charter rule 7 at
    maximum stakes — this is the product's whole thesis).

    `feedback`: V-011's retry loop passes the previous attempt's failure
    reasons here — the prompt text changes, so the response cache (D-011)
    correctly treats a retry as a fresh call rather than replaying the
    same bad answer.
    """
    # Plain substitution, not str.format(): the prompt's own JSON example
    # is full of literal `{`/`}` that .format() would try to parse as
    # fields.
    template = _PROMPT_FILE.read_text(encoding="utf-8")
    prompt = template.replace(_PLACEHOLDER, raw_text)
    if feedback:
        prompt = (
            f"{prompt}\n\n--- YOUR PREVIOUS ATTEMPT FAILED VALIDATION ---\n"
            f"{feedback}\nFix this and answer again with ONLY the JSON object."
        )

    response = await llm.complete(PROMPT_TYPE, prompt, prompt_version=PROMPT_VERSION)
    try:
        decomposition = RubricDecomposition.model_validate(response)
    except ValidationError as exc:
        raise RubricParseError(f"Rubric decomposition response failed validation: {exc}") from exc
    return _normalize_weights(decomposition.criteria)
