"""V-004 PDF ingestion tests.

Synthetic PDFs are built in-test with PyMuPDF (no binary fixtures in the
repo; V-008 adds the committed fixture corpus). Two extra suites:
- "local": runs only where the owner's proposal PDF exists (it is
  local-only by policy, D-007 — CI never has it, so CI skips).
- "live DB": service persistence against the docker-compose Postgres,
  same DATABASE_URL convention as test_schema.py.
"""

import asyncio
import os
import re
import time
from pathlib import Path

import pymupdf
import pytest

from app.config import get_settings
from app.errors import FileMalformedError
from app.ingest.pdf import extract_document
from app.ingest.schemas import ExtractionResult, SectionNode

DEMO_PDF = Path(__file__).resolve().parents[2] / "VERIDICAL-DOCUMENTATION.pdf"

BODY = 11.0
HEADING = 14.0
FONT = "helv"
FONT_BOLD = "hebo"
MARGIN_X = 72
LINE_STEP = 18


class PdfBuilder:
    """Minimal typeset helper: one insert per line, top-down."""

    def __init__(self) -> None:
        self.doc = pymupdf.open()
        self.page = None
        self.y = 0.0

    def new_page(self) -> "PdfBuilder":
        self.page = self.doc.new_page()  # default Letter-ish size
        self.y = MARGIN_X
        return self

    def line(self, text: str, size: float = BODY, bold: bool = False, y: float | None = None):
        if y is not None:
            self.y = y
        self.page.insert_text(
            (MARGIN_X, self.y), text, fontsize=size, fontname=FONT_BOLD if bold else FONT
        )
        self.y += LINE_STEP
        return self

    def image(self, rect=(200, 400, 300, 500)):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8))
        pix.clear_with(90)
        self.page.insert_image(pymupdf.Rect(*rect), stream=pix.tobytes("png"))
        return self

    def save(self, path: Path) -> Path:
        self.doc.save(str(path))
        self.doc.close()
        return path


def _thesis_pdf(tmp_path: Path) -> Path:
    """3 chapters, numbered subsections, a TOC page, running footers, an image."""
    b = PdfBuilder()
    b.new_page().line("A STUDY OF THINGS", size=16, bold=True)
    b.line("By Some Student")
    # TOC page: lists every heading with dot leaders — must not become headings
    b.new_page().line("TABLE OF CONTENTS", bold=True)
    b.line("CHAPTER 1 INTRODUCTION .......... 3")
    b.line("1.1 Background of the Study .......... 3")
    b.line("CHAPTER 2 METHODS .......... 4")
    b.line("2.1 Design .......... 4")
    b.line("2.1.1 Sampling .......... 4")
    b.line("CHAPTER 3 RESULTS .......... 5")
    pages = [
        ["CHAPTER 1 INTRODUCTION", "1.1 Background of the Study"],
        ["CHAPTER 2 METHODS", "2.1 Design", "2.1.1 Sampling"],
        ["CHAPTER 3 RESULTS"],
    ]
    for pno, headings in enumerate(pages, start=3):
        b.new_page()
        for h in headings:
            b.line(h, bold=True)
            for i in range(4):
                b.line(f"Body prose sentence {i} that talks about the study at length.")
        b.line(str(pno), y=760)  # bare page number in the bottom band
        b.line("A Study of Things - Draft", y=775)  # running footer
    b.page and b.image()
    b.new_page().line("REFERENCES", bold=True)
    b.line("Reyes, J. P., & Cruz, M. A. (2023). Assessing capstone readiness in state")
    b.page.insert_text(
        (100, b.y),
        "universities. Philippine Journal of Education, 12(3), 45–67.",
        fontsize=11,
        fontname="helv",
    )
    b.y += LINE_STEP
    b.line("Garcia, L. (2020). Understanding rubric design (2nd ed.). Academic Press.")
    return b.save(tmp_path / "thesis.pdf")


@pytest.fixture(scope="module")
def thesis(tmp_path_factory) -> ExtractionResult:
    path = _thesis_pdf(tmp_path_factory.mktemp("pdfs"))
    return extract_document(str(path), get_settings())


def _titles(nodes: list[SectionNode]) -> list[str]:
    out = []
    for n in nodes:
        out.append(n.title)
        out.extend(_titles(n.children))
    return out


