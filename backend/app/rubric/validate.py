"""Validation gate + retry loop (F2.2, proposal Fig 3.7).

`decompose.py` makes one Gemini call and validates the response SHAPE
(schema-valid, ≥1 criterion). This module adds a second, content-level
gate (coverage of the source text, no duplicate criteria), retries with
the failure fed back into the prompt, and — after exhausting retries —
NEVER fails the upload: it returns a `needs_review` outcome (possibly a
partial criteria set) for the review screen (V-012), per charter rule 1
(escalate low-confidence results to the instructor, don't guess).
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.injection import InjectionSignal, detect_injection_signal
from app.config import Settings, get_settings
from app.ingest.schemas import TextBlock
from app.llm.base import LLMClient
from app.models.audit import AuditLog
from app.rubric.decompose import RubricParseError, decompose_rubric
from app.rubric.schemas import ParsedCriterion
from app.rubric.source import DecompositionSource
from app.rubric.verbatim import SourceIssue, verify_against_source

# BUG-160: worded as a fact about the document, never an accusation against
# whoever uploaded it (ground rule 3) -- the same language could describe a
# false positive (a rubric that legitimately discusses AI grading/security)
# without being wrong about either case. Unlike BUG-045's sibling message in
# `checks/consistency.py` (which explains why one grading batch's agreement
# can't be trusted), this is about PERSISTENCE: text surviving decomposition
# into a criterion rides into every later grading prompt for every
# manuscript checked against this rubric, not just one batch.
RUBRIC_INJECTION_SUSPECTED_REASON = (
    "This document contains text that appears to address an automated "
    "grading system rather than a human reader. Because parsed criteria "
    "are embedded directly into every later grading prompt, an instruction "
    "here would not affect one check -- it would ride along into every "
    "manuscript graded against this rubric. Please review the parsed "
    "criteria carefully before activating it."
)

# BUG-391 AC1 ("nothing is auto-filled"): a draft accepted only after a retry
# was told what was wrong may have changed the rubric to satisfy the check
# rather than the document (backend review, 2026-10-10, reproduced: a row
# with three real levels came back with an invented fourth, and was accepted).
RETRY_CHANGED_DRAFT_REASON = (
    "An earlier draft of these criteria did not pass VERIDICAL's checks ({issues}). "
    "A later draft did, but it may have added or removed something to do so. "
    "Compare the criteria with the original document before activating them."
)

# BUG-566: text that exists only on a repeated copy is not read (so names
# filled in on each copy never reach the model); a line there could still be
# a requirement the first copy lacks.
UNREAD_COPY_TEXT_REASON = (
    "{count} line(s) of text appear only on a repeated copy of this format and "
    "were not read; compare the criteria with the original document"
)

# BUG-566: without a recovered table, a descriptor can be checked as the
# document's own words but not for which row and column it came from (a
# shifted or swapped level still reads as a passage of the flattened text).
NO_TABLE_LEVELS_REASON = (
    "No table structure could be read from this format, so VERIDICAL checked "
    "each level description's wording but not which row and level it belongs "
    "to; compare the levels with the original document"
)

# Significant words only (skip 1-3 letter function words: "the", "and",
# "for", ...) — a coarse but dependency-free overlap signal; no NLP model
# needed for a coverage HEURISTIC (the real judgment stays with the LLM
# and, on persistent failure, the instructor).
_WORD_RE = re.compile(r"[a-z]{4,}")


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _duplicate_texts(criteria: list[ParsedCriterion]) -> list[str]:
    counts: dict[str, int] = {}
    for c in criteria:
        key = " ".join(sorted(_words(c.text)))
        if key:
            counts[key] = counts.get(key, 0) + 1
    return [key for key, count in counts.items() if count > 1]


def coverage_ratio(
    blocks: list[TextBlock],
    criteria: list[ParsedCriterion],
    *,
    min_line_chars: int,
    word_overlap_ratio: float,
) -> float:
    """Fraction of substantive source lines that show up (by word overlap)
    in SOME criterion's text/evidence — catches an answer that quietly
    ignores half the rubric (ticket edge case). 1.0 when there's no
    substantive text to lose (e.g. a near-empty rubric)."""
    lines = [
        b.text.strip()
        for b in blocks
        if not b.is_furniture and len(b.text.strip()) >= min_line_chars
    ]
    if not lines:
        return 1.0
    haystack: set[str] = set()
    for c in criteria:
        haystack |= _words(c.text)
        if c.evidence_needed:
            haystack |= _words(c.evidence_needed)
        # A rubric grid's level descriptors are source text the draft keeps
        # (prompt v4 asks for them); left out, a faithful grid read as 50%
        # covered (backend review, 2026-10-10, UST evaluation form).
        for level in c.levels or []:
            haystack |= _words(f"{level.name} {level.descriptor or ''}")
    covered = 0
    for line in lines:
        line_words = _words(line)
        if not line_words or len(line_words & haystack) / len(line_words) >= word_overlap_ratio:
            covered += 1
    return covered / len(lines)


def _excerpt(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _scale(criterion: ParsedCriterion) -> tuple[float, ...]:
    return tuple(sorted(level.points for level in criterion.levels or []))


def level_scale_issues(
    criteria: list[ParsedCriterion], settings: Settings | None = None
) -> list[str]:
    """BUG-391: a levelled criterion graded on part of the scale most other
    levelled criteria share (1-3 beside 1-4) has probably lost a rung (TIP's
    "Use of references" came back without Exemplary and was accepted). It is
    named so the retry can restore the rung, or the instructor decides. A
    criterion on a genuinely different scale (0-10 beside 1-4) is left alone."""
    settings = settings or get_settings()
    levelled = [c for c in criteria if c.levels]
    if len(levelled) < 2:
        return []
    counts = Counter(_scale(c) for c in levelled)
    common, sharing = counts.most_common(1)[0]
    if sharing < 2 or sharing * 2 <= len(levelled):
        return []  # no scale shared by most levelled criteria
    common_names = {
        level.name.casefold() for c in levelled if _scale(c) == common for level in c.levels or []
    }
    issues = []
    for criterion in levelled:
        scale = _scale(criterion)
        if scale == common:
            continue
        names = {level.name.casefold() for level in criterion.levels or []}
        # Both: a rung lost from the shared scale shows in its points AND its
        # names; a criterion on a scale of its own (0/5/10 Poor/Good/
        # Excellent) is left alone (backend review, 2026-10-10).
        if set(scale) < set(common) and names < common_names:
            issues.append(
                f"'{_excerpt(criterion.text, settings.rubric_feedback_criterion_max_chars)}' "
                f"is graded on {len(scale)} levels while "
                f"{sharing} other criteria share a {len(common)}-level scale; a level "
                "may have been left out, compare it with the original document"
            )
    return issues


def folded_requirements(
    criteria: list[ParsedCriterion], raw_text: str, settings: Settings
) -> str | None:
    """A checklist whose separate items came back folded into a few broad
    criteria cannot be checked item by item (2026-10-09: a draft prompt
    turned MTSU's 44-item checklist into 7 criteria). A rubric grid marks
    each level descriptor with a box or glyph; drafted levels are those
    items, so they count too (2026-10-09: the UST evaluation form's 20
    descriptor glyphs read as 18 list items against 5 correct criteria)."""
    pattern = re.compile(settings.rubric_list_item_pattern)
    items = sum(1 for line in raw_text.splitlines() if pattern.match(line))
    if items < settings.rubric_atomicity_min_items:
        return None
    drafted = len(criteria) + sum(len(c.levels or []) for c in criteria)
    if drafted >= settings.rubric_atomicity_min_ratio * items:
        return None
    return (
        f"the source lists {items} separate items but only {len(criteria)} criteria were "
        "drafted; give each separately checkable requirement its own criterion"
    )


@dataclass
class GateResult:
    ok: bool
    coverage_ratio: float
    reasons: list[str]


def run_gate(
    blocks: list[TextBlock],
    criteria: list[ParsedCriterion],
    settings: Settings,
    raw_text: str | None = None,
) -> GateResult:
    reasons: list[str] = []
    dupes = _duplicate_texts(criteria)
    if dupes:
        reasons.append(f"{len(dupes)} duplicate criterion text(s)")
    reasons.extend(level_scale_issues(criteria, settings))
    if raw_text is not None:
        folded = folded_requirements(criteria, raw_text, settings)
        if folded:
            reasons.append(folded)
    ratio = coverage_ratio(
        blocks,
        criteria,
        min_line_chars=settings.rubric_coverage_min_line_chars,
        word_overlap_ratio=settings.rubric_coverage_word_overlap_ratio,
    )
    if ratio < settings.rubric_coverage_min_ratio:
        reasons.append(
            "the parsed criteria reflect too little of the required format's "
            "substantive source text; compare them with the original document"
        )
    return GateResult(ok=not reasons, coverage_ratio=ratio, reasons=reasons)


def _unplaced_levels_note(
    criteria: list[ParsedCriterion], source: DecompositionSource
) -> list[str]:
    """A rubric graded on scales but read without any table: the instructor
    is told what the check could not establish."""
    levelled = sum(1 for criterion in criteria if criterion.levels)
    return [NO_TABLE_LEVELS_REASON] if levelled >= 2 and not source.cells else []


@dataclass
class DecompositionOutcome:
    criteria: list[ParsedCriterion]
    needs_review: bool
    issues: list[str]
    attempts: int
    # One entry per `issues` item, same order: {"id", "code", "criterion"
    # (draft position or None), "level" (ordinal or None), "message"}.
    issue_details: list[dict] = field(default_factory=list)


def _details(messages: list[str], source_issues: list[SourceIssue]) -> list[dict]:
    """Structured form of `messages` for the review screen. Source issues
    carry the criterion and level they are about; every other message is
    about the draft as a whole ("other", shown as written). Every issue
    starts open: only the instructor acknowledges one, and only a recheck of
    edited criteria resolves one."""
    by_message = {issue.message: issue for issue in source_issues}
    details = []
    for number, message in enumerate(messages, start=1):
        issue = by_message.get(message)
        details.append(
            {
                "id": number,
                "code": issue.code if issue else "other",
                "criterion": issue.criterion if issue else None,
                "level": issue.level if issue else None,
                "message": message,
                "status": "open",
                "resolution": None,
            }
        )
    return details


async def attempt_decomposition(
    session: AsyncSession,
    rubric_id: int,
    blocks: list[TextBlock],
    raw_text: str,
    llm: LLMClient,
    settings: Settings,
    *,
    source: DecompositionSource | None = None,
) -> DecompositionOutcome:
    """Up to `rubric_parse_max_retries + 1` total attempts. Only a
    validation failure (`RubricParseError`, or the gate) triggers a
    retry — quota/API-down errors are infra failures, not content
    problems, and propagate immediately rather than burning retries
    against the daily meter for nothing (ticket edge case).

    `source` is what the upload path builds from the extraction (tables
    cell by cell, repeated copies read once). Without it, `raw_text` is the
    whole source. Either way every level descriptor is checked against it
    (BUG-566): one that is not the document's own words is a gate issue,
    retried with that feedback and, if it persists, left for the
    instructor to review rather than stored as if it were parsed."""
    source = source or DecompositionSource.from_text(blocks, raw_text)
    total_attempts = settings.rubric_parse_max_retries + 1
    feedback: str | None = None
    # The schema-valid attempt with the fewest gate issues (then the most
    # criteria) is what the review screen gets when no attempt passes: the
    # last one could be the worst (backend review, 2026-10-10: 9 criteria
    # then 4, and the 4 were shown).
    best: tuple[list[ParsedCriterion], list[str], list[SourceIssue]] | None = None
    last_issues: list[str] = ["no attempt produced a schema-valid response"]
    # Every gate issue an earlier draft raised: a later draft that passes
    # may have changed the rubric only to pass (invented a missing level,
    # dropped a row), so the instructor confirms it (BUG-391 AC1).
    earlier_gate_issues: list[str] = []

    # BUG-160: `raw_text` is identical across every retry in this loop, so
    # checking it once up front (rather than per-attempt) is not a missed
    # case — it's the same input every time. A suspected match doesn't
    # change what the gate checks; it's an independent concern that must
    # survive even a clean gate pass (see below).
    injection = detect_injection_signal(source.prompt_text)
    # Neither of these changes between attempts, so neither is retried.
    standing: list[str] = []
    if source.unread_copy_lines:
        standing.append(UNREAD_COPY_TEXT_REASON.format(count=source.unread_copy_lines))
    if injection.suspected:
        standing.append(RUBRIC_INJECTION_SUSPECTED_REASON)

    for attempt in range(1, total_attempts + 1):
        try:
            criteria = await decompose_rubric(
                source.prompt_text, llm, feedback=feedback, settings=settings
            )
        except RubricParseError as exc:
            last_issues = [str(exc)]
            await _log_attempt(
                session,
                rubric_id,
                attempt,
                ok=False,
                issues=last_issues,
                coverage=None,
                injection=injection,
            )
            feedback = last_issues[0]
            continue

        criteria, source_issues = verify_against_source(criteria, source, settings)
        gate = run_gate(source.gate_blocks, criteria, settings, source.checklist_text)
        draft_issue_count = len(gate.reasons)
        gate.reasons[:0] = [issue.message for issue in source_issues]
        gate.ok = gate.ok and not source_issues
        await _log_attempt(
            session,
            rubric_id,
            attempt,
            ok=gate.ok,
            issues=gate.reasons,
            coverage=gate.coverage_ratio,
            injection=injection,
        )
        if gate.ok:
            issues = (
                [RETRY_CHANGED_DRAFT_REASON.format(issues="; ".join(earlier_gate_issues))]
                if earlier_gate_issues
                else []
            )
            issues.extend(standing)
            issues.extend(_unplaced_levels_note(criteria, source))
            return DecompositionOutcome(
                criteria=criteria,
                needs_review=bool(issues),
                issues=issues,
                attempts=attempt,
                issue_details=_details(issues, []),
            )
        # A draft is ranked by its whole-draft issues, then by how much of the
        # rubric it kept, and only then by its descriptor issues: each bad
        # descriptor is its own issue, and counting them first would prefer a
        # draft that dropped most rows (backend review, BUG-566).
        rank = (draft_issue_count, -len(criteria), len(source_issues))
        if best is None or rank < (
            len(best[1]) - len(best[2]),
            -len(best[0]),
            len(best[2]),
        ):
            best = (criteria, gate.reasons, source_issues)  # best-so-far partial
        earlier_gate_issues.extend(
            r
            for r in gate.reasons
            if r not in earlier_gate_issues and ("levels while" in r or "separate items" in r)
        )
        last_issues = gate.reasons
        feedback = "; ".join(gate.reasons)

    best_criteria, best_issues, best_source = best if best is not None else ([], last_issues, [])
    best_issues = list(
        dict.fromkeys([*best_issues, *standing, *_unplaced_levels_note(best_criteria, source)])
    )
    return DecompositionOutcome(
        criteria=best_criteria,
        needs_review=True,
        issues=best_issues,
        attempts=total_attempts,
        issue_details=_details(best_issues, best_source),
    )


async def _log_attempt(
    session: AsyncSession,
    rubric_id: int,
    attempt: int,
    *,
    ok: bool,
    issues: list[str],
    coverage: float | None,
    injection: InjectionSignal | None = None,
) -> None:
    """rubric_id lives in the payload, not a typed column — audit_log's
    typed FK-shaped column (check_run_id) means something else, and this
    event predates any check_run."""
    session.add(
        AuditLog(
            event_type="rubric_parse_attempt",
            payload={
                "rubric_id": rubric_id,
                "attempt": attempt,
                "ok": ok,
                "issues": issues,
                "coverage_ratio": coverage,
                "injection_suspected": injection.suspected if injection else False,
                "injection_pattern_id": injection.matched_pattern_id if injection else None,
            },
        )
    )
    await session.flush()
