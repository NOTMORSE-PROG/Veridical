"""Originality/reuse check (F7) assembly: V-036's embedding pipeline +
V-037's similarity query, into one `check_result` + real `Flag` rows —
same shape as `app.checks.forensics.service.run_statistical_forensics_check`.

**V-077 (D-028): this module never writes the checked manuscript into the
corpus.** The F7 embedding tables hold only active Library entries
(`app.library.service` owns entering and leaving), so this module only reads
them. Since D-028 a dashboard check adds no comparison source, and a re-run or
cancelled run cannot add or orphan corpus rows; manuscripts checked before
D-028 were carried into the Library as legacy entries (migration 0037).
"""

import asyncio
import dataclasses

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_event
from app.checks.reuse.embed import compute_document_embeddings, compute_passage_embeddings
from app.checks.reuse.query import (
    PassageMatch,
    SimilarityMatch,
    best_supporting_passage,
    canonical_library_copies,
    comparison_exclusions,
    is_first_upload_for_instructor,
    query_similar_manuscripts,
    query_similar_passages,
)
from app.checks.reuse.quotations import pair_wording
from app.checks.reuse.same_work import SameWork, find_same_work, name_rules_fingerprint
from app.checks.reuse.source import coarse_candidate_supported, passage_match_supported
from app.checks.source_quality import persist_source_limitation
from app.config import Settings
from app.findings.policy import reuse_reason
from app.ingest.analytical_source import analytical_source
from app.ingest.patterns import load_patterns
from app.ingest.references import reference_region_not_assessed, select_references
from app.ingest.schemas import ExtractionResult
from app.models.enums import CheckKind, FlagSeverity, ResultOutcome
from app.models.run import CheckResult, Flag

# BUG-050 Branch B (owner, 2026-08-16) / BUG-097: the F7 corpus is shared
# across instructor accounts BY DESIGN — that decision is not what these
# wordings are about. What they must never do is turn a match into a
# content leak: first-time-user review reproduced a day-one account's very first
# report exposing another instructor's real group name AND the matched
# document's actual chapter heading text. A match is reported via an
# opaque, non-identifying reference (`{ref}`, the archived manuscript's
# internal id) — enough to make the match IDENTIFIABLE (BUG-050 item 5;
# can be referred back to, e.g. once V-066's browse screen exists) without
# making it IDENTIFYING (no other account's real label or heading ever
# appears in text an instructor reads). `matched_group_label` and
# `matched_chapter_title` stay in `detail` below for internal use only —
# confirmed never serialized to any API response (`app/flags/service.py`
# reads only `verdict`/`basis` off `detail`).
# Older results matched on this note; keep it word for word.
NO_TEXT_NOTE = "No embeddable content was extracted from this manuscript."

EXACT_DUPLICATE_WHOLE_DOC_WORDING = (
    "This manuscript appears to be an exact or near-exact textual duplicate of "
    "Library record #{ref}: possible resubmission or reuse. Please verify manually."
)
HIGH_SIMILARITY_WHOLE_DOC_WORDING = (
    "This manuscript shows high textual similarity to Library record #{ref}: "
    "possible shared content or reuse. Please verify manually."
)
EXACT_DUPLICATE_CHAPTER_WORDING = (
    'The section "{own_chapter}" appears to be an exact or near-exact textual '
    "duplicate of a section in Library record #{ref}: possible resubmission or "
    "reuse of that section. Please verify manually."
)
HIGH_SIMILARITY_CHAPTER_WORDING = (
    'The section "{own_chapter}" shows high textual similarity to a section '
    "in Library record #{ref}: possible reuse of that section. Please verify manually."
)

# F7.4 (V-072) passage-level wording. Deliberately different SHAPE from the
# templates above, not just different text: those go into `Flag.reason`
# AND `Flag.evidence_excerpt` (there is no real quotable text to excerpt at
# whole-doc/chapter granularity, so the sentence stands in for both). A
# passage match DOES have real quotable text -- the passage itself -- so
# `evidence_excerpt` becomes the passage's own words (bounded to
# `reuse_passage_chunk_words`) and these templates go into
# `Flag.detail["reason"]` only, which `app.flags.service._to_flag_out`
# already surfaces as `ai_reasoning`. This also means passage flags are the
# ONLY F7 flags whose `evidence_excerpt` is real page text, not a
# templated sentence -- which is what lets V-065's `page.search_for()`-based
# region recovery actually find and highlight them precisely (measured
# 13/13 for real quoted prose, `app/ingest/regions.py`'s own module
# docstring), unlike today's whole-doc/chapter flags, which only ever
# resolve to a coarse `section`/`whole_document` region.
EXACT_DUPLICATE_PASSAGE_WORDING = (
    "This passage appears to be an exact or near-exact textual duplicate of a "
    "passage in Library record #{ref}: possible resubmission or reuse of this "
    "passage. Please verify manually."
)
HIGH_SIMILARITY_PASSAGE_WORDING = (
    "This passage shows high textual similarity to a passage in Library record "
    "#{ref}: possible reuse of this passage. Please verify manually."
)

