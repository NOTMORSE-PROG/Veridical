"""Rubric decomposition (F2.1): raw extracted text -> typed criteria.

One Gemini call per rubric parse (D-011: the cheapest LLM use in the
system, cached by response hash so a re-run costs nothing). This module
owns the prompt and deterministic schema-level validation only; the
retry/coverage/review-fallback loop around a failed parse is V-011's job;
it catches `RubricParseError` from here.
"""

import re
from pathlib import Path

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.errors import VeridicalError
from app.ingest.schemas import TextBlock
from app.llm.base import LLMClient
from app.rubric.schemas import ParsedCriterion, RubricDecomposition

PROMPT_TYPE = "rubric_decomposition"
# V-069: v3 adds an optional "levels" field so a graded performance scale
# (BEGINNER 1/ACCEPTABLE 2/PROFICIENT 3/EXEMPLARY 4, or any institution's
# own scale) is captured as structured data instead of being squashed into
# "evidence_needed" prose (v1's prompt already told the model never to
# collapse a scale; v1/v2 just had nowhere structured to put it). BUG-092:
# v2 adds a third "not_assessable" type for defense-day/physical
# requirements no document could ever settle (v1 only knew
# structural/semantic, so these had nowhere to go but AI grading). BUG-391:
# v4 asks for whole-word level names without the point value, and for every
# rung a shared scale header grades (TIP's wrapped "ACCEPTABL / E 2" header
# came back verbatim in every name, and one row lost its top rung), and
# states the one-criterion-per-requirement rule (a draft without it folded
# MTSU's 44-item checklist into 7 criteria).
# v5 preserves independently graded source rows, distinguishes quality from
# counts using the full scale, and refuses requirements inferred from purpose.
# BUG-566: v6 reads recovered tables cell by cell and asks for every level
# descriptor to be one cell's whole text, copied word for word (flattened
# reading order had moved short cells into the neighbouring row).
PROMPT_VERSION = "v6"
_PROMPT_FILE = Path(__file__).parent / "prompts" / f"{PROMPT_TYPE}_{PROMPT_VERSION}.txt"
_PLACEHOLDER = "{rubric_text}"
_MARKER_PLACEHOLDER = "{withheld_marker}"


class RubricParseError(VeridicalError):
    """The model's structured output didn't validate against the
    criteria contract (V-011 catches this to drive the retry loop)."""

    code = "parse_failed"


def raw_text_for_decomposition(blocks: list[TextBlock]) -> str:
    """Furniture-stripped, reading-order text: the same shredded-table
    reality every rubric PDF/DOCX arrives in (V-010 research: no table
    structure survives generic extraction, the prompt must cope, not a
    preprocessing hack)."""
    lines = [b.text.strip() for b in blocks if not b.is_furniture and b.text.strip()]
    return "\n".join(lines)


_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
_SPACED_TYPO_DASH_RE = re.compile(r"\s+[\u2013\u2014]\s+")
_NUMERIC_RANGE_EN_DASH_RE = re.compile(r"(?<=\d)\u2013(?=\d)")
_REMAINING_TYPO_DASH_RE = re.compile(r"[\u2013\u2014]")


def _without_typographic_dashes(value: str) -> str:
    """BUG-333: normalize em/en dashes in VERIDICAL-drafted criterion fields
    before they reach the instructor review screen."""
    value = _SPACED_TYPO_DASH_RE.sub(": ", value)
    value = _NUMERIC_RANGE_EN_DASH_RE.sub("-", value)
    return _REMAINING_TYPO_DASH_RE.sub(", ", value)


def _name_without_points(name: str, points: float) -> str:
    tokens = name.split()
    if len(tokens) < 2:
        return name
    for index in (-1, 0):
        token = tokens[index].strip("()[]")
        if _NUMBER.match(token) and float(token) == points:
            # "4 - Exemplary" keeps no stray separator (backend review, 2026-10-10).
            rest = " ".join(tokens[:-1] if index == -1 else tokens[1:])
            return rest.strip(" -\u2013\u2014:|") or name
    return name


def _without_point_values(criterion: ParsedCriterion) -> ParsedCriterion:
    """BUG-391: a wrapped scale header ("ACCEPTABL E 2") came back with each
    rung's point value inside its name. The value is already `points`; it
    leaves the name only when every rung's name still has letters and still
    differs from the others ("Level 1 / Level 2" keeps its numbers)."""
    updates: dict[str, object] = {
        "text": _without_typographic_dashes(criterion.text),
    }
    if criterion.evidence_needed is not None:
        updates["evidence_needed"] = _without_typographic_dashes(criterion.evidence_needed)
    if not criterion.levels:
        return criterion.model_copy(update=updates)
    cleaned_levels = [
        level.model_copy(
            update={
                "name": _without_typographic_dashes(level.name),
                # Provenance is the source check's to record, not the model's.
                "source": None,
                "descriptor": (
                    _without_typographic_dashes(level.descriptor)
                    if level.descriptor is not None
                    else None
                ),
            }
        )
        for level in criterion.levels
    ]
    names = [_name_without_points(level.name, level.points) for level in cleaned_levels]
    distinct = len({name.casefold() for name in names}) == len(names)
    if distinct and all(any(ch.isalpha() for ch in name) for name in names):
        cleaned_levels = [
            level.model_copy(update={"name": name})
            for level, name in zip(cleaned_levels, names, strict=True)
        ]
    updates["levels"] = cleaned_levels
    return criterion.model_copy(update=updates)


def relative_weights(weights: list[float], max_decimals: int) -> list[float]:
    """Weights as the instructor can read them. Scoring divides by the
    weights' total, so only their ratios matter. Weights the format states
    plainly (20, 30, 50; or 1, 1, 2) are kept as they are; any with more
    decimals than `max_decimals` (an equal split written as 11.111) are
    rescaled so the lightest is 1, which keeps their ratios."""

    def plain(weight: float) -> bool:
        return abs(weight - round(weight, max_decimals)) < 1e-9

    if all(plain(weight) for weight in weights):
        return list(weights)
    lightest = min(weights)
    return [round(weight / lightest, max_decimals) for weight in weights]


def _relative_weights(criteria: list[ParsedCriterion], settings: Settings) -> list[ParsedCriterion]:
    weights = relative_weights([c.weight for c in criteria], settings.rubric_weight_max_decimals)
    for criterion, weight in zip(criteria, weights, strict=True):
        criterion.weight = weight
    return criteria


async def decompose_rubric(
    raw_text: str,
    llm: LLMClient,
    *,
    feedback: str | None = None,
    settings: Settings | None = None,
) -> list[ParsedCriterion]:
    """Format-agnostic by construction: the prompt carries no assumption
    about section names, numbering, or scoring style (charter rule 7 at
    maximum stakes: this is the product's whole thesis).

    `feedback`: V-011's retry loop passes the previous attempt's failure
    reasons here; the prompt text changes, so the response cache (D-011)
    correctly treats a retry as a fresh call rather than replaying the
    same bad answer.
    """
    # Plain substitution, not str.format(): the prompt's own JSON example
    # is full of literal `{`/`}` that .format() would try to parse as
    # fields.
    settings = settings or get_settings()
    template = _PROMPT_FILE.read_text(encoding="utf-8").replace(
        _MARKER_PLACEHOLDER, settings.rubric_withheld_value_marker
    )
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
    return _relative_weights([_without_point_values(c) for c in decomposition.criteria], settings)
