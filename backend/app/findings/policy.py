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
    if (detail or {}).get("affects_readiness") is False:
        return "unassessed_evidence"
    return None


def reuse_reason(detail: dict[str, Any]) -> str:
    ref = detail.get("matched_manuscript_id")
    target = f"Library record #{ref}" if isinstance(ref, int) else "a Library record"
    if detail.get("kind") == "reuse_same_instructor_resubmission":
        target = f"one of your own Library records (#{ref})"
    chapter = detail.get("own_chapter_title")
    location = f' in your section "{chapter}"' if isinstance(chapter, str) and chapter else ""
    return (
        f"Comparison lead{location} against {target}. Compare the source passages in context; "
        "similarity alone does not establish copying, authorship, or which document came first. "
        "This lead does not change readiness."
    )


def reuse_excerpt(detail: dict[str, Any], recorded_excerpt: str) -> str:
    """Replace only known generated summaries, never a passage or unknown legacy quote."""
    if detail.get("kind") in {
        "reuse_exact_duplicate",
        "reuse_high_similarity",
        "reuse_exact_duplicate_chapter",
        "reuse_high_similarity_chapter",
        "reuse_same_instructor_resubmission",
    }:
        return reuse_reason(detail)
    return recorded_excerpt


def reuse_reasoning(detail: dict[str, Any], recorded_excerpt: str) -> str | None:
    recorded = detail.get("reasoning") or detail.get("reason")
    if recorded == recorded_excerpt:
        return None
    if str(detail.get("kind", "")).startswith("reuse_"):
        return reuse_reason(detail)
    return recorded
