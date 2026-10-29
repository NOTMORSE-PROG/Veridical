"""F1.5/F5: source-bound, run-local quoted metadata repair (V-081)."""

import hashlib
import json
from dataclasses import dataclass, replace

from app.config import get_settings
from app.ingest.reference_metadata_patterns import load_reference_metadata_patterns
from app.ingest.references import extract_references, quoted_title_span
from app.models.enums import CitationParseStatus

SOURCE_FIELDS = (
    "order_index",
    "raw_text",
    "authors",
    "title",
    "year",
    "doi",
    "isbn",
    "venue",
    "url",
    "parse_status",
)
_REPAIR_FIELDS = frozenset({"title", "venue", "parse_status"})


@dataclass(frozen=True)
class CitationView:
    id: int | None
    manuscript_id: int
    order_index: int
    raw_text: str
    authors: list[str] | None
    title: str | None
    year: int | None
    doi: str | None
    isbn: str | None
    venue: str | None
    url: str | None
    parse_status: CitationParseStatus


def _fields(citation):
    return {name: getattr(citation, name) for name in SOURCE_FIELDS}


def _view(citation, draft=None):
    fields = _fields(draft or citation)
    if fields["authors"] is not None:
        fields["authors"] = list(fields["authors"])
    return CitationView(citation.id, citation.manuscript_id, **fields)


def _same(left, right):
    return all(getattr(left, name) == getattr(right, name) for name in SOURCE_FIELDS)


def project_quoted_metadata(citations, extraction, patterns, *, settings=None):
    """Never modify ORM rows or historical findings.

    Admission requires the complete retained primary source inventory and all persisted
    derived fields to match a current draft or the precise former parser view.
    Only title/venue corrections (or a downgrade to parse_failed) are admitted.
    Identifier recovery uses the identical extraction path for both views.
    """
    try:
        active_settings = get_settings()
        if settings is not None and any(
            getattr(settings, name) != getattr(active_settings, name)
            for name in (
                "reference_metadata_patterns_file",
                "reference_metadata_max_chars",
                "reference_unseparated_author_max_tokens",
                "reference_doi_max_geometry_comparisons",
            )
        ):
            # Extraction helpers share one process configuration. A divergent
            # caller override cannot be recorded as the policy they executed.
            raise ValueError("Reference parser settings differ from active extraction settings")
        metadata = load_reference_metadata_patterns(
            active_settings.reference_metadata_patterns_file
        )
    except (OSError, ValueError, UnicodeError):
        views = [
            replace(_view(c), parse_status=CitationParseStatus.parse_failed) for c in citations
        ]
        return views, {
            c.order_index: {
                "schema_version": "source-quoted-metadata-v1",
                "status": "unassessed",
                "reason": "reference_metadata_policy_unavailable",
                "citation_id": c.id,
                "original_fields": _fields(c),
                "effective_fields": _fields(view),
            }
            for c, view in zip(citations, views, strict=True)
        }
    quote_chars = {char for pair in metadata.quoted_title_pairs for char in pair}
    fresh = extract_references(extraction, patterns)
    candidates = {
        c.order_index for c in [*citations, *fresh] if any(ch in quote_chars for ch in c.raw_text)
    }
    if not candidates:
        return citations, {}
    legacy = extract_references(extraction, patterns, _legacy_quoted_titles=True)
    bound = bool(
        fresh
        and len(fresh) == len(legacy) == len(citations)
        and all(
            stored.order_index == new.order_index == old.order_index
            and stored.raw_text == new.raw_text == old.raw_text
            for stored, new, old in zip(citations, fresh, legacy, strict=True)
        )
    )
    if bound:
        for stored, new, old in zip(citations, fresh, legacy, strict=True):
            if _same(stored, new):
                continue
            changed = {name for name in SOURCE_FIELDS if getattr(new, name) != getattr(old, name)}
            downgrade = new.parse_status == CitationParseStatus.parse_failed
            if not (
                _same(stored, old)
                and changed <= _REPAIR_FIELDS
                and ("parse_status" not in changed or downgrade)
            ):
                bound = False
                break
    inventory_sha = hashlib.sha256(
        json.dumps(
            [(draft.order_index, draft.raw_text) for draft in fresh], ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    views, records = [], {}
    if not bound:
        candidates.update(c.order_index for c in citations)
    for index, stored in enumerate(citations):
        if stored.order_index not in candidates:
            views.append(_view(stored, fresh[index]) if bound else _view(stored))
            continue
        view = (
            _view(stored, fresh[index])
            if bound
            else replace(_view(stored), parse_status=CitationParseStatus.parse_failed)
        )
        views.append(view)
        span = None
        if bound and view.parse_status == CitationParseStatus.parsed:
            start = next(i for i, ch in enumerate(stored.raw_text) if ch in quote_chars)
            try:
                candidate = quoted_title_span(stored.raw_text, start)
                if candidate and stored.raw_text[candidate.start : candidate.end] == view.title:
                    span = candidate
            except ValueError:
                pass
        records[stored.order_index] = {
            "schema_version": "source-quoted-metadata-v1",
            "parser_version": metadata.version,
            "parser_config_sha256": metadata.sha256,
            "citation_id": stored.id,
            "reference_order_index": stored.order_index,
            "raw_text_sha256": hashlib.sha256(stored.raw_text.encode("utf-8")).hexdigest(),
            "source_inventory_sha256": inventory_sha,
            "status": "current"
            if bound and _same(stored, view)
            else "applied"
            if bound
            else "unassessed",
            "reason": None if bound else "reference_source_projection_unbound",
            "title_start": span.start if span else None,
            "title_end": span.end if span else None,
            "original_fields": _fields(stored),
            "effective_fields": _fields(view),
        }
    return views, records