# BUG-140 (owner ruling, 2026-09-03), widened by BUG-662: another upload of
# the same work in the SAME instructor's Library (a re-upload, a revision, a
# legacy copy under another group label) is a resubmission question, never a
# plagiarism verdict. Every such record is left out of the comparison and
# reported in one low lead instead of the whole-doc/chapter/passage flood it
# otherwise produces (measured: 82 flags from one re-upload; 41 high leads per
# run on production runs 11, 12, 17 and 18). The sentence shown comes from
# `app.findings.policy.reuse_reason`.

# BUG-097 (presentation-only remedy, owner ruling 2026-08-24): drives
# `detail["first_upload_context"]` below, which surfaces as the report's
# distinct `FirstUploadContextBanner`/`FirstUploadContextGroupNote`
# (frontend) — never changes severity or scoring (see
# `query.is_first_upload_for_instructor`'s own docstring for why a
# severity change was rejected).
#
# UX review finding (2026-08-24, live review of the built banner): an
# earlier version of this fix ALSO appended a caveat sentence to `reason`
# (surfaced via `FlagOut.ai_reasoning`) — with the banner rendering right
# above it, the same "no track record yet, verify carefully" idea was
# stated twice in near-identical wording on one screen, competing for
# attention (Nielsen #4, minimalist design) and undermining the banner's
# own job of being the one clear, distinct signal. The banner is the
# sole disclosure surface now; `reason`/`ai_reasoning` stay exactly what
# the underlying match template already says.


def _match_to_flag_draft(
    match: SimilarityMatch, *, first_upload: bool = False
) -> tuple[FlagSeverity, str, dict]:
    is_chapter = match.matched_chapter_title is not None
    if match.level == "exact_duplicate":
        severity = FlagSeverity.high
        template = (
            EXACT_DUPLICATE_CHAPTER_WORDING if is_chapter else EXACT_DUPLICATE_WHOLE_DOC_WORDING
        )
    else:
        severity = FlagSeverity.med
        template = (
            HIGH_SIMILARITY_CHAPTER_WORDING if is_chapter else HIGH_SIMILARITY_WHOLE_DOC_WORDING
        )

    reason = template.format(
        ref=match.matched_manuscript_id,
        own_chapter=match.own_chapter_title,
    )
    detail = {
        "kind": f"reuse_{match.level}{'_chapter' if is_chapter else ''}",
        "affects_readiness": False,
        "reason": reason,
        "similarity": round(match.similarity, 3),
        "matched_manuscript_id": match.matched_manuscript_id,
        "matched_group_label": match.matched_group_label,
        "matched_chapter_title": match.matched_chapter_title,
        "own_chapter_title": match.own_chapter_title,
        "first_upload_context": first_upload,
    }
    reason = reuse_reason(detail)
    detail["reason"] = reason
    return severity, reason, detail


def _supporting_passage_from_precomputed(
    matched_manuscript_id: int,
    passage_matches: list[PassageMatch],
    *,
    own_chapter_index: int | None = None,
    matched_chapter_index: int | None = None,
) -> PassageMatch | None:
    """BUG-153: `passage_matches` is `query_similar_passages`'s
    corpus-wide, already-computed "best match anywhere, per own passage"
    result (`run_originality_reuse_check` always runs it, for the
    passage-level flags above) — free to filter down to one specific
    matched manuscript, at zero extra queries. Returns `None` when that
    manuscript never won ANY own-passage's corpus-wide top-1 slot in the
    (scoped) candidate pool, which does not mean no real pairing exists (a
    weaker rival could have beaten it on every single passage) -- the
    caller falls back to `best_supporting_passage`'s scoped,
    manuscript-specific query only in that case, so the expensive path is
    the exception, not the rule.

    `own_chapter_index`/`matched_chapter_index` (backend review finding,
    BUG-153 review, live-reproduced): a chapter-level match's own claim is
    about TWO SPECIFIC chapters, not "anywhere in either manuscript" --
    without this filter a Chapter 1 flag could be attached a supporting
    passage that actually lives in Chapter 2, which is real text but
    self-contradicts the very finding it's shown to evidence. Left `None`
    (no filter) for a whole-document match, which makes no such claim.
    NOTE: this is a genuinely weaker guarantee than the scoped fallback
    query below within a chapter pair too -- a candidate here only exists
    if that same own-passage ALSO happened to win its OWN corpus-wide
    top-1 slot before this filter was ever applied, so a real but
    non-corpus-winning pairing within the target chapter pair can still
    be missed here and only found by the fallback."""
    candidates = [p for p in passage_matches if p.matched_manuscript_id == matched_manuscript_id]
    if own_chapter_index is not None:
        candidates = [p for p in candidates if p.own_chapter_index == own_chapter_index]
    if matched_chapter_index is not None:
        candidates = [p for p in candidates if p.matched_chapter_index == matched_chapter_index]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.similarity)


