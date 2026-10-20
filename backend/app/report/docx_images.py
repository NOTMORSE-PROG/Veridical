"""Authorized callers can read a raster bound to the original DOCX and saved extraction."""

import hashlib
import posixpath
import zipfile
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from lxml.etree import XMLSyntaxError, iterparse
from pydantic import ValidationError

from app.config import Settings
from app.errors import ConflictError, GoneError, NotFoundError
from app.ingest.docx_media import raster_header
from app.ingest.schemas import ImageBlock
from app.ingest.service import load_raw_store
from app.models.manuscript import Manuscript
from app.storage import ensure_local_file, get_storage

_UNAVAILABLE = "This source image could not be verified against the original DOCX."
_RELATIONSHIPS = "word/_rels/document.xml.rels"
_RELATIONSHIP_TAG = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"


class _BoundedXmlReader:
    def __init__(self, stream: BinaryIO, limit: int):
        self.stream = stream
        self.remaining = limit

    def read(self, size: int) -> bytes:
        content = self.stream.read(min(size, self.remaining + 1))
        self.remaining -= len(content)
        if self.remaining < 0:
            raise GoneError(_UNAVAILABLE)
        return content


def _verify_occurrence(stream: BinaryIO, index: int, image: ImageBlock, settings: Settings) -> None:
    """Check only a bounded XML prefix, discarding completed nodes as it streams."""
    if image.docx_source is None:
        raise GoneError(_UNAVAILABLE)
    document = body = item = selected = None
    body_index = -1
    occurrence = -1
    nodes_seen = image_references = 0
    events = iterparse(
        _BoundedXmlReader(stream, settings.docx_image_source_xml_max_bytes),
        events=("start", "end"),
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        attribute_defaults=False,
        huge_tree=False,
        recover=False,
        remove_comments=True,
        remove_pis=True,
    )
    for event, node in events:
        if event == "start":
            nodes_seen += 1
            if nodes_seen > settings.docx_image_source_xml_max_nodes:
                raise GoneError(_UNAVAILABLE)
            if document is None:
                if node.tag != qn("w:document"):
                    raise GoneError(_UNAVAILABLE)
                document = node
            elif node.getparent() is document and node.tag == qn("w:body"):
                body = node
            elif node.getparent() is body and node.tag in {qn("w:p"), qn("w:tbl")}:
                body_index += 1
                item = node
            if item is not None and node.tag in {qn("w:drawing"), qn("w:pict")}:
                occurrence += 1
                if occurrence == index:
                    if body_index != image.paragraph:
                        raise GoneError(_UNAVAILABLE)
                    selected = node
            if selected is not None and node.tag in {
                qn("a:blip"),
                "{urn:schemas-microsoft-com:vml}imagedata",
            }:
                image_references += 1
                relationship = node.get(qn("r:embed")) or node.get(qn("r:id"))
                if (
                    image_references != 1
                    or node.get(qn("r:link"))
                    or relationship != image.docx_source.relationship_id
                ):
                    raise GoneError(_UNAVAILABLE)
            continue
        if node is selected:
            if image_references != 1:
                raise GoneError(_UNAVAILABLE)
            # The original file hash was checked before streaming. This read is
            # source-occurrence verification, not revalidation of the whole XML.
            return
        if node is body:
            # python-docx uses the first direct body. A later malformed body
            # cannot supply an occurrence absent from that native inventory.
            raise GoneError(_UNAVAILABLE)
        if node is item:
            item = None
        node.clear(keep_tail=True)
        while node.getprevious() is not None:
            del node.getparent()[0]
    raise GoneError(_UNAVAILABLE)


def _member(package: zipfile.ZipFile, name: str, limit: int) -> bytes:
    info = package.getinfo(name)
    if info.file_size > limit:
        raise GoneError(_UNAVAILABLE)
    with package.open(info) as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise GoneError(_UNAVAILABLE)
    return content


def manuscript_docx_image_for(
    manuscript: Manuscript, image_index: int, settings: Settings
) -> tuple[bytes, str]:
    """Caller must authorize first. No re-extraction, assessment, or external image fetch."""
    if manuscript.purged_at is not None:
        raise GoneError("This manuscript's source file was purged and can no longer be read.")
    if Path(manuscript.file_ref).suffix.lower() != ".docx":
        raise ConflictError("This manuscript's source file isn't a DOCX.")
    if image_index < 0 or image_index >= settings.docx_image_max_count:
        raise NotFoundError("No image at this source location.")
    try:
        extraction = load_raw_store(settings, manuscript.id)
    except (FileNotFoundError, ValidationError):
        raise GoneError(_UNAVAILABLE) from None
    if image_index >= len(extraction.images):
        raise NotFoundError("No image at this source location.")
    image = extraction.images[image_index]
    source = image.docx_source
    if (
        source is None
        or not extraction.docx_source_sha256
        or extraction.docx_source_sha256 != manuscript.content_hash
    ):
        raise GoneError(_UNAVAILABLE)
    try:
        path = ensure_local_file(settings, get_storage(settings), manuscript.file_ref)
        # Same opened file supplies the digest and member reads; no pathname
        # re-open between verifying identity and obtaining the requested bytes.
        with path.open("rb") as original:
            if hashlib.file_digest(original, "sha256").hexdigest() != manuscript.content_hash:
                raise GoneError(_UNAVAILABLE)
            original.seek(0)
            with zipfile.ZipFile(original) as package:
                names = package.namelist()
                if len(set(names)) != len(names):
                    raise GoneError(_UNAVAILABLE)
                with package.open("word/document.xml") as document:
                    _verify_occurrence(document, image_index, image, settings)
                # parse_xml uses python-docx's entity-disabled XML parser.
                relations = parse_xml(
                    _member(package, _RELATIONSHIPS, settings.docx_image_max_bytes)
                )
                matches = [
                    row
                    for row in relations
                    if row.tag == _RELATIONSHIP_TAG and row.get("Id") == source.relationship_id
                ]
                if len(matches) != 1:
                    raise GoneError(_UNAVAILABLE)
                relation = matches[0]
                target = relation.get("Target", "")
                uri = urlsplit(target)
                if (
                    relation.get("Type") != RT.IMAGE
                    or relation.get("TargetMode") not in {None, "Internal"}
                    or not target
                    or uri.scheme
                    or uri.netloc
                    or uri.query
                    or uri.fragment
                    or "\\" in target
                ):
                    raise GoneError(_UNAVAILABLE)
                member = posixpath.normpath(
                    target.removeprefix("/")
                    if target.startswith("/")
                    else posixpath.join("word", target)
                )
                if member != source.part_name:
                    raise GoneError(_UNAVAILABLE)
                content = _member(package, member, settings.docx_image_max_bytes)
        if (
            len(content) != source.byte_length
            or hashlib.sha256(content).hexdigest() != source.sha256
        ):
            raise GoneError(_UNAVAILABLE)
        header = raster_header(content, source.content_type, settings)
        if (header.px_width, header.px_height) != (source.width_px, source.height_px):
            raise GoneError(_UNAVAILABLE)
        return content, source.content_type
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, XMLSyntaxError):
        raise GoneError(_UNAVAILABLE) from None
