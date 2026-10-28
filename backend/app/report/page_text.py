"""F8/4w: optional saved PDF reading aid, never a new assessment."""

import asyncio
import hashlib
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import pymupdf
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ConflictError, FileMalformedError, RateLimitedError
from app.ingest.native_table_order import (
    NativeTableProposal,
    capture_native_characters,
    saved_table_witness_valid,
)
from app.ingest.normalize import normalize
from app.ingest.pdf import _BOLD_FLAG, _native_tables_in_source_space
from app.ingest.reading_order import line_source_id, order_page, propose_regions
from app.ingest.schemas import ExtractionResult, Line, TextBlock
from app.ingest.service import detect_format, raw_store_path
from app.models.enums import IngestStatus
from app.models.manuscript import Manuscript
from app.report.source_files import open_source_pdf
from app.storage.access import read_source


class PageTextBlock(BaseModel):
    text: str
    is_furniture: bool


class PdfPageTextOut(BaseModel):
    check_run_id: int
    manuscript_id: int
    page: int
    page_count: int | None = None
    state: Literal["available", "no_selectable_text", "unavailable"] = "unavailable"
    reason: (
        Literal[
            "source_cache_unavailable",
            "saved_text_unavailable",
            "reading_order_not_recorded",
            "reading_order_unresolved",
            "source_binding_unverified",
            "resource_limit",
            "page_out_of_range",
        ]
        | None
    ) = None
    blocks: list[PageTextBlock] = Field(default_factory=list)
    saved_block_count: int | None = None
    omitted_block_count: int | None = None


_admission_lock = threading.Lock()
_active_readers = 0


@contextmanager
def page_text_admission(settings: Settings):
    """No waiting native-work queue, including while cancellation drains."""
    global _active_readers
    with _admission_lock:
        if _active_readers >= settings.pdf_page_text_slots:
            raise RateLimitedError("Page text is busy. Try again shortly.")
        _active_readers += 1
    try:
        yield
    finally:
        with _admission_lock:
            _active_readers -= 1


async def get_manuscript_page_text(
    session: AsyncSession, check_run_id: int, instructor_id: int, page: int, settings: Settings
) -> PdfPageTextOut:
    # Avoid importing report service into its own pure source helpers.
    from app.report.service import _owned_check_run

    run = await _owned_check_run(session, check_run_id, instructor_id)
    manuscript = await session.get(Manuscript, run.manuscript_id)
    with page_text_admission(settings):
        return await read_source(
            session,
            manuscript,
            lambda: asyncio.to_thread(
                manuscript_page_text_for, manuscript, check_run_id, page, settings
            ),
        )


def _source_hash(path: Path, settings: Settings) -> str | None:
    # Bound actual streamed bytes too, not only a potentially stale stat.
    remaining = settings.max_upload_mb * 1024 * 1024
    if path.stat().st_size > remaining:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(min(settings.content_hash_read_chunk_bytes, remaining + 1)):
            remaining -= len(chunk)
            if remaining < 0:
                return None
            digest.update(chunk)
    return digest.hexdigest()


def _native_lines(page: pymupdf.Page, raw_blocks: list[dict]) -> list[Line]:
    """Same native DICT representation as ingestion; never RAWDICT characters."""
    lines = []
    for block_no, block in enumerate(raw_blocks):
        for line_no, raw in enumerate(block.get("lines", [])):
            spans = [span for span in raw["spans"] if span["text"].strip()]
            if not spans:
                continue
            text = normalize("".join(span["text"] for span in raw["spans"]))
            if not text:
                continue
            chars = sum(len(span["text"]) for span in spans)
            bold = sum(len(span["text"]) for span in spans if span["flags"] & _BOLD_FLAG)
            lines.append(
                Line(
                    page=page.number + 1,
                    block_no=block_no,
                    bbox=tuple(raw["bbox"]),
                    text=text,
                    max_size=max(span["size"] for span in spans),
                    bold_ratio=bold / chars,
                    source_id=line_source_id(page.number + 1, block, block_no, line_no),
                )
            )
    return lines


def _bind_saved_lines(blocks: list[TextBlock], lines: list[Line]) -> list[str] | None:
    native = {line.source_id: line for line in lines}
    seen = []
    for block in blocks:
        if block.paragraph is not None or not block.source_lines or not block.source_line_ids:
            return None
        offset = 0
        ids = []
        texts = []
        for span in block.source_lines:
            line = native.get(span.source_id)
            if line is None or (
                span.start != offset
                or span.end != offset + len(line.text)
                or span.bbox != line.bbox
                or span.max_font_size != line.max_size
                or span.bold_ratio != line.bold_ratio
            ):
                return None
            # Retained furniture classification is producer metadata, not a
            # separately verified semantic claim. All its text is still returned.
            line.is_furniture = span.is_furniture
            ids.append(span.source_id)
            texts.append(line.text)
            offset = span.end + 1
        if ids != block.source_line_ids or block.text != "\n".join(texts):
            return None
        members = [native[sid] for sid in ids]
        if block.bbox != (
            min(line.bbox[0] for line in members),
            min(line.bbox[1] for line in members),
            max(line.bbox[2] for line in members),
            max(line.bbox[3] for line in members),
        ):
            return None
        seen.extend(ids)
    if len(seen) != len(set(seen)) or set(seen) != set(native):
        return None
    return seen


