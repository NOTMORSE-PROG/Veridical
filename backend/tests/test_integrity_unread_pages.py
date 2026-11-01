"""The integrity checks read every page whose reading order is established,
leave out the rest, and say so; text they did not read never becomes a
finding of absence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.checks.citations import verify
from app.checks.source_quality import (
    readable_pages_copy,
    source_limitation_detail,
    unread_pages_detail,
    without_unread_pages,
)
from app.config import Settings
from app.ingest.patterns import load_patterns
from app.ingest.quality import reading_order_issues
from app.ingest.references import extract_references
from app.ingest.schemas import (
    ExtractionResult,
    PageReadingOrder,
    SectionTree,
    TableBlock,
    TextBlock,
)
from app.models.citation import Citation
from app.models.enums import CheckKind, ResultOutcome
from app.models.run import Flag
from app.pipeline import machine
from tests.test_stabilization_integrity_controls import MemorySession

SMITH = "Smith, A. (2020). Booking systems for clinics. Journal of Examples."
REYES = "Reyes, J. (2024). Example source. Journal of Examples."


def _source(page_two: str, *, unresolved=(2,), table=False) -> ExtractionResult:
    contents = [
        (1, "According to Smith (2020), clinics lose bookings to phone queues."),
        (2, page_two),
        (3, "REFERENCES"),
        (3, REYES),
        (3, SMITH),
    ]
    blocks = [
        TextBlock(
            page=page,
            text=text,
            bbox=(50, 100 + index * 30, 500, 120 + index * 30),
            max_font_size=12,
            bold_ratio=1 if text == "REFERENCES" else 0,
            source_line_ids=[f"{page}:{index}:0"],
        )
        for index, (page, text) in enumerate(contents)
    ]
    return ExtractionResult(
        page_count=3,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(text) for _page, text in contents),
        blocks=blocks,
        images=[],
        tables=[TableBlock(page=2, rows=[["n", "mean"], ["12", "4.4"]])] if table else [],
        section_tree=SectionTree(source="none", nodes=[]),
        reading_order=[
            PageReadingOrder(
                page=page,
                status="unresolved" if page in unresolved else "ordered",
                reason="native_table_grid_invalid" if page in unresolved else None,
                source_inventory_sha256="a" * 64,
                source_line_count=sum(block.page == page for block in blocks),
                ordered_source_line_ids=[
                    identifier
                    for block in blocks
                    if block.page == page
                    for identifier in block.source_line_ids
                ],
            )
            for page in range(1, 4)
        ],
    )


CITES_REYES = "Reyes (2024) reports twelve staff in the user test."


def test_the_readable_copy_has_no_reading_order_issue_of_its_own():
    extraction = _source(CITES_REYES)
    assert reading_order_issues(extraction) == [{"page": 2, "reason": "native_table_grid_invalid"}]
    readable = without_unread_pages(extraction, [2])
    assert reading_order_issues(readable) == []
    # A caller whose own scope includes the page still sees it as unread.
    assert reading_order_issues(readable, pages={2, 3}) == [
        {"page": 2, "reason": "native_table_grid_invalid"}
    ]


def test_only_an_unresolved_page_may_be_left_out():
    extraction = _source(CITES_REYES, unresolved=())
    assert reading_order_issues(without_unread_pages(extraction, [2])) == [
        {"page": 2, "reason": "reading_order_scope_invalid"}
    ]


def test_readable_copy_drops_text_and_keeps_table_positions():
    extraction = _source(CITES_REYES, table=True)
    readable = without_unread_pages(extraction, [2])
    assert CITES_REYES not in [block.text for block in readable.blocks]
    assert len(readable.tables) == 1 and readable.tables[0].low_confidence
    assert readable.unread_pages == [2]
    assert extraction.unread_pages is None and not extraction.tables[0].low_confidence
    assert "unread_pages" not in readable.model_dump()


def test_the_checks_are_given_the_readable_pages_when_the_limit_is_confined():
    extraction = _source(CITES_REYES)
    readable, unread = readable_pages_copy(extraction, max_unread_ratio=0.5)
    assert unread == [2]
    assert CITES_REYES not in [block.text for block in readable.blocks]
    assert source_limitation_detail(readable) is None


def test_the_whole_document_is_kept_when_too_many_pages_are_unread():
    extraction = _source(CITES_REYES, unresolved=(1, 2))
    readable, unread = readable_pages_copy(extraction, max_unread_ratio=0.5)
    assert unread == [] and readable is extraction
    assert source_limitation_detail(readable) is not None


def test_a_more_specific_recorded_limit_is_kept():
    assert unread_pages_detail([2], {}) == {
        "unread_pages": [2],
        "n_source_pages_unassessed": 1,
        "coverage_reason": "source_reading_order_partial",
        "analysis_coverage": "partial",
    }
    kept = unread_pages_detail(
        [2], {"coverage_reason": "no_comparison_archive", "analysis_coverage": "unsupported"}
    )
    assert kept["coverage_reason"] == "no_comparison_archive"
    assert kept["analysis_coverage"] == "unsupported"


async def _citations(monkeypatch, extraction):
    session = MemorySession()
    monkeypatch.setattr(
        verify,
        "verify_citation",
        AsyncMock(return_value=verify.CitationVerdict(verify.VerdictKind.not_found)),
    )
    monkeypatch.setattr(verify, "run_claim_support_check", AsyncMock())
    monkeypatch.setattr(verify, "write_audit_event", AsyncMock())
    monkeypatch.setattr("app.checks.source_quality.write_audit_event", AsyncMock())
    citations = [
        Citation(manuscript_id=1, **vars(draft))
        for draft in extract_references(extraction, load_patterns())
    ]
    assert len(citations) == 2
    result = await verify.run_citation_integrity_check(
        session, None, 1, citations, extraction, load_patterns(), Settings(), object()
    )
    flags = [row for row in session.rows if isinstance(row, Flag)]
    # A lead asks the instructor to check and does not count; a finding does.
    kinds = [
        row.detail["kind"] for row in flags if row.detail.get("affects_readiness") is not False
    ]
    leads = [row.detail for row in flags if row.detail.get("affects_readiness") is False]
    return result, kinds, leads


async def test_a_reference_cited_only_on_an_unread_page_is_not_called_uncited(monkeypatch):
    # Control: when the page is readable and never cites Reyes, it is uncited.
    readable_everywhere = _source("The user test ran for two weeks.", unresolved=())
    _result, kinds, _leads = await _citations(monkeypatch, readable_everywhere)
    assert "uncited_reference" in kinds

    unread = without_unread_pages(_source(CITES_REYES), [2])
    result, kinds, leads = await _citations(monkeypatch, unread)
    assert "uncited_reference" not in kinds
    assert result.detail["n_uncited"] == 0
    assert result.detail["n_linkage_unassessed"] >= 1
    # The unread page names Reyes, so the instructor gets a lead to check it.
    [lead] = leads
    assert lead["kind"] == "uncited_reference" and lead["unread_pages"] == [2]
    assert "page 2" in lead["reason"] and "does not change readiness" in lead["reason"]
    assert result.detail["n_uncited_withheld_by_unread_pages"] == 1


async def test_an_unread_page_that_does_not_name_the_entry_does_not_withhold_it(monkeypatch):
    # Page 2 is unread but is a table of numbers that names no reference, so
    # Reyes, cited nowhere that was read, is still reported as uncited.
    unread = without_unread_pages(_source("n = 12, mean 4.4, sd 0.9"), [2])
    result, kinds, leads = await _citations(monkeypatch, unread)
    assert "uncited_reference" in kinds
    assert leads == []
    assert result.detail["n_uncited_withheld_by_unread_pages"] == 0


async def test_check_results_record_the_pages_they_did_not_read(monkeypatch):
    audit = AsyncMock()
    monkeypatch.setattr(machine, "write_audit_event", audit)
    session = SimpleNamespace(commit=AsyncMock())
    passed = SimpleNamespace(
        kind=CheckKind.statistical_forensics, outcome=ResultOutcome.passed, detail={"n_flags": 0}
    )
    await machine._record_unread_pages(session, 9, passed, [45, 46])
    assert passed.outcome == ResultOutcome.escalated
    assert passed.detail["unread_pages"] == [45, 46]
    assert passed.detail["n_source_pages_unassessed"] == 2
    assert passed.detail["coverage_reason"] == "source_reading_order_partial"
    assert audit.await_args.kwargs["event_type"] == "statistical_forensics_source_scope_recorded"
    await machine._record_unread_pages(session, 9, passed, [45], 0.5)
    assert audit.await_args.kwargs["payload"]["unread_pages_max_ratio"] == 0.5

    failed = SimpleNamespace(
        kind=CheckKind.originality_reuse, outcome=ResultOutcome.failed, detail={"n_flags": 2}
    )
    await machine._record_unread_pages(session, 9, failed, [45])
    assert failed.outcome == ResultOutcome.failed
    assert failed.detail["n_flags"] == 2

    untouched = SimpleNamespace(
        kind=CheckKind.citation_integrity, outcome=ResultOutcome.passed, detail={}
    )
    await machine._record_unread_pages(session, 9, untouched, [])
    assert untouched.outcome == ResultOutcome.passed and untouched.detail == {}


async def _integrity_inputs(monkeypatch, source, settings, *, saved_reuse=None):
    """Runs the real integrity stage with every check stubbed, and returns the
    extraction each check was given and the unread pages each one recorded."""
    import contextlib

    from app.checks.forensics import service as forensics

    given, recorded = {}, {}

    def check(kind, position):
        async def run(*args, **_kwargs):
            given[kind] = args[position]
            return SimpleNamespace(kind=kind, detail={"n_flags": 0})

        return run

    async def none(*_args):
        return None

    async def record(_session, _run_id, result, pages, _ratio=None):
        recorded[result.kind] = pages

    async def raw(_settings, _manuscript_id):
        return source

    @contextlib.asynccontextmanager
    async def client(_settings):
        yield None

    async def reuse_existing(*_args):
        return saved_reuse

    for name in ("existing_internal_agreement_result", "existing_citation_integrity_result"):
        monkeypatch.setattr(machine, name, none)
    monkeypatch.setattr(machine, "existing_originality_reuse_result", reuse_existing)
    monkeypatch.setattr(forensics, "existing_statistical_forensics_result", none)
    monkeypatch.setattr(
        machine, "run_internal_agreement_check", check(CheckKind.internal_agreement, 3)
    )
    monkeypatch.setattr(
        machine, "run_citation_integrity_check", check(CheckKind.citation_integrity, 4)
    )
    monkeypatch.setattr(
        forensics, "run_statistical_forensics_check", check(CheckKind.statistical_forensics, 2)
    )
    monkeypatch.setattr(
        machine, "run_originality_reuse_check", check(CheckKind.originality_reuse, 3)
    )
    monkeypatch.setattr(machine, "_record_unread_pages", record)
    monkeypatch.setattr(machine, "_record_stage", lambda *args, **kwargs: None)
    monkeypatch.setattr(machine, "load_raw_store_async", raw)
    monkeypatch.setattr(machine, "bind_analytical_source", lambda extraction, _hash: extraction)
    monkeypatch.setattr(machine, "build_http_client", client)
    session = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])), commit=AsyncMock()
    )
    run = SimpleNamespace(id=9)
    manuscript = SimpleNamespace(id=4, content_hash=None, instructor_id=1)
    await machine._run_integrity_stage(session, run, manuscript, settings, None, AsyncMock())
    return given, recorded


async def test_originality_gets_its_own_readable_copy_between_the_two_bars(monkeypatch):
    """BUG-621: one of three pages unread is over the shared bar, so the
    other checks keep exactly the copy they had before, and the originality
    check compares the two readable pages and names the one it left out."""
    source = _source(CITES_REYES)
    settings = Settings(analysis_unread_pages_max_ratio=0.25, reuse_unread_pages_max_ratio=0.5)
    shared, _ = readable_pages_copy(source, settings.analysis_unread_pages_max_ratio)
    given, recorded = await _integrity_inputs(monkeypatch, source, settings)
    others = (
        CheckKind.internal_agreement,
        CheckKind.citation_integrity,
        CheckKind.statistical_forensics,
    )
    for kind in others:
        assert given[kind].model_dump_json() == shared.model_dump_json(), kind
        # Not in the dump, but the citation check reads it.
        assert given[kind].unread_text == shared.unread_text, kind
        assert recorded[kind] == []
    reuse = given[CheckKind.originality_reuse]
    assert CITES_REYES not in [block.text for block in reuse.blocks]
    assert source_limitation_detail(reuse) is None
    assert recorded[CheckKind.originality_reuse] == [2]


async def test_originality_shares_the_copy_when_both_bars_leave_the_same_pages(monkeypatch):
    source = _source(CITES_REYES)
    settings = Settings(analysis_unread_pages_max_ratio=0.4, reuse_unread_pages_max_ratio=0.5)
    given, recorded = await _integrity_inputs(monkeypatch, source, settings)
    assert given[CheckKind.originality_reuse] is given[CheckKind.internal_agreement]
    assert all(pages == [2] for pages in recorded.values()) and len(recorded) == 4


async def test_a_saved_originality_result_gets_its_own_unread_pages_on_resume(monkeypatch):
    source = _source(CITES_REYES)
    settings = Settings(analysis_unread_pages_max_ratio=0.25, reuse_unread_pages_max_ratio=0.5)
    saved = SimpleNamespace(kind=CheckKind.originality_reuse, detail={"n_flags": 0})
    _given, recorded = await _integrity_inputs(monkeypatch, source, settings, saved_reuse=saved)
    assert recorded[CheckKind.originality_reuse] == [2]
