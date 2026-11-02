"""Conservative readiness eligibility shared by persistence and presentation."""

from typing import Any


def exclusion_reason(kind: str, detail: dict[str, Any] | None = None) -> str | None:
    if kind == "originality_reuse":
        return "comparison_lead_not_scored"
    if kind == "internal_agreement":
        # Statement-role extraction has not established dependable own-study
        # attribution. Located quotes alone do not resolve that limitation.
        return "statement_roles_unverified"
    if kind == "citation_integrity" and (detail or {}).get("kind") == "unverifiable_api_down":
        # Older unavailable-lookup flags predate the explicit eligibility marker.
        return "unassessed_evidence"
    if (
        kind == "citation_integrity"
        and (detail or {}).get("kind") == "outside_recency_window"
        and (detail or {}).get("affects_readiness") is False
    ):
        # The format keeps recency as a note for the instructor, not a
        # readiness finding. The evidence itself was read.
        return "recency_note_not_counted"
    if kind == "citation_integrity" and (detail or {}).get("kind") == "retraction_notice_cited":
        # Citing a retraction notice is not citing a retracted work.
        return "retraction_notice_not_counted"
    if kind == "citation_integrity" and (detail or {}).get("kind") == (
        "possible_retraction_unconfirmed"
    ):
        # OpenAlex's retraction flag is a lead; only Crossref's record counts.
        return "retraction_lead_not_counted"
    if (detail or {}).get("affects_readiness") is False:
        return "unassessed_evidence"
    return None


# BUG-662: why a record was taken for another upload of the same work, as
# shown beside its number in the lead.
_SAME_WORK_BASIS = {
    "same_file": "same file",
    "near_identical_text": "nearly identical text",
    "same_file_name": "similar text, same file name",
    "same_title": "similar text, same title",
    "same_team": "similar text, same team name",
    "same_authors": "similar text, same authors",
}
# A near-exact duplicate whose own records name another title, team or
# authors: the instructor is asked which it is, not told it is the same work.
_NAMES_DIFFER = "near_identical_text_names_differ"


def _record_list(refs: list[int]) -> str:
    named = [f"#{ref}" for ref in refs]
    return named[0] if len(named) == 1 else f"{', '.join(named[:-1])} and {named[-1]}"


def _records(refs: list[int]) -> str:
    return f"record{'s' if len(refs) > 1 else ''} {_record_list(refs)}"


def _same_work_reason(detail: dict[str, Any]) -> str:
    """Another upload of the same work in the instructor's own Library. Says
    what was seen and what was done, never that anything was copied."""
    sources = [
        s
        for s in detail.get("same_work") or []
        if isinstance(s, dict) and isinstance(s.get("manuscript_id"), int)
    ]
    differ = [s["manuscript_id"] for s in sources if s.get("basis") == _NAMES_DIFFER]
    same = [s for s in sources if s.get("basis") != _NAMES_DIFFER]
    refs = [s["manuscript_id"] for s in same]
    if not sources:
        ids = detail.get("matched_manuscript_ids")
        refs = [r for r in ids if isinstance(r, int)] if isinstance(ids, list) else []
        if not refs and isinstance(detail.get("matched_manuscript_id"), int):
            refs = [detail["matched_manuscript_id"]]

    sentences = []
    if refs or not differ:
        many = len(refs) > 1
        why = "; ".join(
            f"#{s['manuscript_id']}: {_SAME_WORK_BASIS[s['basis']]}"
            for s in same
            if s.get("basis") in _SAME_WORK_BASIS
        )
        sentences.append(
            "Possible other upload of this same work in your own Library records: "
            f"{_records(refs) if refs else 'a record'} {'appear' if many else 'appears'} to be "
            f"this manuscript or an earlier version of it{f' ({why})' if why else ''}."
        )
        sentences.append(
            "If any of them is a different team's work, compare this manuscript with it directly."
            if many
            else "If it is a different team's work, compare the two documents directly."
        )
    if differ:
        many = len(differ) > 1
        sentences.append(
            f"Your own Library {_records(differ)} {'have' if many else 'has'} nearly identical "
            f"text, but {'their' if many else 'its'} recorded title, team or authors differ "
            "from this upload's. Please confirm whether "
            f"{'each' if many else 'it'} is this manuscript or another team's work."
        )
    total = len(refs) + len(differ)
    sentences.append(
        f"Text matching {'these records' if total > 1 else 'it'} is not listed as separate "
        "leads. This lead does not change readiness."
    )
    return " ".join(sentences)


def reuse_reason(detail: dict[str, Any]) -> str:
    if detail.get("kind") == "reuse_same_instructor_resubmission":
        return _same_work_reason(detail)
    ref = detail.get("matched_manuscript_id")
    target = f"Library record #{ref}" if isinstance(ref, int) else "a Library record"
    chapter = detail.get("own_chapter_title")
    location = f' in your section "{chapter}"' if isinstance(chapter, str) and chapter else ""
    return (
        f"Comparison lead{location} against {target}. Compare the source passages in context; "
        "similarity alone does not establish copying, authorship, or which document came first. "
        "This lead does not change readiness."
    )


def is_reuse_summary(kind: str | None) -> bool:
    """Producer-defined generated summaries, not a passage-presence heuristic."""
    return isinstance(kind, str) and kind in {
        "reuse_exact_duplicate",
        "reuse_high_similarity",
        "reuse_exact_duplicate_chapter",
        "reuse_high_similarity_chapter",
        "reuse_same_instructor_resubmission",
    }


def reuse_excerpt(detail: dict[str, Any], recorded_excerpt: str) -> str:
    """Replace only known generated summaries, never a passage or unknown legacy quote."""
    if is_reuse_summary(detail.get("kind")):
        return reuse_reason(detail)
    return recorded_excerpt


def reuse_reasoning(detail: dict[str, Any], recorded_excerpt: str) -> str | None:
    recorded = detail.get("reasoning") or detail.get("reason")
    if recorded == recorded_excerpt:
        return None
    if str(detail.get("kind", "")).startswith("reuse_"):
        return reuse_reason(detail)
    return recorded


def problem_kind_of(detail: dict[str, Any] | None) -> str | None:
    """The kind a finding is shown and grouped as. An undecided agreement pair
    whose text also looked like it addressed a grader is shown as that
    signal, so it is never folded into the undecided pairs (BUG-160)."""
    kind = (detail or {}).get("kind")
    if kind == "agreement_cannot_determine" and (detail or {}).get("injection_suspected"):
        return "agreement_injection_suspected"
    return kind