def test_heuristic_tree_has_correct_chapters_and_nesting(thesis):
    tree = thesis.section_tree
    assert tree.source == "heuristics"
    chapters = [n for n in tree.nodes if n.numbering in {"1", "2", "3"}]
    assert [c.title for c in chapters] == [
        "CHAPTER 1 INTRODUCTION",
        "CHAPTER 2 METHODS",
        "CHAPTER 3 RESULTS",
    ]
    ch2 = chapters[1]
    assert [c.title for c in ch2.children] == ["2.1 Design"]
    assert [c.title for c in ch2.children[0].children] == ["2.1.1 Sampling"]
    # Chapter bodies start after the front matter (anchors are body pages).
    assert chapters[0].page == 3 and chapters[2].page == 5


def test_toc_page_lines_do_not_become_headings(thesis):
    # "CHAPTER 1 INTRODUCTION" exists on the TOC page (p2) AND in the body
    # (p3); only the body occurrence may enter the tree.
    anchors = [n.page for n in thesis.section_tree.nodes if n.numbering == "1"]
    assert anchors == [3]


def test_every_text_block_carries_a_page_number(thesis):
    assert thesis.blocks, "expected text blocks"
    assert all(1 <= b.page <= thesis.page_count for b in thesis.blocks)


def test_image_blocks_inventoried_with_bbox_and_page(thesis):
    assert len(thesis.images) == 1
    img = thesis.images[0]
    assert img.page == 5
    x0, y0, x1, y1 = img.bbox
    assert x1 > x0 and y1 > y0


def test_page_furniture_is_flagged_not_deleted(thesis):
    furniture = [b for b in thesis.blocks if b.is_furniture]
    texts = {b.text for b in furniture}
    assert any("Draft" in t for t in texts), "repeating footer should be furniture"
    # The bare page number shares a layout block with the footer, so it is
    # asserted as a line inside a furniture block, not as its own block.
    assert any(re.search(r"^\d+$", t, re.MULTILINE) for t in texts), (
        "bare page numbers should be inside furniture blocks"
    )
    # ...but never silently deleted: the blocks are still present above.
    non_furniture = {b.text for b in thesis.blocks if not b.is_furniture}
    assert "CHAPTER 1 INTRODUCTION" in non_furniture


def test_geometry_reports_margins(thesis):
    g = thesis.geometry[2]  # first body page
    assert g.page == 3 and g.rotation == 0
    assert g.margins is not None
    left, top, right, bottom = g.margins
    assert left == pytest.approx(MARGIN_X, abs=2)
    assert right > 0 and top > 0 and bottom > 0


def test_embedded_toc_used_when_it_matches_the_document(tmp_path):
    b = PdfBuilder()
    b.new_page().line("CHAPTER 1 INTRODUCTION", bold=True).line("Some body text.")
    b.new_page().line("CHAPTER 2 METHODS", bold=True).line("More body text.")
    b.doc.set_toc(
        [
            [1, "CHAPTER 1 INTRODUCTION", 1],
            [2, "1.1 Ghost", 1],
            [1, "CHAPTER 2 METHODS", 2],
            [2, "2.1 Ghost", 2],
            [1, "", 2],
            [1, "CHAPTER 2 METHODS", 2],
        ]
    )
    path = b.save(tmp_path / "with_toc.pdf")
    result = extract_document(str(path), get_settings())
    tree = result.section_tree
    assert tree.source == "embedded_toc"
    # Blank and duplicate entries cleaned; unfound "Ghost" entries kept only
    # because the outline as a whole passed the match threshold.
    assert [n.title for n in tree.nodes] == ["CHAPTER 1 INTRODUCTION", "CHAPTER 2 METHODS"]


def test_embedded_toc_contradicted_by_document_is_discarded(tmp_path):
    b = PdfBuilder()
    b.new_page().line("CHAPTER 1 INTRODUCTION", bold=True).line("Some body text.")
    b.new_page().line("CHAPTER 2 METHODS", bold=True).line("More body text.")
    b.doc.set_toc(
        [
            [1, "Completely Different Outline", 1],
            [1, "Nothing Here Matches", 1],
            [1, "Stale Entry", 2],
            [1, "Foreign Entry", 2],
            [1, "Wrong Again", 2],
        ]
    )
    path = b.save(tmp_path / "bogus_toc.pdf")
    tree = extract_document(str(path), get_settings()).section_tree
    assert tree.source == "heuristics"
    assert [n.title for n in tree.nodes] == ["CHAPTER 1 INTRODUCTION", "CHAPTER 2 METHODS"]