def manuscript_page_text_for(
    manuscript: Manuscript, check_run_id: int, page: int, settings: Settings
) -> PdfPageTextOut:
    out = PdfPageTextOut(check_run_id=check_run_id, manuscript_id=manuscript.id, page=page)

    def unavailable(reason):
        out.reason = reason
        return out

    if manuscript.purged_at is not None:
        from app.errors import GoneError

        raise GoneError("This manuscript's source file was purged and can no longer be read.")
    if manuscript.ingest_status != IngestStatus.done:
        return unavailable("saved_text_unavailable")
    # The aid does not introduce unbounded cold get_bytes allocations. Existing
    # original-PDF recovery may refill its cache under that endpoint's contract.
    path = Path(manuscript.file_ref)
    try:
        digest = _source_hash(path, settings)
    except FileNotFoundError:
        return unavailable("source_cache_unavailable")
    if digest is None:
        return unavailable("resource_limit")
    try:
        source_format = detect_format(path, settings)
    except FileMalformedError:
        raise ConflictError("This manuscript's source file could not be read.") from None
    if source_format != ".pdf":
        raise ConflictError("Page text is available only for PDF sources.")
    with open_source_pdf(path) as doc:
        out.page_count = doc.page_count
        if page < 1 or page > doc.page_count:
            return unavailable("page_out_of_range")
        if doc.page_count > settings.ingest_max_pages:
            return unavailable("resource_limit")
        if not manuscript.content_hash or digest != manuscript.content_hash:
            return unavailable("source_binding_unverified")
        raw_path = raw_store_path(settings, manuscript.id)
        try:
            with raw_path.open("rb") as stream:
                payload = stream.read(settings.pdf_page_text_raw_max_bytes + 1)
        except FileNotFoundError:
            return unavailable("saved_text_unavailable")
        if len(payload) > settings.pdf_page_text_raw_max_bytes:
            return unavailable("resource_limit")
        try:
            extraction = ExtractionResult.model_validate_json(payload)
        except ValidationError:
            return unavailable("source_binding_unverified")
        if extraction.anchor_kind != "page" or extraction.page_count != doc.page_count:
            return unavailable("source_binding_unverified")
        records = [record for record in extraction.reading_order or [] if record.page == page]
        if not records:
            return unavailable("reading_order_not_recorded")
        if len(records) != 1:
            return unavailable("source_binding_unverified")
        record = records[0]
        if record.status != "ordered":
            return unavailable("reading_order_unresolved")
        if not record.source_inventory_sha256:
            return unavailable("source_binding_unverified")
        if not saved_table_witness_valid(extraction, record):
            return unavailable("source_binding_unverified")
        blocks = [block for block in extraction.blocks if block.page == page]
        native_page = doc[page - 1]
        # Independent table presence check: absence in the saved JSON is not
        # enough to authorize flattening a table page.
        native_tables = NativeTableProposal()
        table_blocks = _native_tables_in_source_space(
            native_page, page, ordering=native_tables, settings=settings
        )
        raw_blocks = native_page.get_text(
            "dict", sort=True, flags=(pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
        )["blocks"]
        lines = _native_lines(native_page, raw_blocks)
        if len(lines) > settings.ingest_reading_order_max_lines_per_page:
            return unavailable("resource_limit")
        ids = _bind_saved_lines(blocks, lines)
        if ids is None or len(lines) != record.source_line_count:
            return unavailable("source_binding_unverified")
        proposal = propose_regions(native_page, raw_blocks, lines, settings)
        if table_blocks:
            capture_native_characters(native_page, raw_blocks, lines, native_tables, settings)
            proposal.table_proposal = native_tables
        _, current = order_page(
            page, lines, proposal, has_tables=bool(table_blocks), settings=settings
        )
        if current.status != "ordered":
            return unavailable("reading_order_unresolved")
        if (
            current.source_inventory_sha256 != record.source_inventory_sha256
            or current.table_order != record.table_order
            or current.ordered_source_line_ids != record.ordered_source_line_ids
            or ids != record.ordered_source_line_ids
        ):
            return unavailable("source_binding_unverified")
        out.state, out.reason = "available" if blocks else "no_selectable_text", None
        out.saved_block_count = len(blocks)
        chars = 0
        for block in blocks:
            if len(out.blocks) >= settings.pdf_page_text_max_blocks:
                break
            if chars + len(block.text) > settings.pdf_page_text_max_chars:
                break
            item = PageTextBlock(text=block.text, is_furniture=block.is_furniture)
            out.blocks.append(item)
            out.omitted_block_count = len(blocks) - len(out.blocks)
            if len(out.model_dump_json().encode("utf-8")) > settings.pdf_page_text_max_bytes:
                out.blocks.pop()
                break
            chars += len(block.text)
        out.omitted_block_count = len(blocks) - len(out.blocks)
        return out
