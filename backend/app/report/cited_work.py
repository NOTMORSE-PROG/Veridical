"""The cited work a citation-integrity finding is about (BUG-517).

Several findings can share one sentence as their evidence excerpt; the
citation as written tells them apart without parsing the explanation text."""

from typing import Any


def cited_work(detail: dict[str, Any], evidence_excerpt: str) -> dict[str, Any]:
    kind = detail.get("kind")
    if kind == "orphan_in_text_citation":
        text = detail.get("raw_text")
    elif kind == "uncited_reference":
        text = evidence_excerpt
    else:
        return {"cited_text": None, "cited_authors": None, "cited_year": None}
    authors = detail.get("surnames", detail.get("authors"))
    year = detail.get("year")
    return {
        "cited_text": text.strip() if isinstance(text, str) and text.strip() else None,
        "cited_authors": [name for name in authors if isinstance(name, str) and name.strip()]
        or None
        if isinstance(authors, list)
        else None,
        "cited_year": str(year).strip()
        if isinstance(year, (str, int)) and not isinstance(year, bool) and str(year).strip()
        else None,
    }
