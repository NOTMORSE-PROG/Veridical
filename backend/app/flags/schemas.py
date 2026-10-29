"""Flag evidence/annotation/override HTTP contract (F8.2/F8.4, screen 4i)."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.checks.citations.history_review import CitationMetadataReviewWarning
from app.config import get_settings
from app.report.schemas import FlagSummaryOut, ReportOut


class PassagePairOut(BaseModel):
    """V-072 (F7.4): present only on a passage-level reuse flag
    (`FlagOut.passage_pair`) — the two-sided comparison design review's
    spec (2026-08-20) calls `PassagePairPanel`. `matched_excerpt`/
    `matched_context_before`/`matched_context_after` are bounded, stored
    text (`app/models/manuscript.py`'s `ManuscriptPassageArchive.text`/
    `context_text`, computed once at archive-build time) — never a live
    read of the matched manuscript's file (bounded-excerpt rule, carried
    from V-058/BUG-050 Branch B). `matched_ref` is the same opaque,
    non-identifying manuscript id every other F7 flag already uses
    (BUG-050/097) — never a real name or heading."""

    own_excerpt: str
    own_context_before: str | None
    own_context_after: str | None
    matched_ref: int
    matched_excerpt: str
    matched_context_before: str | None
    matched_context_after: str | None
    # Echoes `reuse_passage_context_words` (config, not hardcoded on the
    # frontend) — the bounded-window disclosure ticket AC8 requires
    # wherever a match is shown.
    context_words_each_side: int
    similarity: float
    # None for a supporting excerpt below standalone passage thresholds or
    # historical support whose classification was not stored.
    level: str | None


class CitationSourceKeyOut(BaseModel):
    """BUG-078: what `confirm_citation_source` would mark, if the
    instructor confirms this flag's source -- surfaced so the frontend can
    show a title-keyed match's collision risk distinctly (design review
    spec, 2026-08-24: a title match isn't a unique identifier the way a
    DOI/ISBN is, and two distinct papers can share a normalized title)."""

    kind: str  # "doi" | "isbn" | "title"
    value: str


class CitationSourceLinkOut(BaseModel):
    kind: Literal["doi_as_cited", "url_as_cited", "doi_recorded_lookup"]
    url: HttpUrl


class CitationSourceLocatorOut(BaseModel):
    basis: Literal["saved_reference", "recorded_source_key"]
    order_index: int | None = Field(strict=True, ge=0)
    links: list[CitationSourceLinkOut] = Field(min_length=1)

    @model_validator(mode="after")
    def coherent_basis(self):
        if self.basis == "recorded_source_key":
            if self.order_index is not None or any(
                link.kind != "doi_recorded_lookup" for link in self.links
            ):
                raise ValueError(
                    "A recorded lookup has no inferred reference index or as-cited links"
                )
        elif self.order_index is None or any(
            link.kind == "doi_recorded_lookup" for link in self.links
        ):
            raise ValueError("An as-cited reference needs its saved index and as-cited links")
        return self


class PValueComparisonOut(BaseModel):
    """Saved calculation display only; never expose arbitrary internal flag detail."""

    reported: str
    recalculated: str


class AgreementPairOut(BaseModel):
    """Only the two stored candidate statements and their recorded locations."""

    intent_text: str
    intent_anchor: str | None
    outcome_text: str
    outcome_anchor: str | None


class CitationMetadataValuesOut(BaseModel):
    model_config = ConfigDict(strict=True)
    doi: str | None = None
    title: str | None = None
    year: int | None = None
    first_author: str | None = None


class CitationMetadataComparisonOut(BaseModel):
    """Only saved inputs/provenance; never a live cache or registry reconstruction."""

    schema_version: Literal["doi-metadata-v1"]
    provider: str
    cited: CitationMetadataValuesOut
    registered: CitationMetadataValuesOut
    differing_fields: list[str]
    title_status: str
    unassessed_reason: str | None = None
    policy_version: str | None = None
    policy_sha256: str | None = None
    doi_source_url: HttpUrl | None = None
    provider_record_url: HttpUrl | None = None


class FlagOut(BaseModel):
    id: int
    assessment_revision: int | None = None
    check_result_id: int
    check_run_id: int
    manuscript_group_label: str
    check_kind: str
    # None for integrity checks (F4-F7) — they aren't tied to a rubric
    # criterion (check_result.criterion_id is nullable, ENGINEERING §2).
    criterion_text: str | None
    severity: str
    # Agreement score from self-consistency voting (D-006), when the
    # underlying check used it — None for deterministic/rule-based checks.
    confidence: float | None
    evidence_excerpt: str
    page_anchor: str
    annotation: str | None
    overridden: bool
    affects_readiness: bool = True
    readiness_exclusion_reason: str | None = None
    override_reason: str | None
    # What the check itself concluded — from flag.detail when present
    # (F5/F6, V-033: many flags share one check_result), else
    # check_result.detail (semantic grading, V-020: one flag per
    # check_result) — shown alongside the override so both are visible
    # (ticket AC: "AI said X · instructor overrode to Y").
    ai_verdict_summary: str | None
    ai_reasoning: str | None
    p_value_comparison: PValueComparisonOut | None = None
    agreement_pair: AgreementPairOut | None = None
    citation_metadata_comparison: CitationMetadataComparisonOut | None = None
    citation_source_locator: CitationSourceLocatorOut | None = None
    citation_metadata_review_warning: CitationMetadataReviewWarning | None = None
    # BUG-049: "fake" (fixture data — e.g. a vision-pass table that was
    # never in the manuscript), "real", or "unknown" (predates migration
    # 0024). The flag evidence page is exactly where the audit found a
    # fabricated statistical-forensics finding rendered with no disclosure
    # at all.
    llm_mode: str
    # BUG-170: mirrors `FlagSummaryOut.is_passage_level` exactly (same
    # `detail["kind"].endswith("_passage")` derivation) -- lets the evidence
    # detail page tell a passage-level flag (evidence_excerpt is real,
    # quoted manuscript text) apart from a whole-document/chapter/
    # resubmission reuse flag (evidence_excerpt is a system-authored
    # template sentence, never a quote) without re-deriving the suffix
    # check from `ai_verdict_summary` a second time in a different file.
    is_passage_level: bool = False
    # V-072 (F7.4): present only for a passage-level reuse flag.
    passage_pair: PassagePairOut | None = None
    # V-077: the matched Library record's number for an F7 finding (the same
    # opaque reference already shown in the finding text and on the report's
    # `FlagSummaryOut.matched_ref`), so the instructor can open the record and
    # verify instead of only agreeing. None for every other check.
    matched_ref: int | None = None
    # BUG-518: the instructor confirmed this finding as a real issue.
    finding_confirmed: bool = False
    finding_confirm_note: str | None = None
    finding_confirmed_at: datetime | None = None
    # BUG-517: the citation a citation-integrity finding is about, as written
    # (in-text citation or reference entry), with its parsed authors/year.
    # None for every other finding and for rows saved before it existed.
    cited_text: str | None = None
    cited_authors: list[str] | None = None
    cited_year: str | None = None
    # BUG-097 (presentation-only remedy, owner ruling 2026-08-24): True only
    # for an F7 originality/reuse flag produced on the account's first-ever
    # manuscript upload — never changes `severity` (see
    # `app.checks.reuse.query.is_first_upload_for_instructor`'s docstring).
    # Drives the report's "this is your first-ever check" banner so a new
    # instructor knows to verify the match with extra care, without the
    # product silently deciding the match is less trustworthy.
    first_upload_context: bool = False
    # BUG-153 (backend review finding, live-reproduced): True only for a
    # whole-document/chapter F7 flag that could not be evidenced with even
    # one real supporting passage -- `checks.reuse.service`'s own
    # fallback clause downgrades such a flag from high to med severity,
    # but the WORDING stays the same templated accusation sentence either
    # way (there is nothing else honest to say). Without this field there
    # was no way, in the API response or the UI, to tell "downgraded
    # because unevidenceable" apart from "genuinely medium-confidence
    # match" -- both looked identical. Never True for a passage-level flag
    # (those either have real evidence or were never scored at all).
    evidence_unavailable: bool = False
    # BUG-078/FEATURES.md §9: present only for a citation flag with a real
    # DOI/ISBN/title to key a confirmation on (null for the "no identifier
    # at all" not_found case, and null for every non-citation-integrity
    # flag) — gates the "Confirm this source" button vs. the "nothing to
    # confirm" explanation on screen 4i.
    citation_source_key: CitationSourceKeyOut | None = None
    # True only when THIS flag was resolved via "Confirm this source"
    # rather than an ordinary override — always False while `overridden`
    # is False. Lets the terminal banner/announcement tell the two paths
    # apart without inferring it from `override_reason`'s text (both are
    # instructor-authored free text).
    confirmed_citation_source: bool = False


class AnnotateFlagIn(BaseModel):
    annotation: str = Field(min_length=1)


class OverrideFlagIn(BaseModel):
    expected_revision: int = Field(strict=True, ge=0)
    reason: str = Field(min_length=1)


class ConfirmCitationSourceIn(BaseModel):
    expected_revision: int = Field(strict=True, ge=0)
    # BUG-078: instructor-authored (design review spec: "Where you verified
    # this"), same required-reason discipline as OverrideFlagIn -- not a
    # fixed/generated string, since this is a MORE consequential action
    # (durable, cross-manuscript) than an ordinary override, not less.
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def _reject_whitespace_only(cls, value: str) -> str:
        # backend review (BUG-078 review), live-reproduced: `min_length=1`
        # alone let a whitespace-only reason (" ") through -- the client
        # strips before submit, but the API is directly reachable, and this
        # action is durable/cross-manuscript with no un-confirm, a HIGHER
        # bar than `OverrideFlagIn`'s (which has the same gap, unfixed
        # here deliberately -- an ordinary override is reversible and
        # single-flag-scoped, not the same stakes).
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason must not be empty or whitespace-only")
        return stripped


class OverrideFlagOut(FlagOut):
    # The recomputed report state (ticket AC: "recomputes score/status
    # immediately") — same pattern as V-023's `ResolveEscalationOut`.
    report: ReportOut


class ConfirmFindingIn(BaseModel):
    expected_revision: int = Field(strict=True, ge=0)
    # BUG-518: agreeing with a finding needs no justification; an optional
    # note is kept for the record. Blank is stored as no note.
    note: str | None = None


class ConfirmFindingOut(FlagOut):
    report: ReportOut


class ConfirmCitationSourceOut(FlagOut):
    # Same shape as `OverrideFlagOut` — confirming a citation source is
    # implemented as a specific kind of override (BUG-078) and recomputes
    # score/status the same way.
    report: ReportOut


# Largest BIGINT identity value; anything above can never name a row.
_MAX_ID = 2**63 - 1


class BulkResolveFlagsIn(BaseModel):
    """V-089: one confirm or dismiss for many findings of one check run.

    Each finding is changed exactly as its single-flag action would change
    it (`confirm-finding`, `override`); the shared note or reason is copied
    to every one of them.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["confirm", "dismiss"]
    # Strict: `true` or "5" must never select a real finding. Ids are BIGINT.
    flag_ids: list[Annotated[int, Field(strict=True, gt=0, le=_MAX_ID)]] = Field(min_length=1)
    expected_revision: int = Field(strict=True, ge=0)
    # Confirm: optional, blank stored as no note (as `ConfirmFindingIn`).
    note: str | None = None
    # Dismiss: required, as `OverrideFlagIn`, and never whitespace only.
    reason: str | None = None

    @field_validator("flag_ids", mode="before")
    @classmethod
    def _bounded_request(cls, value):
        # Checked before each item is parsed. Duplicates are allowed but the
        # list as sent may not be longer than the limit either.
        limit = get_settings().flag_bulk_resolve_max_flags
        if isinstance(value, list) and len(value) > limit:
            raise ValueError(f"select at most {limit} findings at a time")
        return value

    @field_validator("flag_ids")
    @classmethod
    def _unique(cls, value: list[int]) -> list[int]:
        # Duplicates name one finding twice; it is changed and audited once.
        return list(dict.fromkeys(value))

    @field_validator("note", "reason")
    @classmethod
    def _bounded_text(cls, value: str | None) -> str | None:
        # Copied to every finding and audit entry of the batch.
        limit = get_settings().flag_bulk_resolve_max_text_chars
        if value is not None and len(value.strip()) > limit:
            raise ValueError(f"keep the note or reason to {limit} characters")
        return value

    @model_validator(mode="after")
    def _text_matches_action(self):
        if self.action == "dismiss":
            if self.note is not None:
                raise ValueError("a dismissal takes a reason, not a note")
            if self.reason is None or not self.reason.strip():
                raise ValueError("a reason is required to dismiss findings")
        elif self.reason is not None:
            raise ValueError("a confirmation takes an optional note, not a reason")
        return self


class BulkResolveFlagsOut(BaseModel):
    # The changed findings as the report's flags list shows them.
    flags: list[FlagSummaryOut]
    report: ReportOut
    # Carried by every audit entry this action wrote.
    batch_id: str
