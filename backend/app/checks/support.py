"""F3 evidence-support veto. A second review can withhold, never invent, a grade."""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.checks.levels import match_level
from app.errors import ApiDownError
from app.models.enums import ResultOutcome

PROMPT_TYPE = "semantic_support"
PROMPT_VERSION = "v3"
SOURCE_SCOPE_POLICY_VERSION = "source_obligations_v5_owned_quotes"
_PROMPT = Path(__file__).parent / "prompts" / f"{PROMPT_TYPE}_{PROMPT_VERSION}.txt"
SupportStatus = Literal["supported", "unsupported", "uncertain"]


class RequirementSupport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str = Field(min_length=1)
    status: SupportStatus
    evidence_quotes: list[str]


class SupportItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    index: int = Field(ge=0)
    status: SupportStatus
    reason: str = Field(min_length=1)
    requirements: list[RequirementSupport] = Field(min_length=1)


def context_limitation(batch: Any, settings: Any) -> dict[str, Any] | None:
    if getattr(batch, "source_scopes", None) == ():
        return {
            "basis": "source-coverage",
            "reason": (
                "The retained source does not establish safe readable input for this criterion. "
                "Native Word equation coverage is unavailable or affected containers "
                "were excluded. "
                "Review the original manuscript; no rubric rating was assigned."
            ),
            "source_coverage": getattr(batch, "source_coverage", None),
        }
    if len(batch.context_text) <= settings.semantic_context_max_chars:
        return None
    return {
        "basis": "source-coverage",
        "reason": (
            "The available manuscript text exceeds the supported semantic-review context. "
            "No text was silently omitted and no rubric rating was assigned. "
            "Review the criterion directly in the full manuscript."
        ),
        "context_chars": len(batch.context_text),
        "context_limit_chars": settings.semantic_context_max_chars,
    }


def _incomplete(reason: str) -> dict[str, Any]:
    return {"status": "incomplete", "reason": reason, "prompt_version": PROMPT_VERSION}


@lru_cache
def _scope_patterns(path):
    defaults = Path(__file__).parent / "data" / "semantic_evidence_scopes.json"
    data = {
        **json.loads(defaults.read_text(encoding="utf-8")),
        **(json.loads(Path(path).read_text(encoding="utf-8")) if path is not None else {}),
    }
    return {
        key: [re.compile(pattern, re.I) for pattern in patterns] for key, patterns in data.items()
    }


def _capability_failure(batch, criterion, detail):
    patterns = _scope_patterns(getattr(batch, "scope_patterns_file", None))
    obligations = {"criterion": criterion.text}
    if getattr(criterion, "evidence", None):
        obligations["evidence_request"] = criterion.evidence
    level = match_level(criterion, detail["verdict"])
    if level is not None:
        obligations["level_descriptor"] = next(
            item["descriptor"] for item in criterion.levels if int(item["level"]) == level.ordinal
        )
    guarded = [
        {"key": key, "capabilities": needed}
        for key, text in obligations.items()
        if (
            needed := [
                capability
                for capability in (
                    "external_verification_required",
                    "collection_assessment_required",
                )
                if any(pattern.search(" ".join(text.split())) for pattern in patterns[capability])
            ]
        )
    ]
    if not guarded:
        return None
    missing = sorted({item for obligation in guarded for item in obligation["capabilities"]})
    requirements = []
    if "external_verification_required" in missing:
        requirements.append("independent verification of external properties")
    if "collection_assessment_required" in missing:
        requirements.append("an itemized assessment of the relevant complete collection")
    return {
        "status": "unsupported",
        "reason": (
            "The proposed rating requires " + " and ".join(requirements) + ". "
            "The saved manuscript quotations do not provide these assessment records. "
            "Review the criterion and its performance level directly; no automatic grade "
            "was assigned."
        ),
        "scope_guard": "independent_assessment_required",
        "missing_capabilities": missing,
        "guarded_obligations": guarded,
        "prompt_version": PROMPT_VERSION,
        "source_scope": batch.label,
        "source_scope_policy": SOURCE_SCOPE_POLICY_VERSION,
    }