def test_image_only_scan_is_a_state_not_an_error(tmp_path):
    b = PdfBuilder()
    b.new_page().image(rect=(50, 50, 550, 700))
    b.new_page().image(rect=(50, 50, 550, 700))
    path = b.save(tmp_path / "scan.pdf")
    result = extract_document(str(path), get_settings())
    assert result.image_only is True
    assert result.section_tree.source == "none"
    assert len(result.images) == 2  # pipeline continues with what it has


def test_malformed_file_raises_taxonomy_error(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"this is not a pdf at all")
    with pytest.raises(FileMalformedError) as exc_info:
        extract_document(str(bad), get_settings())
    assert exc_info.value.code == "file_malformed"


def test_encrypted_pdf_raises_taxonomy_error(tmp_path):
    b = PdfBuilder()
    b.new_page().line("Secret content")
    path = tmp_path / "locked.pdf"
    b.doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    b.doc.close()
    with pytest.raises(FileMalformedError):
        extract_document(str(path), get_settings())


def test_hundred_page_pdf_ingests_under_a_minute(tmp_path):
    """Acceptance criterion: 100 pages < 60s on CPU (Render-class, no GPU).
    Local machines are faster than a Render dyno, so the margin must be
    large: we require < 30s here and record the real number in the ticket."""
    b = PdfBuilder()
    for ch in range(1, 101):
        b.new_page().line(f"CHAPTER {ch} TITLE", bold=True)
        for i in range(30):
            b.line(f"Line {i} of body prose for chapter {ch}, long enough to be realistic.")
    path = b.save(tmp_path / "hundred.pdf")
    started = time.perf_counter()
    result = extract_document(str(path), get_settings())
    elapsed = time.perf_counter() - started
    assert result.page_count == 100
    assert len(result.section_tree.nodes) == 100
    assert elapsed < 30, f"100-page ingest took {elapsed:.1f}s"


# --- the V0 demo document (local-only file; CI skips) ------------------------

demo_pdf_only = pytest.mark.skipif(
    not DEMO_PDF.exists(),
    reason="owner's proposal PDF is local-only (D-007); run this suite locally",
)


@demo_pdf_only
def test_demo_document_chapter_tree_via_embedded_toc():
    result = extract_document(str(DEMO_PDF), get_settings())
    tree = result.section_tree
    assert tree.source == "embedded_toc"
    _assert_demo_tree_correct(tree.nodes)
    assert result.text_chars > 0 and not result.image_only
    assert result.images, "the proposal contains figures"


@demo_pdf_only
def test_demo_document_chapter_tree_via_heuristics(monkeypatch):
    # Same document with its outline suppressed: the heuristic scan alone
    # must still recover the chapter tree (the no-embedded-TOC world).
    monkeypatch.setattr(pymupdf.Document, "get_toc", lambda self, simple=True: [])
    tree = extract_document(str(DEMO_PDF), get_settings()).section_tree
    assert tree.source == "heuristics"
    _assert_demo_tree_correct(tree.nodes)


def _assert_demo_tree_correct(nodes: list[SectionNode]) -> None:
    chapters = {n.numbering: n for n in nodes if n.numbering in {"1", "2", "3"}}
    assert set(chapters) == {"1", "2", "3"}
    assert "INTRODUCTION" in chapters["1"].title
    assert "REVIEW OF RELATED LITERATURE" in chapters["2"].title
    assert "RESEARCH METHODOLOGY" in chapters["3"].title
    ch1_nums = [c.numbering for c in chapters["1"].children]
    assert ch1_nums == ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6"]
    ch12 = chapters["1"].children[1]
    assert [c.numbering for c in ch12.children] == ["1.2.1", "1.2.2"]
    # Anchors: chapters start where the probe said they do.
    assert (chapters["1"].page, chapters["2"].page, chapters["3"].page) == (5, 10, 24)


# --- persistence (live DB, same convention as test_schema.py) ----------------

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)

SCRATCH_DB = "veridical_ingesttest"


@pytest.fixture(scope="module")
def ingest_scratch_url():
    """Own scratch database, migrated to head — the dev DB is never touched
    (test_schema.py's rule; its helpers are reused)."""
    from alembic import command
    from tests.test_schema import _admin_execute, _alembic_config, _swap_db

    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"'))
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    url = _swap_db(base, SCRATCH_DB)
    command.upgrade(_alembic_config(url), "head")
    yield url
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))


