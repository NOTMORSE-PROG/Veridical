"""F8: source-placement qualifications shared by print and reading surfaces."""

import json
from pathlib import Path

_COPY = json.loads(
    (Path(__file__).parent / "data" / "evidence_source_copy.json").read_text("utf-8")
)
TEXT_LAYER_CAPTION = _COPY["text_layer_match"]
PAGE_ONLY_CAPTION = _COPY["page_only"]
SCOPE_FIRST_PAGE_CAPTION = _COPY["scope_first_page"]
DOCX_CAPTION = _COPY["docx"]
PLACEMENT_UNAVAILABLE = _COPY["unavailable"]
SOURCE_ROLE_LABELS = _COPY["roles"]


def placement_caption(state, reason):
    if state == "text_layer_match":
        return TEXT_LAYER_CAPTION
    if reason == "scope_first_page":
        return SCOPE_FIRST_PAGE_CAPTION
    if state == "page_only":
        return PAGE_ONLY_CAPTION
    if state == "paragraph_only":
        return _COPY["paragraph_only"]
    return PLACEMENT_UNAVAILABLE.get(reason, PLACEMENT_UNAVAILABLE["location_unavailable"])


def reader_location_caption(state, reason):
    if state == "unavailable":
        return _COPY["reader_unavailable"].get(
            reason, _COPY["reader_unavailable"]["location_unavailable"]
        )
    return placement_caption(state, reason)
