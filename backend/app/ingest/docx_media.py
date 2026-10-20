"""F1/F8 original embedded raster inventory, independent of image assessment."""

import hashlib
import struct
from dataclasses import dataclass, field

from docx.image.exceptions import (
    InvalidImageStreamError,
    UnexpectedEndOfFileError,
    UnrecognizedImageError,
)
from docx.image.image import Image
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn
from pydantic import ValidationError

from app.config import Settings
from app.ingest.schemas import DocxImageSource, ImageBlock

_VML_IMAGE = "{urn:schemas-microsoft-com:vml}imagedata"
_RASTER_TYPES = {"image/png", "image/jpeg"}
_IMAGE_ERRORS = (
    InvalidImageStreamError,
    UnexpectedEndOfFileError,
    UnrecognizedImageError,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    struct.error,
    ZeroDivisionError,
)


def _bound_metadata(blob: bytes, limit: int) -> None:
    """Walk offsets only; python-docx otherwise allocates an object per chunk/marker."""
    png = blob.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8 if png else 2
    count = 0
    while offset < len(blob):
        count += 1
        if count > limit:
            raise ValueError("image_limit")
        if png:
            if offset + 12 > len(blob):
                raise ValueError("invalid_image")
            length = struct.unpack_from(">I", blob, offset)[0]
            end = offset + length + 12
            if end > len(blob):
                raise ValueError("invalid_image")
            kind = blob[offset + 4 : offset + 8]
            if kind == b"IEND":
                return
            offset = end
        else:
            if blob[offset] != 0xFF:
                raise ValueError("invalid_image")
            while offset < len(blob) and blob[offset] == 0xFF:
                offset += 1
            if offset >= len(blob):
                raise ValueError("invalid_image")
            marker = blob[offset]
            offset += 1
            if marker in {0x00, 0xD8, 0xD9}:
                raise ValueError("invalid_image")
            if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(blob):
                raise ValueError("invalid_image")
            length = struct.unpack_from(">H", blob, offset)[0]
            if length < 2 or offset + length > len(blob):
                raise ValueError("invalid_image")
            if marker == 0xDA:  # python-docx stops collecting header objects at SOS.
                return
            offset += length
    raise ValueError("invalid_image")


def raster_header(blob: bytes, content_type: str, settings: Settings) -> Image:
    """Header inspection only; do not decode an unbounded pixel array."""
    if len(blob) > settings.docx_image_max_bytes:
        raise ValueError("image_limit")
    if content_type not in _RASTER_TYPES:
        raise ValueError("invalid_image")
    if not (blob.startswith(b"\x89PNG\r\n\x1a\n") or blob.startswith(b"\xff\xd8\xff")):
        raise ValueError("invalid_image")
    _bound_metadata(blob, settings.docx_image_max_metadata_segments)
    try:
        header = Image.from_blob(blob)
    except _IMAGE_ERRORS as exc:
        raise ValueError("invalid_image") from exc
    except Exception as exc:
        # python-docx's JPEG marker reader raises this exact untyped exception
        # for malformed embedded offsets. Do not suppress other programming errors.
        if type(exc) is Exception and str(exc) == "unexpected end of file":
            raise ValueError("invalid_image") from exc
        raise
    if header.content_type != content_type or min(header.px_width, header.px_height) <= 0:
        raise ValueError("invalid_image")
    if header.px_width * header.px_height > settings.docx_image_max_pixels:
        raise ValueError("image_limit")
    return header


@dataclass
class DocxMediaInventory:
    settings: Settings
    # Multiple occurrences keep distinct anchors, while shared parts are inspected once.
    sources: dict[str, DocxImageSource] = field(default_factory=dict)
    rejected: dict[str, str] = field(default_factory=dict)
    total_bytes: int = 0

    def describe(self, drawing, document_part, paragraph: int) -> ImageBlock:
        entry = ImageBlock(paragraph=paragraph, display_unavailable_reason="unsupported_drawing")
        for element in drawing.iter():
            if element.tag in {qn("wp:docPr"), qn("pic:cNvPr")}:
                description = element.get("descr") or element.get("title")
                if description:
                    entry.source_alt_text = description
                    break
        image_nodes = [
            element for element in drawing.iter() if element.tag in {qn("a:blip"), _VML_IMAGE}
        ]
        if len(image_nodes) != 1:
            return entry
        node = image_nodes[0]
        if node.get(qn("r:link")):
            entry.display_unavailable_reason = "external_image"
            return entry
        relationship_id = node.get(qn("r:embed")) or node.get(qn("r:id"))
        relation = document_part.rels.get(relationship_id)
        if relation is None or relation.reltype != RT.IMAGE:
            entry.display_unavailable_reason = "invalid_image"
            return entry
        if relation.is_external:
            entry.display_unavailable_reason = "external_image"
            return entry
        part = relation.target_part
        name = str(part.partname).removeprefix("/")
        if rejected := self.rejected.get(name):
            entry.display_unavailable_reason = rejected
            return entry
        if cached := self.sources.get(name):
            entry.docx_source = cached.model_copy(update={"relationship_id": relationship_id})
            entry.display_unavailable_reason = None
            return entry
        blob = part.blob
        if self.total_bytes + len(blob) > self.settings.docx_image_total_max_bytes:
            entry.display_unavailable_reason = "image_limit"
            self.rejected[name] = entry.display_unavailable_reason
            return entry
        try:
            header = raster_header(blob, part.content_type, self.settings)
            source = DocxImageSource(
                relationship_id=relationship_id,
                part_name=name,
                sha256=hashlib.sha256(blob).hexdigest(),
                content_type=header.content_type,
                byte_length=len(blob),
                width_px=header.px_width,
                height_px=header.px_height,
            )
        except ValidationError:
            entry.display_unavailable_reason = "invalid_image"
            self.rejected[name] = entry.display_unavailable_reason
            return entry
        except ValueError as exc:
            entry.display_unavailable_reason = (
                "image_limit" if str(exc) == "image_limit" else "invalid_image"
            )
            self.rejected[name] = entry.display_unavailable_reason
            return entry
        self.sources[name] = source
        self.total_bytes += len(blob)
        entry.docx_source = source
        entry.display_unavailable_reason = None
        return entry
