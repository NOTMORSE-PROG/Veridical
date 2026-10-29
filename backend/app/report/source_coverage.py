"""F3/F8: safe recorded input-coverage facts, never reconstructed evidence."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_serializer,
    model_validator,
)

SourcePosition = Annotated[int, Field(strict=True, ge=0)]
SourcePage = Annotated[int, Field(strict=True, ge=1)]


class CriterionSourceLocationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paragraph: SourcePosition | None
    kind: Literal["paragraph", "table_cell"]
    n_occurrences: int = Field(strict=True, ge=1)


class CriterionSourceCoverageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    native_math_coverage_version: Literal["v1"] | None = None
    native_math_inventory_state: Literal[
        "complete", "not_recorded", "unavailable", "not_applicable"
    ]
    native_math_inventory_scope: Literal["main_document_body"] | None = None
    n_native_equations_unassessed: int | None = Field(default=None, strict=True, ge=0)
    n_native_equations_unlocated: int | None = Field(default=None, strict=True, ge=0)
    native_math_excluded_paragraphs: list[SourcePosition] = Field(default_factory=list)
    native_math_coverage_items: list[CriterionSourceLocationOut] = Field(default_factory=list)
    # PDF pages left out of the assessment because their reading order
    # could not be established; the rest of the manuscript was assessed.
    unread_pages: list[SourcePage] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def omit_unread_pages_when_none(self, handler):
        # Only a PDF assessed around unreadable pages carries the field, so
        # earlier saved shapes and Word sources serialize exactly as before.
        data = handler(self)
        if not data.get("unread_pages"):
            data.pop("unread_pages", None)
        return data

    @model_validator(mode="after")
    def recorded_facts_agree(self):
        locations = self.native_math_coverage_items
        excluded = self.native_math_excluded_paragraphs
        if self.native_math_inventory_state == "complete":
            if (
                self.native_math_coverage_version != "v1"
                or self.native_math_inventory_scope != "main_document_body"
                or self.n_native_equations_unassessed is None
                or self.n_native_equations_unlocated is None
                or self.n_native_equations_unassessed
                != sum(item.n_occurrences for item in locations)
                or self.n_native_equations_unlocated
                != sum(item.n_occurrences for item in locations if item.paragraph is None)
                or excluded
                != sorted({item.paragraph for item in locations if item.paragraph is not None})
            ):
                raise ValueError("Recorded native source coverage facts disagree")
        elif (
            self.native_math_inventory_scope is not None
            or locations
            or excluded
            or self.n_native_equations_unlocated is not None
            or self.n_native_equations_unassessed
            != (0 if self.native_math_inventory_state == "not_applicable" else None)
        ):
            raise ValueError("Unknown source coverage cannot claim measured locations or counts")
        return self


def project_source_coverage(value: object) -> CriterionSourceCoverageOut:
    if value is None:
        return CriterionSourceCoverageOut(native_math_inventory_state="not_recorded")
    if not isinstance(value, dict):
        return CriterionSourceCoverageOut(native_math_inventory_state="unavailable")
    if value.get("native_math_coverage_version") != "v1":
        return CriterionSourceCoverageOut(native_math_inventory_state="unavailable")
    safe = {name: value[name] for name in CriterionSourceCoverageOut.model_fields if name in value}
    locations = safe.get("native_math_coverage_items")
    if isinstance(locations, list):
        safe["native_math_coverage_items"] = [
            {name: item[name] for name in CriterionSourceLocationOut.model_fields if name in item}
            if isinstance(item, dict)
            else item
            for item in locations
        ]
    try:
        return CriterionSourceCoverageOut.model_validate(safe)
    except (ValidationError, TypeError, ValueError):
        return CriterionSourceCoverageOut(native_math_inventory_state="unavailable")