def _scope_failure(batch, criterion, detail):
    path = getattr(batch, "scope_patterns_file", None)
    if path is None:
        return None
    patterns = _scope_patterns(path)
    quotes = [" ".join(item["quote"].split()).casefold() for item in detail.get("evidence", [])]
    if quotes and all(
        any(pattern.fullmatch(quote) for pattern in patterns["non_substantive_quotes"])
        for quote in quotes
    ):
        return (
            "The submitted evidence consists only of a source identifier. An identifier can "
            "locate a source, but does not demonstrate this semantic criterion or performance "
            "level. Review substantive manuscript evidence before assigning a level."
        )
    requirements = criterion.text + "\n" + (getattr(criterion, "evidence", None) or "")
    level = match_level(criterion, detail["verdict"])
    if level is not None:
        requirements += "\n" + next(
            item["descriptor"] for item in criterion.levels if int(item["level"]) == level.ordinal
        )
    if not any(pattern.search(requirements) for pattern in patterns["body_required"]):
        return None
    body, references = (
        getattr(batch, "body_context", None),
        getattr(batch, "reference_context", None),
    )
    if body is None or references is None:
        return "Source boundaries do not establish the body evidence required by this criterion."

    def normalize(value):
        return " ".join(value.split()).casefold()

    body, references = normalize(body), normalize(references)
    # A necessary provenance condition, not a relevance or similarity score:
    # explicit in-text obligations require a submitted body passage. Ambiguous
    # passages appearing in both scopes cannot establish that distinction.
    if not any(quote and quote in body and quote not in references for quote in quotes):
        return (
            "The criterion mentions in-text documentation, but its submitted quotes do not "
            "establish distinct manuscript-body evidence. Review the criterion's intended "
            "scope and manuscript body before assigning a level."
        )
    return None


async def check_support(
    batch: Any,
    candidates: list[tuple[Any, dict[str, Any]]],
    llm: Any,
    *,
    check_run_id: int | None,
) -> dict[int, dict[str, Any]]:
    """One bounded call per batch; missing/ambiguous items fail closed individually.

    Provider/quota exceptions propagate unchanged so the pipeline retains its
    availability/recovery policy. The grader's explanation is deliberately absent.
    Context and task data enter the response-cache key through the full prompt.
    """
    if not candidates:
        return {}
    items = []
    obligations = []
    for index, (criterion, detail) in enumerate(candidates):
        required = {"criterion": criterion.text}
        if getattr(criterion, "evidence", None):
            required["evidence_request"] = criterion.evidence
        matched = match_level(criterion, detail["verdict"])
        if matched is not None:
            required["level_descriptor"] = next(
                level["descriptor"]
                for level in criterion.levels
                if int(level["level"]) == matched.ordinal
            )
        obligations.append(required)
        items.append(
            {
                "index": index,
                "criterion": criterion.text,
                "evidence_needed": getattr(criterion, "evidence", None),
                "performance_levels": getattr(criterion, "levels", None),
                "proposed_verdict": detail["verdict"],
                "candidate_quotes": [item["quote"] for item in detail.get("evidence", [])],
                "required_obligations": [
                    {"key": key, "text": text} for key, text in required.items()
                ],
            }
        )
    task = {
        "items": items,
        "source_scope": batch.label,
        "source_obligation_policy": SOURCE_SCOPE_POLICY_VERSION,
    }
    # One replacement prevents source strings resembling template slots from
    # being interpolated. JSON escaping keeps supplied data structurally separate.
    prompt = _PROMPT.read_text(encoding="utf-8").replace(
        "{task_json}", json.dumps(task, ensure_ascii=False)
    )
    response = await llm.complete(
        PROMPT_TYPE, prompt, prompt_version=PROMPT_VERSION, check_run_id=check_run_id
    )
    raw_items = response.get("items") if isinstance(response, dict) else None
    if not isinstance(raw_items, list) or any(
        not isinstance(raw, dict)
        or type(raw.get("index")) is not int
        or not 0 <= raw["index"] < len(candidates)
        for raw in raw_items
    ):
        return {
            c.id: _incomplete("The evidence-support response could not be read.")
            for c, _ in candidates
        }
    by_index: dict[int, list[dict]] = {}
    for raw in raw_items:
        if isinstance(raw, dict) and type(raw.get("index")) is int:
            by_index.setdefault(raw["index"], []).append(raw)
    results = {}
    for index, (criterion, detail) in enumerate(candidates):
        matches = by_index.get(index, [])
        failure = "The evidence-support response was missing, ambiguous or incomplete."
        if len(matches) != 1:
            results[criterion.id] = _incomplete(failure)
            continue
        try:
            item = SupportItem.model_validate(matches[0])
            if not item.reason.strip():
                raise ValueError("Blank support explanation")
            keys = [part.key for part in item.requirements]
            if len(keys) != len(set(keys)) or set(keys) != set(obligations[index]):
                raise ValueError("Every supplied obligation must be reviewed exactly once")
            proposed = {e["quote"] for e in detail.get("evidence", [])}
            for part in item.requirements:
                if part.status == "supported" and not part.evidence_quotes:
                    raise ValueError("Support requires candidate evidence")
                if any(
                    not quote.strip() or quote not in proposed for quote in part.evidence_quotes
                ):
                    raise ValueError("Reviewer substituted unsubmitted evidence")
            if item.status == "supported" and any(
                part.status != "supported" for part in item.requirements
            ):
                raise ValueError("Not every requirement supports the proposed rating")
        except (ValidationError, ValueError):
            results[criterion.id] = _incomplete(failure)
            continue
        results[criterion.id] = {
            **item.model_dump(exclude={"index"}),
            "requirements": [
                {**part.model_dump(), "requirement": obligations[index][part.key]}
                for part in item.requirements
            ],
            "prompt_version": PROMPT_VERSION,
            "source_scope": batch.label,
            "source_scope_policy": SOURCE_SCOPE_POLICY_VERSION,
        }
        scope_failure = _scope_failure(batch, criterion, detail)
        capability_failure = _capability_failure(batch, criterion, detail)
        if item.status == "supported" and scope_failure:
            results[criterion.id] = {
                "status": "unsupported",
                "reason": scope_failure,
                "prompt_version": PROMPT_VERSION,
                "source_scope": batch.label,
                "scope_guard": "required_body_evidence_unconfirmed",
                "source_scope_policy": SOURCE_SCOPE_POLICY_VERSION,
            }
        elif item.status == "supported" and capability_failure:
            results[criterion.id] = capability_failure
    return results


