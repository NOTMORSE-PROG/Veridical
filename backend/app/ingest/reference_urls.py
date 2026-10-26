"""Recover printed URLs from text and bounded PDF evidence, never by fetching."""

import re
from urllib.parse import urlsplit

from app.ingest.schemas import TextBlock, UriLink
from app.models.citation import Citation

_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:)"
# Storage capacity, not an authenticity threshold. Never persist a truncated URL.
_URL_STORAGE_CHARS = Citation.__table__.c.url.type.length


def supported_reference_url(uri: str) -> bool:
    if len(uri) > _URL_STORAGE_CHARS:
        return False
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in uri):
        return False
    if "\\" in uri:
        return False
    try:
        parsed = urlsplit(uri)
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and parsed.port != 0
        )
    except ValueError:
        return False


def text_url(raw: str) -> str | None:
    """No guess at a line-final URL followed by more text: its suffix is unknown."""
    match = _URL.search(raw)
    if match is None:
        return None
    suffix = raw[match.end() :]
    # A wrap after the token can belong to either the URL or ordinary prose.
    # Keep the line boundary until this decision; do not first flatten it away.
    if re.match(r"[^\S\r\n]*[\r\n]+\s*\S", suffix):
        return None
    uri = match.group().rstrip(_TRAILING_PUNCT)
    return uri if supported_reference_url(uri) else None


def _contains(block: TextBlock, link: UriLink, tolerance: float) -> bool:
    if block.page != link.page or block.bbox is None:
        return False
    outer, inner = block.bbox, link.bbox
    return (
        outer[0] - tolerance <= inner[0]
        and outer[1] - tolerance <= inner[1]
        and inner[2] <= outer[2] + tolerance
        and inner[3] <= outer[3] + tolerance
    )


def reference_urls(
    groups: list[list[TextBlock]],
    links: list[UriLink] | None,
    *,
    complete: bool,
    tolerance: float,
) -> list[str | None]:
    """Only one entry can own a recovery; sliced/ambiguous geometry abstains.

    Each URI must agree with all its ordered visible fragments AND a unique
    occurrence in that entry. A link labelled 'website' cannot supply a URL.
    This proves printed identity only, never authenticity or availability.
    """
    raw_entries = ["\n".join(block.text for block in group) for group in groups]
    resolved = [text_url(raw) for raw in raw_entries]
    if not links or not complete:
        return resolved
    bound: dict[int, list[UriLink]] = {}
    conflicted: set[int] = set()
    for link in links:
        owners = [
            index
            for index, group in enumerate(groups)
            if any(_contains(block, link, tolerance) for block in group)
        ]
        if len(owners) != 1:
            conflicted.update(owners)
            continue
        bound.setdefault(owners[0], []).append(link)
    for index, group in enumerate(groups):
        if resolved[index] is not None:
            continue
        if index in conflicted or any(block.page is None or block.bbox is None for block in group):
            continue
        candidates = bound.get(index, [])
        destinations = {link.uri for link in candidates}
        if len(destinations) != 1:
            continue
        uri = next(iter(destinations))
        if not supported_reference_url(uri):
            continue
        ordered = sorted(candidates, key=lambda item: (item.page, item.bbox[1], item.bbox[0]))
        visible = "".join(re.sub(r"\s+", "", link.text) for link in ordered)
        if visible != uri and visible.rstrip(_TRAILING_PUNCT) != uri:
            continue
        compact = re.sub(r"\s+", "", raw_entries[index])
        if compact.count(uri) != 1:
            continue
        first = re.search(r"https?://\S*", raw_entries[index], re.IGNORECASE)
        if first is None or not uri.startswith(first.group().rstrip(_TRAILING_PUNCT)):
            continue
        # Visible/annotation text must not be just a prefix of a longer printed
        # URL, or a different occurrence elsewhere in the same reference.
        positions = [offset for offset, char in enumerate(raw_entries[index]) if not char.isspace()]
        start = compact.index(uri)
        end = positions[start + len(uri) - 1] + 1
        if positions[start] != first.start():
            continue
        following = raw_entries[index][end:]
        # Punctuation can be part of a longer URL. It terminates this printed
        # locator only as a final punctuation run before whitespace or EOF.
        if following and not re.match(r"[.,;:)]+(?:\s|$)|\s", following):
            continue
        if re.match(r"[^\S\r\n]*[\r\n]+\s*\S", following.lstrip(_TRAILING_PUNCT)):
            continue
        resolved[index] = uri
    return resolved
