"""Flag evidence/annotation/override HTTP contract (F8.2/F8.4, screen 4i)."""

from pydantic import BaseModel, Field, field_validator

from app.report.schemas import ReportOut


class PassagePairOut(BaseModel):
    """V-072 (F7.4): present only on a passage-level reuse flag
    (`FlagOut.passage_pair`) — the two-sided comparison `ui-designer`'s
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
    level: str


class CitationSourceKeyOut(BaseModel):
    """BUG-078: what `confirm_citation_source` would mark, if the
    instructor confirms this flag's source -- surfaced so the frontend can
    show a title-keyed match's collision risk distinctly (`ui-designer`
    spec, 2026-08-24: a title match isn't a unique identifier the way a
    DOI/ISBN is, and two distinct papers can share a normalized title)."""

    kind: str  # "doi" | "isbn" | "title"
    value: str


class FlagOut(BaseModel):
    id: int
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
    override_reason: str | None
    # What the check itself concluded — from flag.detail when present
    # (F5/F6, V-033: many flags share one check_result), else
    # check_result.detail (semantic grading, V-020: one flag per
    # check_result) — shown alongside the override so both are visible
    # (ticket AC: "AI said X · instructor overrode to Y").
    ai_verdict_summary: str | None
    ai_reasoning: str | None
    # BUG-049: "fake" (fixture data — e.g. a vision-pass table that was
    # never in the manuscript), "real", or "unknown" (predates migration
    # 0024). The flag evidence page is exactly where the audit found a
    # fabricated statistical-forensics finding rendered with no disclosure
    # at all.
    llm_mode: str
    # V-072 (F7.4): present only for a passage-level reuse flag.
    passage_pair: PassagePairOut | None = None
    # BUG-097 (presentation-only remedy, owner ruling 2026-08-24): True only
    # for an F7 originality/reuse flag produced on the account's first-ever
    # manuscript upload — never changes `severity` (see
    # `app.checks.reuse.query.is_first_upload_for_instructor`'s docstring).
    # Drives the report's "this is your first-ever check" banner so a new
    # instructor knows to verify the match with extra care, without the
    # product silently deciding the match is less trustworthy.
    first_upload_context: bool = False
    # BUG-153 (backend-critic finding, live-reproduced): True only for a
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
    reason: str = Field(min_length=1)


class ConfirmCitationSourceIn(BaseModel):
    # BUG-078: instructor-authored (ui-designer spec: "Where you verified
    # this"), same required-reason discipline as OverrideFlagIn -- not a
    # fixed/generated string, since this is a MORE consequential action
    # (durable, cross-manuscript) than an ordinary override, not less.
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def _reject_whitespace_only(cls, value: str) -> str:
        # backend-critic (BUG-078 review), live-reproduced: `min_length=1`
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


class ConfirmCitationSourceOut(FlagOut):
    # Same shape as `OverrideFlagOut` — confirming a citation source is
    # implemented as a specific kind of override (BUG-078) and recomputes
    # score/status the same way.
    report: ReportOut