async def check_support_available(batch: Any, candidates: list, llm: Any, **context: Any) -> dict:
    """Attribute a failed shared review only to its submitted proposals.

    Quota and cancellation retain the caller's existing cohort/resume policy.
    No additional remote attempts are made here.
    """
    try:
        return await check_support(batch, candidates, llm, **context)
    except ApiDownError:
        return {
            criterion.id: {
                **_incomplete(
                    "The evidence-support service was unavailable. The proposed rating "
                    "remains unassessed; review the manuscript directly."
                ),
                "failure_outcome": ResultOutcome.api_down.value,
                "failure_phase": "semantic_support",
            }
            for criterion, _detail in candidates
        }


def apply_support(
    outcome: ResultOutcome,
    detail: dict[str, Any],
    support: dict[str, Any],
    *,
    decide: bool = False,
) -> tuple[ResultOutcome, dict[str, Any]]:
    """Keep location-verified passages, but remove any withheld accepted rating.

    `decide` (GRADING_DECISION_POLICY): an unconfirmed support check keeps
    the verified-evidence rating and records the check's reason as a
    disclosed caution instead; a support outage is still not a rating."""
    updated = {**detail, "support_check": support}
    if support["status"] == "supported":
        updated["support_basis"] = support["reason"]
        updated["grading_reasoning"] = updated.get("reasoning")
        updated["reasoning"] = support["reason"]
        return outcome, updated
    if (
        decide
        and support.get("failure_outcome") != ResultOutcome.api_down.value
        and outcome in (ResultOutcome.passed, ResultOutcome.failed)
    ):
        updated["support_caution"] = support["reason"]
        return outcome, updated
    if "verdict" in updated:
        updated["proposed_verdict"] = updated.pop("verdict")
    if "level" in updated:
        updated["proposed_level"] = updated.pop("level")
    if "reasoning" in updated:
        updated["proposed_reasoning"] = updated.pop("reasoning")
    updated.pop("score", None)
    updated["reason"] = support["reason"]
    updated["support_withheld"] = True
    if support.get("failure_outcome") == ResultOutcome.api_down.value:
        updated["failure_phase"] = support["failure_phase"]
        return ResultOutcome.api_down, updated
    return ResultOutcome.escalated, updated