def _supporting_passage_detail(supporting: PassageMatch) -> dict:
    """BUG-153: distinct key names from `_passage_match_to_flag_draft`'s
    detail above on purpose — `evidence_excerpt` for a whole-doc/chapter
    flag is the templated accusation sentence (see this module's wording
    templates' own docstring), so it can never double as `own_text` the
    way a genuine passage-level flag's `evidence_excerpt` does.

    `level` is `supporting`'s OWN classification, or None when its real
    similarity is below the configured standalone passage thresholds, not the
    parent whole-doc/chapter match's band. An earlier version of this fix
    echoed the parent's level instead, reasoning that the two should
    "agree" -- backend review found (live-reproduced, BUG-153 review)
    that this could overstate a weak supporting passage as "Exact
    duplicate" purely because a stronger aggregate match happened to
    contain it, which is the more dangerous direction (a flag can still
    force Not Ready under BUG-150 while showing evidence stronger than it
    is). The passage panel's own band describes the QUOTED PASSAGE, not
    the flag as a whole -- the flag's own severity badge is shown
    separately -- so there is no real inconsistency in the two differing,
    only in overstating one from the other."""
    return {
        "own_text": supporting.own_text,
        "own_context_text": supporting.own_context_text,
        "matched_text": supporting.matched_text,
        "matched_context_text": supporting.matched_context_text,
        "matched_manuscript_id": supporting.matched_manuscript_id,
        "similarity": round(supporting.similarity, 3),
        "level": supporting.level,
    }


def _same_work_flag_draft(same_work: SameWork) -> tuple[FlagSeverity, str, dict]:
    detail = {
        # Kept under the BUG-140 kind so earlier reports and the report's
        # labels read it the same way.
        "kind": "reuse_same_instructor_resubmission",
        "affects_readiness": False,
        "matched_manuscript_id": same_work.sources[0].manuscript_id,
        "matched_manuscript_ids": [s.manuscript_id for s in same_work.sources],
        "same_work": [
            {
                "manuscript_id": s.manuscript_id,
                "basis": s.basis,
                "similarity": round(s.similarity, 3) if s.similarity is not None else None,
            }
            for s in same_work.sources
        ],
    }
    reason = reuse_reason(detail)
    detail["reason"] = reason
    # Raised a step when a record's own title, team or authors differ: the
    # instructor has to confirm it, so it should not read as routine.
    needs_confirming = any(s.basis == "near_identical_text_names_differ" for s in same_work.sources)
    return (FlagSeverity.med if needs_confirming else FlagSeverity.low), reason, detail


def _passage_match_to_flag_draft(
    match: PassageMatch, *, first_upload: bool = False
) -> tuple[FlagSeverity, str, str, dict]:
    """Returns (severity, evidence_excerpt, page_anchor, detail) — a
    different shape from `_match_to_flag_draft` above (see the wording
    templates' own comment for why: real passage text as the excerpt,
    the explanatory sentence in `detail["reason"]` instead)."""
    if match.level not in ("exact_duplicate", "high_similarity"):
        raise ValueError("Unclassified supporting evidence cannot create a passage flag")
    if match.level == "exact_duplicate":
        severity = FlagSeverity.high
        template = EXACT_DUPLICATE_PASSAGE_WORDING
    else:
        severity = FlagSeverity.med
        template = HIGH_SIMILARITY_PASSAGE_WORDING
    reason = template.format(ref=match.matched_manuscript_id)

    page_anchor = (
        f"p. {match.own_page}" if match.own_page is not None else f"¶{match.own_paragraph}"
    )
    detail = {
        "kind": f"reuse_{match.level}_passage",
        "affects_readiness": False,
        "reason": reason,
        "first_upload_context": first_upload,
        "similarity": round(match.similarity, 3),
        "matched_manuscript_id": match.matched_manuscript_id,
        "matched_group_label": match.matched_group_label,  # internal only, BUG-050/097
        "own_chapter_index": match.own_chapter_index,
        "own_context_text": match.own_context_text,
        "matched_chapter_index": match.matched_chapter_index,
        "matched_page": match.matched_page,
        "matched_paragraph": match.matched_paragraph,
        "matched_text": match.matched_text,
        "matched_context_text": match.matched_context_text,
        "is_reference_list_match": match.own_is_reference_list or match.matched_is_reference_list,
        "is_block_quote_match": match.own_is_block_quote or match.matched_is_block_quote,
    }
    detail["reason"] = reuse_reason(detail)
    return severity, match.own_text, page_anchor, detail


