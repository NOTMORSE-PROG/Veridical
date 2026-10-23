"""Explicit synthetic DOCX source inventory for isolated consumer controls.

This measures a generated no-math main document with the fixture's paragraph
positions. It supplies the new native-math contract without changing the
consumer's chosen text/layout/reference controls. It is not ingestion or UAT
evidence; full ingestion controls use their actual source bytes instead.
"""

import hashlib
from io import BytesIO

from docx import Document

from app.ingest.docx_math import inventory_native_math
from app.ingest.schemas import ExtractionResult


def record_empty_native_inventory(extraction: ExtractionResult) -> ExtractionResult:
    if extraction.anchor_kind != "paragraph":
        return extraction
    positions = {block.paragraph: block.text for block in extraction.blocks}
    assert None not in positions
    document = Document()
    for index in range(max(positions, default=-1) + 1):
        document.add_paragraph(positions.get(index, ""))
    stream = BytesIO()
    document.save(stream)
    digest = hashlib.sha256(stream.getvalue()).hexdigest()
    return extraction.model_copy(
        update={
            "docx_source_sha256": digest,
            "native_math_inventory": inventory_native_math(document, digest),
        }
    )
