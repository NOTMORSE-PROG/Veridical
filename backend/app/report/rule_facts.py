"""F3.2/F8: allowlisted observations from saved structural results, without I/O."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from app.checks.rules.presence import PRESENCE_SCOPE_VERSION, presence_input_digest


class _Facts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SavedBound(_Facts):
    kind: Literal["min", "max", "exact", "range"]
    low: float = Field(ge=0, allow_inf_nan=False)
    high: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def complete(self):
        if self.kind == "range":
            if self.high is None or self.high < self.low:
                raise ValueError("The recorded range is incomplete.")
        elif self.high is not None:
            raise ValueError("A non-range bound has no upper endpoint.")
        return self


class HeadingFacts(_Facts):
    rule_id: Literal["required_section_present"]
    target: str
    matched_title: str | None = None

    @field_validator("target", "matched_title")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("The saved heading is blank.")
        return value


class OrderFacts(_Facts):
    rule_id: Literal["section_order"]
    checked_sections: list[str] = Field(min_length=2)

    @field_validator("checked_sections")
    @classmethod
    def readable(cls, values):
        if any(not value.strip() for value in values):
            raise ValueError("An ordered heading is blank.")
        return values


class CountFacts(_Facts):
    rule_id: Literal["page_limit", "reference_count_min_max"]
    actual_count: int = Field(ge=0)
    expected: SavedBound

    @model_validator(mode="after")
    def pages_exist(self):
        if self.rule_id == "page_limit" and self.actual_count == 0:
            raise ValueError("A completed page-count observation requires pages.")
        return self


class CaptionFacts(_Facts):
    rule_id: Literal["table_formatting_presence"]
    total_tables: int = Field(gt=0)
    captioned: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent(self):
        if self.captioned > self.total_tables:
            raise ValueError("Caption count exceeds the table inventory.")
        return self


class MarginFacts(_Facts):
    rule_id: Literal["margins_spacing"]
    checked_pages_or_sections: int | None = Field(default=None, gt=0)
    side: Literal["left", "top", "right", "bottom"] | None = None
    actual_pts: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def one_observation(self):
        count = self.checked_pages_or_sections is not None
        measurement = self.side is not None and self.actual_pts is not None
        if count == measurement or (
            count and (self.side is not None or self.actual_pts is not None)
        ):
            raise ValueError("A saved margin observation requires one supported shape.")
        return self


class CitationPatternFacts(_Facts):
    rule_id: Literal["citation_style_sniff"]
    threshold_relation: Literal["met", "below"]


RuleFacts = Annotated[
    HeadingFacts | OrderFacts | CountFacts | CaptionFacts | MarginFacts | CitationPatternFacts,
    Field(discriminator="rule_id"),
]
_ADAPTER = TypeAdapter(RuleFacts)
_RATIO = TypeAdapter(Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)])


def _original_outcome(detail: dict, current: str) -> str | None:
    if detail.get("resolution") is None:
        value = current
    else:
        original = detail.get("original_result")
        value = original.get("outcome") if isinstance(original, dict) else None
    return (
        value
        if isinstance(value, str)
        and value
        in {
            "passed",
            "failed",
            "escalated",
            "unverifiable",
            "not_applicable",
            "api_down",
            "quota_exhausted",
        }
        else None
    )


def project_rule_facts(
    detail: object, *, criterion_type: str, kind: str, outcome: str
) -> RuleFacts | None:
    """Project recorded observations, never use a manual grade as the old rule result."""
    if (
        not isinstance(detail, dict)
        or criterion_type != "structural"
        or kind != "structural"
        or detail.get("basis") is not None
    ):
        return None
    rule = detail.get("rule_id")
    original = _original_outcome(detail, outcome)
    if original in {"unverifiable", "not_applicable", "api_down", "quota_exhausted"}:
        return None
    if original is None and detail.get("resolution") is None:
        return None
    candidate = {"rule_id": rule}
    try:
        if rule == "required_section_present":
            candidate["target"] = detail.get("target")
            if "matched_title" in detail:
                if not isinstance(detail["matched_title"], str):
                    return None
                candidate["matched_title"] = detail["matched_title"]
                if original is not None and original != "passed":
                    return None
            elif original == "passed":
                return None
        elif original == "escalated":
            return None
        elif rule == "section_order":
            if original == "failed":
                return None
            candidate["checked_sections"] = detail.get("checked_sections")
        elif rule in {"page_limit", "reference_count_min_max"}:
            candidate["actual_count"] = detail.get(
                "actual_pages" if rule == "page_limit" else "actual_count"
            )
            expected = detail.get("expected")
            candidate["expected"] = (
                {key: expected[key] for key in ("kind", "low", "high") if key in expected}
                if isinstance(expected, dict)
                else None
            )
        elif rule == "table_formatting_presence":
            candidate.update(
                total_tables=detail.get("total_tables"), captioned=detail.get("captioned")
            )
        elif rule == "margins_spacing":
            candidate.update(
                {
                    key: detail[key]
                    for key in ("checked_pages_or_sections", "side", "actual_pts")
                    if key in detail
                }
            )
            if (
                original == "passed"
                and "side" in candidate
                or original == "failed"
                and "checked_pages_or_sections" in candidate
            ):
                return None
        elif rule == "citation_style_sniff":
            ratio = _RATIO.validate_python(detail.get("matched_ratio"))
            threshold = _RATIO.validate_python(detail.get("threshold"))
            met = ratio >= threshold
            if original is not None and (original == "passed") != met:
                return None
            candidate["threshold_relation"] = "met" if met else "below"
        else:
            return None
        projected = _ADAPTER.validate_python(candidate)
        if isinstance(projected, CountFacts) and original in {"passed", "failed"}:
            bound = projected.expected
            actual = projected.actual_count
            within = (
                actual >= bound.low
                if bound.kind == "min"
                else actual <= bound.low
                if bound.kind == "max"
                else actual == bound.low
                if bound.kind == "exact"
                else bound.low <= actual <= bound.high
            )
            if within != (original == "passed"):
                return None
        return projected
    except (ValidationError, TypeError, ValueError):
        return None


@lru_cache
def rule_facts_copy() -> dict[str, str]:
    return json.loads((Path(__file__).parent / "data" / "rule_facts_copy.json").read_text("utf-8"))


def rule_facts_presentation(facts: RuleFacts) -> tuple[list[str], list[str]]:
    """Shared print/screen template contract; all interpolations remain plain text."""
    copy = rule_facts_copy()

    def number(value):
        return str(value).removesuffix(".0")

    if isinstance(facts, HeadingFacts):
        return [
            copy["heading_match"].format(matched_title=facts.matched_title, target=facts.target)
            if facts.matched_title is not None
            else copy["heading_search"].format(target=facts.target),
            copy["heading_scope"],
        ], []
    if isinstance(facts, OrderFacts):
        return [copy["order"], copy["order_scope"]], facts.checked_sections
    if isinstance(facts, CountFacts):
        bound = copy["bound_" + facts.expected.kind].format(
            low=number(facts.expected.low), high=number(facts.expected.high)
        )
        family = "pages" if facts.rule_id == "page_limit" else "references"
        lines = [
            copy[family].format(count=facts.actual_count),
            copy[family + "_bound"].format(bound=bound),
        ]
        if family == "references":
            lines.append(copy["references_scope"])
        return lines, []
    if isinstance(facts, CaptionFacts):
        return [
            copy["captions"].format(captioned=facts.captioned, total_tables=facts.total_tables),
            copy["captions_scope"],
        ], []
    if isinstance(facts, MarginFacts):
        return (
            (
                [
                    copy["margins_count"].format(count=facts.checked_pages_or_sections),
                    copy["margins_scope"],
                ],
                [],
            )
            if facts.checked_pages_or_sections is not None
            else (
                [
                    copy["margins_measurement"].format(
                        side=facts.side, actual_pts=number(facts.actual_pts)
                    )
                ],
                [],
            )
        )
    return [
        copy["citation_pattern"],
        copy["citation_" + facts.threshold_relation],
        copy["citation_scope"],
    ], []


def project_rule_scope_unconfirmed(detail: object, *, criterion, kind: str, outcome: str) -> bool:
    """Read saved capability provenance; never run today's grammar on an old grade."""
    facts = project_rule_facts(
        detail, criterion_type=criterion.type.value, kind=kind, outcome=outcome
    )
    if not isinstance(facts, HeadingFacts):
        return False
    scope = detail.get("section_presence_scope")
    return not (
        isinstance(scope, dict)
        and scope.get("version") == PRESENCE_SCOPE_VERSION
        and scope.get("rule_id") == facts.rule_id
        and scope.get("input_digest") == presence_input_digest(criterion)
    )
