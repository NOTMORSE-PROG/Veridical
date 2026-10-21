"""Current guidance about unchanged saved title evidence (F5/F8, V-081)."""

import hashlib
import re
from typing import Literal

from app.checks.citations.projection import SOURCE_FIELDS
from app.config import get_settings
from app.ingest.citation_dates import date_patterns
from app.ingest.reference_metadata_patterns import load_reference_metadata_patterns
from app.ingest.references import ieee_quoted_author_prefix_supported

CitationMetadataReviewWarning = Literal["quoted_title_projection_unverified"]


def citation_metadata_review_warning(
    check_kind, raw, detail
) -> CitationMetadataReviewWarning | None:
    """Only saved data: no source/provider read or historical-record rewrite.

    Recognizing a leading quotation is not validation of its parsed title.
    Missing proof means unverified, never that every recorded field is wrong.
    """
    if not isinstance(raw, str) or not isinstance(detail, dict):
        return None
    if check_kind != "citation_integrity" or detail.get("kind") != "possible_metadata_mismatch":
        return None
    comparison = detail.get("metadata_comparison")
    if not isinstance(comparison, dict):
        comparison = {}
    field_lists = [
        values["differing_fields"]
        for values in (comparison, detail)
        if "differing_fields" in values
    ]
    known_fields = {"title", "year", "first author"}
    if field_lists and all(
        isinstance(fields, list)
        and fields
        and all(isinstance(field, str) and field in known_fields for field in fields)
        and "title" not in fields
        for fields in field_lists
    ):
        return None
    patterns, first = None, None
    try:
        patterns = load_reference_metadata_patterns(get_settings().reference_metadata_patterns_file)
        quote_chars = {char for pair in patterns.quoted_title_pairs for char in pair}
        first = next((i for i, char in enumerate(raw) if char in quote_chars), None)
        candidate = first is not None and _leading_title_candidate(raw[:first], patterns)
    except (OSError, ValueError, UnicodeError):
        # The policy needed to interpret a historical quoted boundary cannot
        # be applied. Do not silently certify that title comparison.
        import unicodedata

        candidate = any("QUOTATION MARK" in unicodedata.name(char, "") for char in raw)
    if not candidate:
        return None
    proof = detail.get("source_projection")
    if _valid_saved_projection(raw, comparison, proof, patterns, first):
        return None
    return "quoted_title_projection_unverified"


def _leading_title_candidate(prefix, patterns):
    trimmed = prefix.rstrip()
    end = len(trimmed)
    while end and (
        trimmed[end - 1].isspace() or trimmed[end - 1] in patterns.quoted_title_separators
    ):
        end -= 1
    dated = trimmed[:end]
    if dated.endswith(")"):
        start = dated.rfind("(")
        author = dated[:start] if start >= 0 else ""
        if (
            any(char.isalpha() for char in author)
            and not any(char in author for char in "()")
            and date_patterns()["reference"].fullmatch(dated[start:])
        ):
            return True
    return ieee_quoted_author_prefix_supported(trimmed)


def _valid_saved_projection(raw, comparison, proof, patterns, first):
    if not isinstance(proof, dict):
        return False
    status = proof.get("status")
    if (
        not isinstance(status, str)
        or proof.get("schema_version") != "source-quoted-metadata-v1"
        or status
        not in {
            "current",
            "applied",
        }
    ):
        return False
    if proof.get("raw_text_sha256") != hashlib.sha256(raw.encode("utf-8")).hexdigest():
        return False
    for name in ("parser_config_sha256", "source_inventory_sha256"):
        value = proof.get(name)
        if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value):
            return False
    if not isinstance(proof.get("parser_version"), str) or not proof["parser_version"].strip():
        return False
    start, end = proof.get("title_start"), proof.get("title_end")
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(raw):
        return False
    if patterns is None or first is None:
        return False
    pairs = dict(patterns.quoted_title_pairs)
    quote_chars = {char for pair in patterns.quoted_title_pairs for char in pair}
    close = next((i for i in range(first + 1, len(raw)) if raw[i] in quote_chars), None)
    if close is None or pairs.get(raw[first]) != raw[close] or not first < start < end <= close:
        return False
    if any(char in quote_chars for char in raw[close + 1 :]):
        return False
    if raw[first + 1 : start].strip() or any(
        not char.isspace() and char not in patterns.quoted_title_terminal_delimiters
        for char in raw[end:close]
    ):
        return False
    original, effective, cited = (
        proof.get("original_fields"),
        proof.get("effective_fields"),
        comparison.get("cited"),
    )
    if not all(isinstance(values, dict) for values in (original, effective, cited)):
        return False
    for values in (original, effective):
        if not all(key in values for key in SOURCE_FIELDS):
            return False
        if type(values["order_index"]) is not int or not isinstance(values["raw_text"], str):
            return False
        if values["year"] is not None and type(values["year"]) is not int:
            return False
        authors = values["authors"]
        if authors is not None and (
            not isinstance(authors, list)
            or not all(isinstance(author, str) and author.strip() for author in authors)
        ):
            return False
        if any(
            values[key] is not None and not isinstance(values[key], str)
            for key in ("title", "doi", "isbn", "venue", "url")
        ):
            return False
        if values["parse_status"] not in ("parsed", "parse_failed"):
            return False
    order = proof.get("reference_order_index")
    if type(order) is not int or order < 0:
        return False
    return (
        original.get("raw_text") == effective.get("raw_text") == raw
        and original.get("order_index") == effective.get("order_index") == order
        and original.get("parse_status") == effective.get("parse_status") == "parsed"
        and raw[start:end] == effective.get("title") == cited.get("title")
        and all(
            original.get(key) == effective.get(key)
            for key in ("authors", "year", "doi", "isbn", "url")
        )
    )
