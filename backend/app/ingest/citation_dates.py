"""Explicit citation-date syntax shared by reference and in-text extraction."""

import re
from functools import lru_cache

from app.config import get_settings
from app.ingest.reference_metadata_patterns import load_reference_metadata_patterns


@lru_cache
def compiled_dates(source):
    return {
        "reference": re.compile(rf"\(({source})(?:,[^)]*)?\)"),
        "entry": re.compile(rf"^[^()\n]+\({source}(?:,[^)]*)?\)\.\s*\S"),
        "group": re.compile(source),
        "parenthetical": re.compile(rf",\s*(?P<year>{source})(?=,|\s*$)"),
        "narrative": re.compile(rf"\((?P<year>{source})"),
    }


def date_patterns(settings=None):
    settings = settings or get_settings()
    return compiled_dates(
        load_reference_metadata_patterns(settings.reference_metadata_patterns_file).date_source
    )


def citation_date_key(token):
    return token[:4] if token[:4].isdigit() else "explicit_no_date"


def reference_date_key(reference):
    if reference.year is not None:
        cleaned_year = str(reference.year).lower().replace(" ", "")
        if cleaned_year in ("n.d.", "nd"):
            return "explicit_no_date"
        return str(reference.year)
    match = date_patterns()["reference"].search(reference.raw_text)
    if match and not match.group(1)[:4].isdigit():
        return "explicit_no_date"
    return None