def _remap(match, remapped: dict[int, tuple[int, int]]):
    """Point a match at its canonical Library copy (`canonical_library_copies`)."""
    target = remapped.get(match.matched_manuscript_id)
    if target is None:
        return match
    canonical_id, canonical_owner = target
    return dataclasses.replace(
        match, matched_manuscript_id=canonical_id, matched_instructor_id=canonical_owner
    )


def _wording(evidence: dict) -> dict:
    return {"shingle_share": evidence["shingle_share"], "longest_run": evidence["longest_run"]}


def _pair_wording(pair, settings) -> dict:
    """Shared wording between a passage pair, with quotations marked and
    cited left out (BUG-632, `quotations.pair_wording`)."""
    return pair_wording(
        pair.own_text, pair.own_context_text, pair.matched_text, pair.matched_context_text, settings
    )


def _withheld_lead(granularity: str, match, level, pair, settings) -> dict:
    """A match that was not raised, with the wording evidence it was judged
    on (BUG-620), from `pair`, the passage pair behind it. Only the opaque
    matched record id, as everywhere in audit."""
    evidence = _pair_wording(pair, settings)
    return {
        "granularity": granularity,
        "matched_manuscript_id": match.matched_manuscript_id,
        "similarity": round(match.similarity, 3),
        "level": level,
        "reason": "cited_quotation" if evidence["cited_quotation_only"] else "no_shared_wording",
        **evidence,
    }


async def _same_work_audit(session: AsyncSession, same_work: SameWork, settings) -> dict:
    """What BUG-662 left out of the comparison and why, so a past result can
    be rebuilt after the Library or the name rules change. Opaque ids only."""
    cache = {}
    unreadable = [
        s.manuscript_id
        for s in same_work.sources
        if not await coarse_candidate_supported(session, s.manuscript_id, settings, cache)
    ]
    return {
        "same_work_source_ids": [s.manuscript_id for s in same_work.sources],
        "same_work": [
            {"manuscript_id": s.manuscript_id, "basis": s.basis, "similarity": s.similarity}
            for s in same_work.sources
        ],
        # Includes other instructors' byte-identical copies of those records.
        "same_work_excluded_ids": sorted(same_work.excluded_ids),
        # Records whose own source could not be read; the decision used their
        # stored whole-document vector (see `app.checks.reuse.same_work`).
        "same_work_source_unreadable_ids": unreadable,
        "same_work_similarity_threshold": settings.reuse_same_work_similarity_threshold,
        "same_work_name_rules": name_rules_fingerprint(settings),
    }


def _has_text(extraction: ExtractionResult) -> bool:
    return any(not block.is_furniture and block.text.strip() for block in extraction.blocks)


def _text_pages(extraction: ExtractionResult) -> list[int]:
    return sorted(
        {
            block.page
            for block in extraction.blocks
            if block.page is not None and not block.is_furniture and block.text.strip()
        }
    )


