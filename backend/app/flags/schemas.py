"""Flag evidence/annotation/override HTTP contract (F8.2/F8.4, screen 4i)."""

from pydantic import BaseModel, Field

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


class AnnotateFlagIn(BaseModel):
    annotation: str = Field(min_length=1)


class OverrideFlagIn(BaseModel):
    reason: str = Field(min_length=1)


class OverrideFlagOut(FlagOut):
    # The recomputed report state (ticket AC: "recomputes score/status
    # immediately") — same pattern as V-023's `ResolveEscalationOut`.
    report: ReportOut
