"""Originality/reuse check (F7) assembly: V-036's embedding pipeline +
V-037's similarity query, into one `check_result` + real `Flag` rows —
same shape as `app.checks.forensics.service.run_statistical_forensics_check`.

**Write-back happens AFTER the check completes and the flags are
persisted** (ticket AC, F7.3) — `query_similar_manuscripts` runs first
against whatever is already in the archive, and only once that result is
committed does this manuscript's own embedding get written in, so a
re-run of the SAME check_run (idempotency, ENGINEERING §4) never
compares this manuscript against itself.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.reuse.embed import compute_document_embeddings, compute_passage_embeddings
from app.checks.reuse.query import (
    PassageMatch,
    SimilarityMatch,
    best_supporting_passage,
    is_first_upload_for_instructor,
    query_similar_manuscripts,
    query_similar_passages,
    same_instructor_hash_duplicate,
)
from app.checks.reuse.store import store_document_embeddings, store_passage_embeddings
from app.config import Settings
from app.ingest.schemas import ExtractionResult
from app.models.enums import CheckKind, FlagSeverity, ResultOutcome
from app.models.run import CheckResult, Flag

# BUG-050 Branch B (owner, 2026-08-16) / BUG-097: the F7 corpus is shared
# across instructor accounts BY DESIGN — that decision is not what these
# wordings are about. What they must never do is turn a match into a
# content leak: `newcomer` reproduced a day-one account's very first
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
EXACT_DUPLICATE_WHOLE_DOC_WORDING = (
    "This manuscript appears to be an exact or near-exact textual duplicate of "
    "archived manuscript #{ref} in VERIDICAL's shared originality library: "
    "possible resubmission or reuse. Please verify manually."
)
HIGH_SIMILARITY_WHOLE_DOC_WORDING = (
    "This manuscript shows high textual similarity to archived manuscript "
    "#{ref} in VERIDICAL's shared originality library: possible shared content "
    "or reuse. Please verify manually."
)
EXACT_DUPLICATE_CHAPTER_WORDING = (
    'The section "{own_chapter}" appears to be an exact or near-exact textual '
    "duplicate of a section in archived manuscript #{ref} in VERIDICAL's "
    "shared originality library: possible resubmission or reuse of that section. "
    "Please verify manually."
)
HIGH_SIMILARITY_CHAPTER_WORDING = (
    'The section "{own_chapter}" shows high textual similarity to a section '
    "in archived manuscript #{ref} in VERIDICAL's shared originality library: "
    "possible reuse of that section. Please verify manually."
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
    "passage in archived manuscript #{ref} in VERIDICAL's shared originality "
    "library: possible resubmission or reuse of this passage. Please verify manually."
)
HIGH_SIMILARITY_PASSAGE_WORDING = (
    "This passage shows high textual similarity to a passage in archived "
    "manuscript #{ref} in VERIDICAL's shared originality library: possible reuse "
    "of this passage. Please verify manually."
)

# BUG-140 (owner ruling, 2026-09-03): a whole-document exact duplicate
# against the SAME instructor's own earlier upload is a resubmission
# question, never a plagiarism verdict -- collapsed into this one
# low-severity confirmation flag instead of the whole-doc/chapter/passage
# flood a re-upload otherwise produces (measured: 82 flags from one
# re-upload, ticket's own evidence). Deliberately distinct wording from the
# EXACT_DUPLICATE_* templates above: "your own earlier upload", never
# "possible reuse" -- this is not the same finding softened, it names a
# different, non-accusatory situation (charter ground rule 3).
SAME_INSTRUCTOR_RESUBMISSION_WORDING = (
    "This manuscript appears to be the same document as your own earlier upload, "
    "archived manuscript #{ref}. This is most likely a resubmission or revision, "
    "not reuse. Please confirm this is intentional."
)

# BUG-097 (presentation-only remedy, owner ruling 2026-08-24): drives
# `detail["first_upload_context"]` below, which surfaces as the report's
# distinct `FirstUploadContextBanner`/`FirstUploadContextGroupNote`
# (frontend) — never changes severity or scoring (see
# `query.is_first_upload_for_instructor`'s own docstring for why a
# severity change was rejected).
#
# `ux-critic` finding (2026-08-24, live review of the built banner): an
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
        "reason": reason,
        "similarity": round(match.similarity, 3),
        "matched_manuscript_id": match.matched_manuscript_id,
        "matched_group_label": match.matched_group_label,
        "matched_chapter_title": match.matched_chapter_title,
        "own_chapter_title": match.own_chapter_title,
        "first_upload_context": first_upload,
    }
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

    `own_chapter_index`/`matched_chapter_index` (`backend-critic` finding,
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

    `level` is `supporting`'s OWN band (`_classify_passage` on its real
    similarity, always a genuine classification -- both
    `query_similar_passages` and `best_supporting_passage` only ever
    return a `PassageMatch` with a real, non-fabricated `level`), not the
    parent whole-doc/chapter match's band. An earlier version of this fix
    echoed the parent's level instead, reasoning that the two should
    "agree" -- `backend-critic` found (live-reproduced, BUG-153 review)
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


def _resubmission_flag_draft(matched_manuscript_id: int) -> tuple[FlagSeverity, str, dict]:
    reason = SAME_INSTRUCTOR_RESUBMISSION_WORDING.format(ref=matched_manuscript_id)
    detail = {
        "kind": "reuse_same_instructor_resubmission",
        "reason": reason,
        "matched_manuscript_id": matched_manuscript_id,
    }
    return FlagSeverity.low, reason, detail


def _passage_match_to_flag_draft(
    match: PassageMatch, *, first_upload: bool = False
) -> tuple[FlagSeverity, str, str, dict]:
    """Returns (severity, evidence_excerpt, page_anchor, detail) — a
    different shape from `_match_to_flag_draft` above (see the wording
    templates' own comment for why: real passage text as the excerpt,
    the explanatory sentence in `detail["reason"]` instead)."""
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
    return severity, match.own_text, page_anchor, detail


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
    embeddings = compute_document_embeddings(extraction, settings)

    # BUG-140: checked before anything else -- a content-hash match is a
    # CERTAIN "this is the same file" signal, unlike embedding similarity,
    # and it still works when there is no embeddable content to compare at
    # all (the branch immediately below, e.g. an image-only re-scan).
    hash_duplicate_id = (
        await same_instructor_hash_duplicate(session, manuscript_id, instructor_id, content_hash)
        if instructor_id is not None
        else None
    )

    if embeddings.whole_document is None:
        has_resubmission = hash_duplicate_id is not None
        result = CheckResult(
            check_run_id=check_run_id,
            criterion_id=None,
            kind=CheckKind.originality_reuse,
            outcome=ResultOutcome.passed if has_resubmission else ResultOutcome.not_applicable,
            detail={
                # `backend-critic` finding (BUG-140 review): 0 alongside a
                # real flag read as internally inconsistent -- the
                # embedding-based archive comparison never ran (nothing to
                # embed), but the hash lookup DID find one real comparable
                # entry, and cold-start disclosure exists precisely to be
                # honest about what was actually compared against.
                "archive_size_n": 1 if has_resubmission else 0,
                "n_flags": 1 if has_resubmission else 0,
                "note": "No embeddable content was extracted from this manuscript.",
            },
        )
        session.add(result)
        await session.flush()
        if has_resubmission:
            severity, reason, detail = _resubmission_flag_draft(hash_duplicate_id)
            session.add(
                Flag(
                    check_result_id=result.id,
                    severity=severity,
                    evidence_excerpt=reason,
                    page_anchor="whole document",
                    detail=detail,
                )
            )
        await session.commit()
        return result

    query_result = await query_similar_manuscripts(session, manuscript_id, embeddings, settings)
    # F7.4 (V-072): passage-level candidates alongside the whole-doc/chapter
    # ones above -- a genuinely different chunker (`compute_passage_embeddings`),
    # queried separately (`query_similar_passages` excludes reference-list/
    # block-quote passages by default, ticket AC3), but folded into the
    # SAME check_result (one F7 result per run, more flags at finer
    # granularity — same "flags at every matching level" precedent the
    # existing exact-duplicate test already documents for chapter vs
    # whole-doc).
    passages = compute_passage_embeddings(extraction, settings)
    passage_query_result = await query_similar_passages(session, manuscript_id, passages, settings)
    # BUG-097 (presentation-only remedy): computed once, applied to every
    # match this run produces — see `is_first_upload_for_instructor`'s own
    # docstring for why this changes wording/context only, never severity.
    first_upload = await is_first_upload_for_instructor(session, manuscript_id)

    # BUG-140: a same-instructor WHOLE-DOCUMENT exact duplicate (by hash
    # above, or by embedding similarity here if the hash check didn't
    # already find one -- e.g. a hash-less legacy row) is a resubmission
    # question, not a plagiarism verdict. Collapse it, and every chapter/
    # passage match against that SAME archived manuscript (the identical
    # finding restated at finer grain), into one low-severity confirmation
    # flag instead of the flood a re-upload otherwise produces (measured:
    # 82 high-severity flags from one re-upload, ticket's own evidence).
    resubmission_source_id = hash_duplicate_id
    if resubmission_source_id is None and instructor_id is not None:
        for match in query_result.matches:
            if (
                match.matched_chapter_title is None  # whole-document match only
                and match.level == "exact_duplicate"
                and match.matched_instructor_id == instructor_id
            ):
                resubmission_source_id = match.matched_manuscript_id
                break

    if resubmission_source_id is not None:
        whole_and_chapter_matches = [
            m for m in query_result.matches if m.matched_manuscript_id != resubmission_source_id
        ]
        passage_matches = [
            p
            for p in passage_query_result.matches
            if p.matched_manuscript_id != resubmission_source_id
        ]
    else:
        whole_and_chapter_matches = query_result.matches
        passage_matches = passage_query_result.matches

    n_flags = (
        len(whole_and_chapter_matches)
        + len(passage_matches)
        + (1 if resubmission_source_id is not None else 0)
    )

    result = CheckResult(
        check_run_id=check_run_id,
        criterion_id=None,
        kind=CheckKind.originality_reuse,
        outcome=ResultOutcome.passed,
        detail={
            "archive_size_n": query_result.archive_size_n,
            "passage_archive_size_n": passage_query_result.passage_archive_size_n,
            "n_flags": n_flags,
            "first_upload_context": first_upload,
        },
    )
    session.add(result)
    await session.flush()  # need result.id before attaching flags
    if resubmission_source_id is not None:
        severity, reason, detail = _resubmission_flag_draft(resubmission_source_id)
        session.add(
            Flag(
                check_result_id=result.id,
                severity=severity,
                evidence_excerpt=reason,
                page_anchor="whole document",
                detail=detail,
            )
        )
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
            detail["supporting_passage"] = _supporting_passage_detail(supporting)
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
        session.add(
            Flag(
                check_result_id=result.id,
                severity=severity,
                evidence_excerpt=evidence_excerpt,
                page_anchor=page_anchor,
                detail=detail,
            )
        )
    await session.commit()

    # Write-back AFTER the check + its flags are committed (ticket AC,
    # F7.3) — never match against self. Passage write-back follows the
    # same ordering for the same reason.
    #
    # BUG-140: SKIPPED for a recognized same-instructor resubmission -- the
    # ticket's own fix item 1 ("a byte-identical re-upload ... must never
    # become an independent archive entry that later reads as a rival
    # submission") means archiving it AGAIN is exactly the mistake, not
    # just the flags it produces. Two compounding reasons this matters
    # beyond tidiness: (1) every prior fix attempt that only suppressed
    # flags left the archive growing unboundedly with each resubmission,
    # so the instructor's OWN duplicate pile keeps getting bigger forever;
    # (2) `_best_chapter_matches`/`_best_passage_match_for` each run an
    # independent top-1 HNSW query with no similarity tiebreak
    # (`backend-critic` finding, BUG-140 review) -- with MULTIPLE
    # byte-identical same-instructor archive entries (production's own
    # unrecovered pre-fix pollution, ticket item 4), a chapter/passage
    # sub-query could resolve its tie to a DIFFERENT sibling id than the
    # one whole-document match picked as `resubmission_source_id`, letting
    # that one flag slip past the suppression above. Never writing a NEW
    # duplicate keeps that risk from growing past today's fixed, already-
    # disclosed legacy set instead of compounding with every future
    # resubmission.
    if resubmission_source_id is None:
        await store_document_embeddings(session, manuscript_id, embeddings)
        if passages:
            await store_passage_embeddings(session, manuscript_id, passages, settings)

    return result


async def existing_originality_reuse_result(
    session: AsyncSession, check_run_id: int
) -> CheckResult | None:
    """Idempotency guard (ENGINEERING §4) — a resumed run must not
    re-query-and-reflag (or double-write-back) work it already did."""
    return await session.scalar(
        select(CheckResult).where(
            CheckResult.check_run_id == check_run_id,
            CheckResult.kind == CheckKind.originality_reuse,
        )
    )