async def run_originality_reuse_check(
    session: AsyncSession,
    manuscript_id: int,
    check_run_id: int,
    extraction: ExtractionResult,
    settings: Settings,
    *,
    # BUG-140: keyword-only, optional, default `None` -- every existing
    # caller (and every fixture in this suite that predates this ticket)
    # keeps producing EXACTLY its old behavior unchanged. Only a caller
    # that actually has this manuscript's own instructor/hash (the real
    # pipeline, `app/pipeline/machine.py`) opts into same-instructor
    # resubmission detection by passing them.
    instructor_id: int | None = None,
    content_hash: str | None = None,
) -> CheckResult:
    source_stop = await persist_source_limitation(
        session, check_run_id, CheckKind.originality_reuse, extraction
    )
    if source_stop is not None:
        return source_stop
    source = analytical_source(extraction)
    selection = select_references(extraction, load_patterns(settings.ingest_patterns_file))
    membership_partial = bool(selection.membership_issues)
    # When the reference list's end is unclear, the comparison leaves out
    # everything from the list onward; say where instead of passing.
    not_compared = reference_region_not_assessed(extraction, selection)
    # BUG-152: off the event loop -- this runs the local model2vec encoder
    # over every chapter plus the whole document, real CPU work with no
    # I/O, on the single Render worker that also serves every other
    # instructor's requests (including the 2s progress poll reporting on
    # this exact check).
    embeddings = await asyncio.get_running_loop().run_in_executor(
        None, compute_document_embeddings, extraction, settings
    )

    # BUG-662: the instructor's own Library records of this same work are
    # found before any comparison and left out of it, so a copy of the thesis
    # can neither flag as reuse nor hide a real source behind itself in a
    # nearest-match query. The same-file test needs no text, so it still
    # works when nothing could be embedded (the branch below, e.g. an
    # image-only re-scan).
    exclusions = await comparison_exclusions(session, manuscript_id)
    same_work = await find_same_work(
        session,
        manuscript_id,
        instructor_id=instructor_id,
        content_hash=content_hash,
        whole_document=embeddings.whole_document,
        exclude_manuscript_ids=exclusions,
        settings=settings,
    )
    has_resubmission = bool(same_work.sources)
    same_work_audit = await _same_work_audit(session, same_work, settings)

    if embeddings.whole_document is None:
        # Text that could not be told apart from an unclear reference list was
        # not compared at all, which is not the same as a file with no text.
        body_refused = (
            bool(selection.issues)
            and source.state not in {"not_recorded", "unavailable"}
            and _has_text(extraction)
        )
        result = CheckResult(
            check_run_id=check_run_id,
            criterion_id=None,
            kind=CheckKind.originality_reuse,
            outcome=ResultOutcome.escalated
            if source.limited or membership_partial or body_refused
            else (ResultOutcome.passed if has_resubmission else ResultOutcome.not_applicable),
            detail={
                **source.detail(),
                "reference_membership_issues": list(selection.membership_issues),
                "pages_not_compared": _text_pages(extraction) if body_refused else [],
                # Kept apart from coverage_reason, which a native-equation or
                # membership limit can take over, so the refusal is never
                # reported as a file with no text.
                **({"not_compared_reason": "reference_boundary_unclear"} if body_refused else {}),
                **(
                    {"coverage_reason": "reference_boundary_unclear"}
                    if body_refused and not membership_partial
                    else {}
                ),
                **(
                    {
                        "coverage_reason": "reference_membership_partial",
                        "passage_inventory_status": "not_captured",
                    }
                    if membership_partial
                    else {}
                ),
                **(
                    {"coverage_reason": "native_math_comparison_incomplete"}
                    if source.limited
                    else {}
                ),
                # backend review finding (BUG-140 review): 0 alongside a
                # real flag read as internally inconsistent -- the
                # embedding-based archive comparison never ran (nothing to
                # embed), but the hash lookup DID find one real comparable
                # entry, and cold-start disclosure exists precisely to be
                # honest about what was actually compared against.
                "archive_size_n": len(same_work.sources),
                "corpus": "library",
                # V-077 (backend review): no text means no comparison ran; the
                # zero above must never read as "the Library was empty".
                "comparison_ran": False,
                "n_flags": 1 if has_resubmission else 0,
                "n_same_work_sources": len(same_work.sources),
                "note": "The body could not be told apart from the reference list, so no "
                "text was compared."
                if body_refused
                else NO_TEXT_NOTE,
            },
        )
        session.add(result)
        await session.flush()
        if has_resubmission:
            severity, reason, detail = _same_work_flag_draft(same_work)
            session.add(
                Flag(
                    check_result_id=result.id,
                    severity=severity,
                    evidence_excerpt=reason,
                    page_anchor="whole document",
                    detail=detail,
                )
            )
        # BUG-151: F6/F7 make zero LLM calls, so they wrote nothing to the
        # audit log at all -- the check that forced 82 of one production
        # run's 99 flags had no record of how its verdict was reached
        # (charter judgment 4). One event per check, every path, including
        # this early "nothing to compare" branch. Only the OPAQUE matched
        # manuscript id ever goes in the payload, never `matched_group_
        # label`/`matched_chapter_title` -- the same BUG-050 IDENTIFIABLE-
        # not-IDENTIFYING bar the flags themselves already hold, extended
        # here rather than assumed to not apply just because the audience
        # is this check's own owning instructor.
        await write_audit_event(
            session,
            event_type="originality_reuse_check_computed",
            check_run_id=check_run_id,
            payload={
                **result.detail,
                "outcome": result.outcome.value,
                "matched_manuscript_ids": sorted(s.manuscript_id for s in same_work.sources),
                **same_work_audit,
            },
        )
        await session.commit()
        return result

    query_result = await query_similar_manuscripts(
        session,
        manuscript_id,
        embeddings,
        settings,
        exclude_manuscript_ids=same_work.excluded_ids,
    )
    # F7.4 (V-072): passage-level candidates alongside the whole-doc/chapter
    # ones above -- a genuinely different chunker (`compute_passage_embeddings`),
    # queried separately (`query_similar_passages` excludes reference-list/
    # block-quote passages by default, ticket AC3), but folded into the
    # SAME check_result (one F7 result per run, more flags at finer
    # granularity — same "flags at every matching level" precedent the
    # existing exact-duplicate test already documents for chapter vs
    # whole-doc).
    passages = await asyncio.get_running_loop().run_in_executor(
        None, compute_passage_embeddings, extraction, settings
    )
    passage_query_result = await query_similar_passages(
        session,
        manuscript_id,
        passages,
        settings,
        # BUG-620: a passage match needs shared wording, tried over the
        # nearest few candidates so a topic match cannot hide the real source.
        # A quotation marked and cited is left out of that test (BUG-632).
        confirm=lambda m: _pair_wording(m, settings)["confirmed"],
        candidates=settings.reuse_confirm_candidates,
        exclude_manuscript_ids=same_work.excluded_ids,
    )
    # V-077: byte-identical Library copies (two instructors adding the same
    # file) are reported against ONE representative, so a single source can
    # never read as two. Remaps only ids that already matched.
    remapped = await canonical_library_copies(
        session,
        {m.matched_manuscript_id for m in query_result.matches}
        | {p.matched_manuscript_id for p in passage_query_result.matches},
        instructor_id,
        exclude_manuscript_ids=exclusions | same_work.excluded_ids,
    )
    if remapped:
        query_result = dataclasses.replace(
            query_result, matches=[_remap(m, remapped) for m in query_result.matches]
        )
        passage_query_result = dataclasses.replace(
            passage_query_result,
            matches=[_remap(p, remapped) for p in passage_query_result.matches],
        )
        # A canonical identical-byte copy may retain an older extraction.
        # Revalidate the selected representative instead of transferring
        # source-projection authority merely because the file hash matches.
        source_cache = {}
        supported_coarse = []
        for match in query_result.matches:
            if await coarse_candidate_supported(
                session, match.matched_manuscript_id, settings, source_cache
            ):
                supported_coarse.append(match)
        query_result = dataclasses.replace(
            query_result,
            matches=supported_coarse,
            n_source_candidates_withheld=query_result.n_source_candidates_withheld
            + len(query_result.matches)
            - len(supported_coarse),
        )
        supported_passages = []
        for match in passage_query_result.matches:
            if await passage_match_supported(session, manuscript_id, match, settings, source_cache):
                supported_passages.append(match)
        passage_query_result = dataclasses.replace(
            passage_query_result,
            matches=supported_passages,
            n_source_candidates_withheld=passage_query_result.n_source_candidates_withheld
            + len(passage_query_result.matches)
            - len(supported_passages),
        )
    # BUG-097 (presentation-only remedy): computed once, applied to every
    # match this run produces — see `is_first_upload_for_instructor`'s own
    # docstring for why this changes wording/context only, never severity.
    first_upload = await is_first_upload_for_instructor(session, manuscript_id)

    whole_and_chapter_matches = query_result.matches
    passage_matches = passage_query_result.matches

    # BUG-620: a similarity match is a finding only when the two passages
    # also share wording. Same-genre text (two requirements lists, two
    # signature blocks) sits close in the static embedding with no words in
    # common; those leads are counted and audited, not raised.
    withheld_leads = [
        _withheld_lead("passage", m, m.level, m, settings) for m in passage_query_result.unconfirmed
    ]

    coarse_drafts = []
    for match in whole_and_chapter_matches:
        severity, reason, detail = _match_to_flag_draft(match, first_upload=first_upload)
        anchor = match.own_chapter_title or "whole document"
        # BUG-153: a whole-document/chapter match must carry the same
        # class of evidence its passage-level sibling already does. Try
        # the free, already-computed corpus-wide passage result first;
        # only fall back to a scoped, manuscript-specific query when this
        # SPECIFIC matched manuscript (and, for a chapter-level match, the
        # SPECIFIC chapter pair the flag is actually about -- see both
        # helpers' own docstrings) never won any own-passage's corpus-wide
        # top-1 slot (rare, but not "no real pairing exists"). Both index
        # fields are `None` for a whole-document match (no chapter claim
        # to scope), set for a chapter-level one.
        supporting = _supporting_passage_from_precomputed(
            match.matched_manuscript_id,
            passage_matches,
            own_chapter_index=match.own_chapter_index,
            matched_chapter_index=match.matched_chapter_index,
        )
        if supporting is None:
            supporting = await best_supporting_passage(
                session,
                passages,
                match.matched_manuscript_id,
                settings,
                own_chapter_index=match.own_chapter_index,
                matched_chapter_index=match.matched_chapter_index,
            )
        if supporting is not None:
            lead = _withheld_lead("section", match, match.level, supporting, settings)
            # BUG-620: a section is reported as reused only when its best
            # evidence is itself a match: it shares wording and, for a
            # high-similarity section, clears the passage bar on its own. A
            # section near the threshold on topic, evidenced only by a weaker
            # passage (shared standard text, say), is counted instead.
            if not lead["confirmed"]:
                withheld_leads.append(lead)
                continue
            if match.level != "exact_duplicate" and supporting.level is None:
                withheld_leads.append({**lead, "reason": "evidence_below_passage_bar"})
                continue
            detail["supporting_passage"] = {
                **_supporting_passage_detail(supporting),
                "shared_wording": _wording(lead),
            }
        elif severity == FlagSeverity.high:
            # Ticket's own fallback clause: "If a finding genuinely cannot
            # be evidenced, it should not be high severity." No real
            # supporting passage could be found anywhere in the (scoped)
            # search space -- either the matched manuscript has no
            # passage archive at all (an image-only match, or one
            # ingested before F7.4 existed), or, for a chapter-level
            # match, this specific chapter pair has no passage on one or
            # both sides even though other chapters do (a chapter-level
            # aggregate match can be driven by diffuse similarity spread
            # across many passages, not one strong pairing) -- this
            # finding can no longer force Not Ready (BUG-150) on evidence
            # the instructor has no way to check (charter judgment rule 1).
            severity = FlagSeverity.med
            detail["evidence_unavailable"] = True
        coarse_drafts.append((severity, reason, anchor, detail))

    n_flags = len(coarse_drafts) + len(passage_matches) + (1 if has_resubmission else 0)
    no_comparisons = (
        query_result.archive_size_n == 0
        and passage_query_result.passage_archive_size_n == 0
        and n_flags == 0
    )
    withheld = (
        query_result.n_source_candidates_withheld
        + passage_query_result.n_source_candidates_withheld
    )
    native_limited = source.limited or bool(withheld)

    result = CheckResult(
        check_run_id=check_run_id,
        criterion_id=None,
        kind=CheckKind.originality_reuse,
        outcome=ResultOutcome.escalated
        if native_limited or membership_partial or not_compared
        else (ResultOutcome.not_applicable if no_comparisons else ResultOutcome.passed),
        detail={
            **source.detail(),
            "reference_membership_issues": list(selection.membership_issues),
            "passage_inventory_status": "not_captured" if membership_partial else "available",
            "n_source_candidates_withheld": withheld,
            "coverage_reason": "native_math_comparison_incomplete"
            if native_limited
            else "reference_membership_partial"
            if membership_partial
            else "reference_end_unclear"
            if not_compared
            else ("no_comparison_archive" if no_comparisons else None),
            "pages_not_compared": not_compared.pages if not_compared else [],
            "not_compared_from": not_compared.start if not_compared else None,
            "comparison_ran": True,
            # BUG-662: own records of the same work were compared too (by
            # file hash and whole text) before being left out of the queries;
            # counting them keeps "compared against 0" from sitting beside a
            # lead that names one.
            "archive_size_n": query_result.archive_size_n + len(same_work.sources),
            # V-077: what this count measured. Results written before D-028
            # have no "corpus" key; they counted the pre-Library archive.
            "corpus": "library",
            "passage_archive_size_n": passage_query_result.passage_archive_size_n,
            # Saved facts from the same aggregate as the initial corpus count.
            # Later Library mutations must not rewrite this report's scope.
            "library_passage_indexing": query_result.library_passage_indexing,
            "n_flags": n_flags,
            # BUG-662: own Library records of this same work, left out of
            # the counts above and named in one low lead.
            "n_same_work_sources": len(same_work.sources),
            # BUG-620: matches not raised. Kept apart because they mean
            # different things: passages that share no wording (topic or
            # layout only), and sections whose only evidence is a passage too
            # weak to be a match itself.
            "n_similar_without_shared_wording": sum(
                lead["reason"] == "no_shared_wording" for lead in withheld_leads
            ),
            "n_sections_evidence_below_passage_bar": sum(
                lead["reason"] == "evidence_below_passage_bar" for lead in withheld_leads
            ),
            # BUG-632: pairs whose shared wording lies only inside quotations
            # marked and cited in the text. Judged on the nearest Library
            # passage, like the count above.
            "n_shared_wording_only_in_cited_quotations": sum(
                lead["reason"] == "cited_quotation" for lead in withheld_leads
            ),
            "first_upload_context": first_upload,
            "passages_found": len(passages),
        },
    )
    session.add(result)
    await session.flush()  # need result.id before attaching flags
    if has_resubmission:
        severity, reason, detail = _same_work_flag_draft(same_work)
        session.add(
            Flag(
                check_result_id=result.id,
                severity=severity,
                evidence_excerpt=reason,
                page_anchor="whole document",
                detail=detail,
            )
        )
    for severity, reason, anchor, detail in coarse_drafts:
        session.add(
            Flag(
                check_result_id=result.id,
                severity=severity,
                evidence_excerpt=reason,
                page_anchor=anchor,
                detail=detail,
            )
        )
    for passage_match in passage_matches:
        severity, evidence_excerpt, page_anchor, detail = _passage_match_to_flag_draft(
            passage_match, first_upload=first_upload
        )
        detail["shared_wording"] = _wording(_pair_wording(passage_match, settings))
        session.add(
            Flag(
                check_result_id=result.id,
                severity=severity,
                evidence_excerpt=evidence_excerpt,
                page_anchor=page_anchor,
                detail=detail,
            )
        )

    # BUG-151: F6/F7 make zero LLM calls, so they wrote nothing to the
    # audit log at all -- the check that forced 82 of one production run's
    # 99 flags had no record of how its verdict was reached (charter
    # judgment 4). `reuse_exact_duplicate_threshold`/`reuse_high_
    # similarity_threshold` are env-configurable (ground rule 7), so this
    # is the only way a past verdict is ever reconstructable after either
    # changes. Only the OPAQUE matched manuscript id ever goes in the
    # payload, never `matched_group_label`/`matched_chapter_title` -- the
    # same BUG-050 IDENTIFIABLE-not-IDENTIFYING bar the flags themselves
    # already hold, extended here rather than assumed to not apply just
    # because the audience is this check's own owning instructor.
    matched_manuscript_ids = sorted(
        {d["matched_manuscript_id"] for *_, d in coarse_drafts}
        | {p.matched_manuscript_id for p in passage_matches}
        | {s.manuscript_id for s in same_work.sources}
    )
    await write_audit_event(
        session,
        event_type="originality_reuse_check_computed",
        check_run_id=check_run_id,
        payload={
            **result.detail,
            "thresholds": {
                "exact_duplicate": settings.reuse_exact_duplicate_threshold,
                "high_similarity": settings.reuse_high_similarity_threshold,
                "passage_exact_duplicate": settings.reuse_passage_exact_duplicate_threshold,
                "passage_high_similarity": settings.reuse_passage_high_similarity_threshold,
                "confirm_shingle_words": settings.reuse_confirm_shingle_words,
                "confirm_shingle_share": settings.reuse_confirm_shingle_share,
                "confirm_run_words": settings.reuse_confirm_run_words,
                "confirm_share_min_words": settings.reuse_confirm_share_min_words,
                "confirm_candidates": settings.reuse_confirm_candidates,
            },
            # BUG-620: what was not raised and why, so a past decision can be
            # reconstructed after the Library changes. Opaque ids only.
            "withheld_leads": withheld_leads[: settings.reuse_confirm_audit_limit],
            "n_withheld_leads": len(withheld_leads),
            "matched_manuscript_ids": matched_manuscript_ids,
            "resubmission_source_id": same_work.sources[0].manuscript_id
            if has_resubmission
            else None,
            **same_work_audit,
        },
    )
    await session.commit()

    return result


async def existing_originality_reuse_result(
    session: AsyncSession, check_run_id: int
) -> CheckResult | None:
    """Idempotency guard (ENGINEERING §4) — a resumed run must not
    re-query-and-reflag work it already did."""
    return await session.scalar(
        select(CheckResult).where(
            CheckResult.check_run_id == check_run_id,
            CheckResult.kind == CheckKind.originality_reuse,
        )
    )