@live
def test_service_persists_tree_and_status(tmp_path, monkeypatch, ingest_scratch_url):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app import db
    from app.db import sqlalchemy_url
    from app.ingest.service import ingest_manuscript, raw_store_path
    from app.models import Instructor, Manuscript
    from app.models.enums import IngestStatus

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    # BUG-038: ingest_manuscript's vision pass goes through get_llm_client(),
    # which (in fake mode) now writes an audit row via the process-wide
    # db.get_engine() — same DATABASE_URL/engine-reset convention already
    # used by test_ingest_api.py/test_rubric_router.py's `client` fixtures,
    # so that write lands in this test's own scratch DB, not the shared dev
    # DB the ingest_scratch_url fixture's docstring promises never to touch.
    monkeypatch.setenv("DATABASE_URL", ingest_scratch_url)
    db._engine = None
    get_settings.cache_clear()
    settings = get_settings()
    pdf_path = _thesis_pdf(tmp_path)

    async def scenario():
        engine = create_async_engine(sqlalchemy_url(ingest_scratch_url))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            instructor = Instructor(
                email=f"ingest-test-{time.time_ns()}@test.local", display_name="Ingest Test"
            )
            session.add(instructor)
            await session.commit()
            manuscript = Manuscript(
                instructor_id=instructor.id, group_label="G", file_ref=str(pdf_path)
            )
            session.add(manuscript)
            await session.commit()

            result = await ingest_manuscript(session, manuscript, pdf_path, settings)

            stored = await session.scalar(select(Manuscript).where(Manuscript.id == manuscript.id))
            assert stored.ingest_status == IngestStatus.done
            assert stored.section_tree["source"] == "heuristics"
            assert len(stored.section_tree["nodes"]) == len(result.section_tree.nodes)
            raw = raw_store_path(settings, manuscript.id)
            assert raw.exists() and raw.stat().st_size > 0

            # Citations persisted (V-006) — and re-ingest replaces, not appends.
            from app.models import Citation

            for _ in range(2):
                await ingest_manuscript(session, manuscript, pdf_path, settings)
                rows = (
                    await session.scalars(
                        select(Citation)
                        .where(Citation.manuscript_id == manuscript.id)
                        .order_by(Citation.order_index)
                    )
                ).all()
                assert [c.authors[0] for c in rows] == ["Reyes, J. P.", "Garcia, L."]
                assert all(c.parse_status == "parsed" for c in rows)

            # Malformed upload: failed status recorded, taxonomy error raised.
            bad = tmp_path / "bad.pdf"
            bad.write_bytes(b"nope")
            broken = Manuscript(instructor_id=instructor.id, group_label="G", file_ref=str(bad))
            session.add(broken)
            await session.commit()
            with pytest.raises(FileMalformedError):
                await ingest_manuscript(session, broken, bad, settings)
            await session.refresh(broken)
            assert broken.ingest_status == IngestStatus.failed
            # BUG-016: a failed row must say why, not dead-end silently.
            from app.models.enums import IngestFailureReason

            assert broken.ingest_failure_reason == IngestFailureReason.unreadable_format
        await engine.dispose()

    try:
        asyncio.run(scenario())
    finally:
        # Drop the engine this test bound to its own scratch DB so later
        # tests in the same process don't inherit it (mirrors
        # test_ingest_api.py/test_rubric_router.py's `client` fixture).
        db._engine = None


@live
def test_oversized_upload_records_file_too_large_reason(tmp_path, monkeypatch, ingest_scratch_url):
    """BUG-016: the `ingest_upload` (too-large) failure path records its own
    reason, distinct from `ingest_manuscript`'s (unreadable-format) path."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.errors import FileTooLargeError
    from app.ingest.service import ingest_upload
    from app.models import Instructor, Manuscript
    from app.models.enums import IngestFailureReason, IngestStatus

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    settings = get_settings()

    async def chunks():
        yield b"x" * (2 * 1024 * 1024)

    async def scenario():
        engine = create_async_engine(sqlalchemy_url(ingest_scratch_url))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            instructor = Instructor(
                email=f"ingest-toolarge-{time.time_ns()}@test.local", display_name="Ingest Test"
            )
            session.add(instructor)
            await session.commit()

            with pytest.raises(FileTooLargeError):
                await ingest_upload(
                    session, chunks(), "big.pdf", "G", settings, instructor_id=instructor.id
                )

            manuscript = await session.scalar(
                select(Manuscript).where(Manuscript.instructor_id == instructor.id)
            )
            assert manuscript.ingest_status == IngestStatus.failed
            assert manuscript.ingest_failure_reason == IngestFailureReason.file_too_large
        await engine.dispose()

    asyncio.run(scenario())
