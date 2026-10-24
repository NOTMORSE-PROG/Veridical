"""F7 Library passage preparation facts, with fail-closed historical projection."""

from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.checks.reuse.embed import LibraryPassageEmbeddings


class PassageIndexingOut(BaseModel):
    state: Literal["complete", "partial", "unavailable", "not_recorded"] = "not_recorded"
    passages_found: int | None = None
    passages_indexed: int | None = None


class LibraryPassageIndexingSummary(BaseModel):
    version: Literal["v1"]
    partial: bool = Field(strict=True)
    unavailable: bool = Field(strict=True)
    not_recorded: bool = Field(strict=True)


def project_indexing_summary(value: object) -> LibraryPassageIndexingSummary | None:
    try:
        return LibraryPassageIndexingSummary.model_validate(value)
    except ValidationError:
        return None


def indexing_scope_sentences(summary: LibraryPassageIndexingSummary | None) -> list[str]:
    if summary is None:
        return ["Passage coverage for this comparison was not recorded."]
    sentences = []
    if summary.partial:
        sentences.append(
            "Passage matching was limited for some Library records available when this "
            "comparison began. Other extracted passages were not available for individual matching."
        )
    if summary.unavailable:
        sentences.append(
            "Some Library records available when this comparison began had no usable "
            "passage preparation for individual matching."
        )
    if summary.not_recorded:
        sentences.append(
            "Passage coverage was not recorded for some Library records available when this "
            "comparison began. Do not assume that every extracted passage was searchable."
        )
    if sentences:
        sentences.append(
            "Full-document and section comparisons have separate evidence. "
            "A missing match does not establish originality."
        )
    return sentences


class PassageIndexingSnapshot(BaseModel):
    version: Literal["v1"]
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_id: str = Field(min_length=1)
    limit: int = Field(strict=True, ge=0)
    inventory_state: Literal["measured", "unavailable"]
    passages_found: int | None = Field(strict=True, ge=0)
    passages_indexed: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def consistent_inventory(self):
        if self.inventory_state == "unavailable":
            if self.passages_found is not None or self.passages_indexed != 0:
                raise ValueError("Unavailable inventory has no measured total or indexed passages")
        elif self.passages_found is None or self.passages_indexed != min(
            self.passages_found, self.limit
        ):
            raise ValueError("Measured indexing must agree with its captured prefix limit")
        return self


def indexing_snapshot(
    inventory: LibraryPassageEmbeddings, source_sha256: str | None, model_id: str
) -> dict | None:
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(char not in "abcdef0123456789" for char in source_sha256)
    ):
        # Re-activating legacy data must retain unknown source binding.
        return None
    return PassageIndexingSnapshot(
        version="v1",
        source_sha256=source_sha256,
        model_id=model_id,
        limit=inventory.limit,
        inventory_state=inventory.inventory_state,
        passages_found=inventory.passages_found,
        passages_indexed=len(inventory.passages),
    ).model_dump(mode="json")


def project_passage_indexing(
    value: object, source_sha256: str | None, archived_model_id: str | None
) -> PassageIndexingOut:
    """Never infer old preparation from today's cap, model or archive count."""
    try:
        saved = PassageIndexingSnapshot.model_validate(value)
    except ValidationError:
        return PassageIndexingOut()
    if saved.source_sha256 != source_sha256 or saved.model_id != archived_model_id:
        return PassageIndexingOut()
    if saved.inventory_state == "unavailable":
        return PassageIndexingOut(state="unavailable")
    return PassageIndexingOut(
        state="partial" if saved.passages_indexed < saved.passages_found else "complete",
        passages_found=saved.passages_found,
        passages_indexed=saved.passages_indexed,
    )
