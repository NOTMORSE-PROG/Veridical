"""Report projection of the citation recency note (V-092).

Reads only what the run's citation-integrity result recorded, so an old
report keeps describing the window it was checked against even after the
format's setting changes."""

from typing import Any

from pydantic import ValidationError

from app.report.schemas import CitationRecencySummaryOut


def _count(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def _year(value: Any) -> int | None:
    return value if type(value) is int else None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def recency_summary(detail: dict[str, Any] | None) -> CitationRecencySummaryOut | None:
    saved = (detail or {}).get("citation_recency")
    if not isinstance(saved, dict) or saved.get("status") not in {"assessed", "unassessed", "off"}:
        return None
    evidence = saved.get("reference_year_evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    sources = saved.get("setting_sources")
    reasons = saved.get("year_not_readable_reasons")
    rows = saved.get("sources")
    try:
        return CitationRecencySummaryOut(
            status=saved["status"],
            unassessed_reason=_text(saved.get("unassessed_reason")),
            enabled_source=sources.get("enabled") if isinstance(sources, dict) else None,
            window_years=_year(saved.get("window_years")),
            window_source=sources.get("window_years") if isinstance(sources, dict) else None,
            counts_toward_readiness=saved.get("counts_toward_readiness")
            if isinstance(saved.get("counts_toward_readiness"), bool)
            else None,
            reference_year=_year(saved.get("reference_year")),
            reference_year_basis=saved.get("reference_year_basis"),
            reference_year_evidence_text=_text(evidence.get("text")),
            reference_year_evidence_anchor=_text(evidence.get("anchor")),
            reference_year_fallback_reason=_text(saved.get("reference_year_fallback_reason")),
            oldest_year_in_window=_year(saved.get("oldest_year_in_window")),
            n_references=_count(saved.get("n_references")),
            n_dated=_count(saved.get("n_dated")),
            n_outside_window=_count(saved.get("n_outside_window")),
            n_within_window=_count(saved.get("n_within_window")),
            n_dated_after_reference_year=_count(saved.get("n_dated_after_reference_year")),
            n_year_not_readable=_count(saved.get("n_year_not_readable")),
            year_not_readable_reasons={
                str(key): _count(value)
                for key, value in (reasons.items() if isinstance(reasons, dict) else ())
                if _count(value)
            },
            year_not_readable_reference_numbers=[
                row["reference_order_index"] + 1
                for row in (rows if isinstance(rows, list) else ())
                if isinstance(row, dict)
                and row.get("status") == "year_not_readable"
                and type(row.get("reference_order_index")) is int
                and row["reference_order_index"] >= 0
            ],
        )
    except ValidationError:
        # A malformed saved record is not shown rather than shown wrong.
        return None


_FALLBACK_NOTES = {
    "manuscript_year_not_found": "The title page did not state a year, so the check date was used.",
    "manuscript_year_ambiguous": (
        "The title page states more than one year, so the check date was used."
    ),
    "manuscript_year_after_check_date": (
        "The title page year is later than the check date, so the check date was used."
    ),
    "manuscript_year_precedes_cited_source": (
        "The manuscript cites work published after its title page year, so the check date was used."
    ),
}


_UNASSESSED_CAUSES = {
    "reference_list_not_bound": (
        "the reference list could not be matched to the document reliably enough to date "
        "its entries"
    ),
    "reference_membership_partial": "only part of the reference list could be told apart",
    "reference_list_unavailable": "the reference list could not be located reliably",
    "source_unavailable": "the document's text could not be read reliably",
    "no_references": "no reference list entries were found",
    "format_setting_invalid": "the format's saved recency setting is not valid",
    "recency_policy_unavailable": "the recency reading rules could not be loaded",
}


def recency_sentences(summary: CitationRecencySummaryOut | None) -> list[str]:
    """Plain sentences for the exported report. Counts only."""
    if summary is None:
        return []
    if summary.status == "off":
        # A format that never turned the note on gets no line about it.
        return (
            ["Source recency: not checked; this format turns the recency note off."]
            if summary.enabled_source == "format"
            else []
        )
    if summary.status == "unassessed":
        cause = _UNASSESSED_CAUSES.get(
            summary.unassessed_reason or "", "the reference list could not be checked"
        )
        return [f"Source recency: not assessed, because {cause}."]
    window = summary.window_years
    basis = (
        f"the year on the manuscript's title page ({summary.reference_year_evidence_text}, "
        f"{summary.reference_year_evidence_anchor})"
        if summary.reference_year_basis == "manuscript_year"
        and summary.reference_year_evidence_text
        and summary.reference_year_evidence_anchor
        else "the date of this check"
    )
    dated = summary.n_dated
    sentences = [
        f"Source recency: {summary.n_outside_window} of {dated} "
        f"{'source' if dated == 1 else 'sources'} with a readable year "
        f"{'is' if summary.n_outside_window == 1 else 'are'} older than the {window}-year "
        f"window (published before {summary.oldest_year_in_window}). The window counts back "
        f"from {summary.reference_year}, {basis}."
    ]
    fallback = _FALLBACK_NOTES.get(summary.reference_year_fallback_reason or "")
    if fallback:
        sentences.append(fallback)
    if summary.n_year_not_readable:
        count = summary.n_year_not_readable
        sentences.append(
            f"{count} {'source has' if count == 1 else 'sources have'} no readable year "
            f"and {'was' if count == 1 else 'were'} not judged for recency."
        )
    sentences.append(
        "These notes count toward readiness under this format."
        if summary.counts_toward_readiness
        else "These notes do not count toward readiness."
    )
    return sentences
