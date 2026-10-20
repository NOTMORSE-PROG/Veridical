"""Printed DOI wrap recovery from original lines plus corroborating PDF links.

No network access, approximate text matching or generated source coordinates.
Only physical line separators can be joined. Ordinary spaces and non-ASCII
characters retain their identity; a DOI proxy is corroboration, not verification.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.ingest.schemas import ExtractionResult, TextBlock, UriLink
from app.ingest.source_slices import SourceFragment, line_inventory_valid
from app.models.citation import Citation

_DOI = re.compile(r"10\.\d{4,9}/[^\s]+")
_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_LINE_JOIN = re.compile(r"[ \t]*\r?\n[ \t]*")
_DOI_STORAGE_CHARS = Citation.__table__.c.doi.type.length
_FINAL_PUNCTUATION = re.compile(r"[.,;:)]+(?:\s|$)")


@dataclass(frozen=True)
class DoiRecovery:
    doi: str
    # Offsets into "\n".join(group block texts), not normalized stored raw_text.
    start: int
    end: int


@dataclass(frozen=True)
class _Line:
    block_index: int
    source_id: str | None
    page: int
    bbox: tuple[float, float, float, float]
    text: str


def _proxy_doi(uri: str, hosts: frozenset[str]) -> str | None:
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in uri) or "\\" in uri:
        return None
    try:
        parsed = urlsplit(uri)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in hosts
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
        ):
            return None
        doi = parsed.path.removeprefix("/")
    except ValueError:
        return None
    # Percent-encoded identifiers need a separate source/encoding contract.
    if (
        "%" in doi
        or any(segment in {".", ".."} for segment in doi.split("/"))
        or len(doi) > _DOI_STORAGE_CHARS
        or not _DOI.fullmatch(doi)
    ):
        return None
    return doi


def _owns(line: _Line, link: UriLink, tolerance: float) -> bool:
    """Font-leading can extend above a line; its centre must belong to only one line."""
    centre_y = (link.bbox[1] + link.bbox[3]) / 2
    return (
        line.page == link.page
        and line.bbox[1] <= centre_y <= line.bbox[3]
        and line.bbox[0] - tolerance <= link.bbox[0]
        and link.bbox[2] <= line.bbox[2] + tolerance
    )


def _printed_span(raw: str, doi: str) -> DoiRecovery | None:
    # Index-preserving removal of actual line separators only, never every
    # whitespace character. The target proxy must corroborate the whole name.
    removed = {index for match in _LINE_JOIN.finditer(raw) for index in range(*match.span())}
    positions = [index for index in range(len(raw)) if index not in removed]
    joined = "".join(raw[index] for index in positions)
    key, wanted = joined.translate(_ASCII_FOLD), doi.translate(_ASCII_FOLD)
    starts = [match.start() for match in re.finditer(re.escape(wanted), key)]
    if len(starts) != 1:
        return None
    start, end = starts[0], starts[0] + len(doi)
    if start and (joined[start - 1].isalnum() or joined[start - 1] == "_"):
        return None
    following = joined[end:]
    if following and not following[0].isspace() and not _FINAL_PUNCTUATION.match(following):
        return None
    source_start, source_end = positions[start], positions[end - 1] + 1
    source_following = raw[source_end:].lstrip(".,;:)")
    if re.match(r"[^\S\r\n]*[\r\n]+\s*\S", source_following):
        return None
    if "\n" not in raw[source_start:source_end]:
        return None  # complete single-line identifiers use the existing parser
    return DoiRecovery(joined[start:end], source_start, source_end)


def recover_reference_dois(
    result: ExtractionResult,
    groups: list[list[TextBlock]],
    *,
    resolver_hosts: frozenset[str],
    tolerance: float,
    max_chars: int,
    max_comparisons: int,
) -> list[DoiRecovery | None]:
    """Recover only one uniquely printed, fully corroborated DOI per entry."""
    empty: list[DoiRecovery | None] = [None for _ in groups]
    if not result.uri_links_complete or not result.uri_links:
        return empty
    lines: dict[tuple[int, str], _Line] = {}
    page_lines: dict[int, list[_Line]] = {}
    known_ids: set[str] = set()
    unknown_pages: set[int] = set()
    for index, block in enumerate(result.blocks):
        if block.is_furniture or not block.text.strip() or block.page is None:
            continue
        if not line_inventory_valid(block):
            return empty
        if block.source_lines is None:
            if block.bbox is None:
                unknown_pages.add(block.page)
            else:
                # Legacy/unknown lines can compete for ownership, but cannot
                # themselves supply a source-mapped identifier.
                page_lines.setdefault(block.page, []).append(
                    _Line(index, None, block.page, block.bbox, block.text)
                )
            continue
        for span in block.source_lines:
            if span.is_furniture:
                continue
            if span.source_id is None or span.source_id in known_ids:
                return empty
            known_ids.add(span.source_id)
            line = _Line(
                index, span.source_id, block.page, span.bbox, block.text[span.start : span.end]
            )
            lines[index, span.source_id] = line
            page_lines.setdefault(block.page, []).append(line)

    group_lines: dict[tuple[int, str], set[int]] = {}
    valid_groups: set[int] = set()
    for index, group in enumerate(groups):
        valid = True
        for block in group:
            if (
                not isinstance(block, SourceFragment)
                or block.source_lines is None
                or block.page in unknown_pages
            ):
                valid = False
                continue
            for span in block.source_lines:
                key = (block.source_block_index, span.source_id)
                native = lines.get(key)
                if native is None or native.text != block.text[span.start : span.end]:
                    valid = False
                    continue
                group_lines.setdefault(key, set()).add(index)
        if valid:
            valid_groups.add(index)

    bound: dict[int, list[tuple[UriLink, _Line]]] = {}
    conflicted: set[int] = set()
    reference_pages = {block.page for group in groups for block in group if block.page is not None}
    links = [link for link in result.uri_links if link.page in reference_pages]
    # Check the complete ownership workload before any geometry comparisons.
    # Keep every competing native line on candidate pages; unrelated pages
    # cannot own a reference annotation and need not enter this cross-product.
    if sum(len(page_lines.get(link.page, [])) for link in links) > max_comparisons:
        return empty
    for link in links:
        owners = [line for line in page_lines.get(link.page, []) if _owns(line, link, tolerance)]
        memberships = [
            group_lines.get((line.block_index, line.source_id), set()) for line in owners
        ]
        if len(owners) != 1:
            conflicted.update(index for group in memberships for index in group)
            continue
        entries = memberships[0]
        if len(entries) != 1:
            conflicted.update(entries)
            continue
        bound.setdefault(next(iter(entries)), []).append((link, owners[0]))

    recovered = list(empty)
    for index in valid_groups - conflicted:
        raw = "\n".join(block.text for block in groups[index])
        if len(raw) > max_chars:
            continue
        candidates = bound.get(index, [])
        destinations = {link.uri for link, _ in candidates}
        if len(destinations) != 1:
            continue
        doi = _proxy_doi(next(iter(destinations)), resolver_hosts)
        recovery = _printed_span(raw, doi) if doi else None
        if recovery is None:
            continue
        used_links: set[int] = set()
        position = 0
        valid = True
        for block in groups[index]:
            for span in block.source_lines or []:
                start = max(position + span.start, recovery.start)
                end = min(position + span.end, recovery.end)
                if start >= end:
                    continue
                piece = raw[start:end].translate(_ASCII_FOLD)
                matches = [
                    link_index
                    for link_index, (link, owner) in enumerate(candidates)
                    if owner.block_index == block.source_block_index
                    and owner.source_id == span.source_id
                    and piece
                    in {text.strip().translate(_ASCII_FOLD) for text in link.text.splitlines()}
                ]
                if len(matches) != 1 or matches[0] in used_links:
                    valid = False
                    break
                used_links.add(matches[0])
            position += len(block.text) + 1
        if valid and len(used_links) == len(candidates):
            recovered[index] = recovery
    return recovered
