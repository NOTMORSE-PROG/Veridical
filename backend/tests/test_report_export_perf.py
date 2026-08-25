"""V-039 AC: a 60-criterion report exports in <30s with a <50MB memory
spike (measured, not assumed) -- the exact numbers the ticket names.
Pure unit test on `build_report_pdf`, no live DB needed (this is a
render-speed/memory property of the function itself, not the data
layer).

V-070's own test below measures a DIFFERENT kind of memory cost the same
way, for the same reason: `tracemalloc` only tracks Python-heap
allocations, which is correct for `build_report_pdf` (pure reportlab, pure
Python) above but would be a FALSE-CONFIDENCE measurement for
`build_page_evidence_images` (PyMuPDF's `get_pixmap` allocates natively in
C, invisible to `tracemalloc` -- confirmed live, 2026-08-25: a same-process
`tracemalloc` reading during page rendering showed <2MB while the real OS
working set grew by 30-70MB). `_peak_native_memory_mb` below measures the
OS's own view of this process instead."""

import time
import tracemalloc
from pathlib import Path

from app.report.export import build_report_pdf
from app.report.schemas import (
    CriterionResultOut,
    EvidenceItem,
    FlagSummaryOut,
    ReportExportData,
    ReportOut,
)

_MAX_SECONDS = 30
_MAX_MEMORY_MB = 50


def _peak_native_memory_mb() -> float:
    """Best-available NATIVE (not Python-heap) peak working-set/RSS for
    THIS process. POSIX (CI/Render's actual Linux target):
    `resource.getrusage().ru_maxrss` (KB on Linux). Windows (local dev
    only, `resource` doesn't exist there): a `GetProcessMemoryInfo` ctypes
    probe -- psutil isn't a project dependency, and this is a one-off
    measurement, not a runtime import."""
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except ImportError:
        import ctypes
        import ctypes.wintypes as wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        psapi = ctypes.WinDLL("psapi.dll")
        kernel32 = ctypes.WinDLL("kernel32.dll")
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_Counters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        ok = psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        )
        assert ok
        return counters.PeakWorkingSetSize / 1024 / 1024


def _sixty_criterion_report() -> ReportExportData:
    results = [
        CriterionResultOut(
            criterion_id=i,
            text=f"Criterion {i}: checks a specific structural or content requirement of chapter "
            "formatting, in enough detail to be realistic.",
            type="semantic" if i % 2 else "structural",
            weight=100 / 60,
            weight_importance="med",
            kind="semantic_grading",
            outcome="passed" if i % 3 else "failed",
            score=90.0,
            basis=None,
            anchor="page 3",
            reasoning=(
                "The manuscript demonstrates clear evidence of meeting this criterion based on "
                "the extracted content and structure. " * 2
            ),
            reason=None,
            evidence=[
                EvidenceItem(
                    quote=(
                        "A representative excerpt from the manuscript showing compliance with "
                        "this criterion, spanning a couple of sentences for realism."
                    ),
                    anchor="page 3, paragraph 2",
                )
            ],
            resolution=None,
        )
        for i in range(60)
    ]
    flags = [
        FlagSummaryOut(
            id=i,
            check_kind="citation_integrity",
            severity="high" if i % 4 == 0 else "low",
            criterion_text=None,
            evidence_excerpt="Citation excerpt text repeated for realistic length. " * 5,
            page_anchor=f"p. {i}",
            overridden=False,
        )
        for i in range(15)
    ]
    report = ReportOut(
        check_run_id=1,
        manuscript_group_label="Test Group",
        manuscript_original_filename="thesis.pdf",
        rubric_title="TIP Format v2",
        status="conditionally_ready",
        composite_score=72.5,
        thresholds={"ready_min_score": 85.0, "not_ready_max_score": 60.0},
        reason=None,
        flag_deduction=2.5,
        unresolved_high_flag_count=0,
        llm_mode="real",
        results=results,
        decision=None,
        decided_at=None,
        decision_note=None,
        pending_review_count=0,
        rubric_is_current=True,
        previous_status=None,
        previous_composite_score=None,
    )
    return ReportExportData(report=report, flags=flags, archive_size_n=42)


def test_sixty_criterion_export_meets_the_speed_and_memory_ac():
    data = _sixty_criterion_report()

    tracemalloc.start()
    start = time.perf_counter()
    pdf_bytes = build_report_pdf(data)
    elapsed = time.perf_counter() - start
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert pdf_bytes.startswith(b"%PDF")
    assert elapsed < _MAX_SECONDS, f"export took {elapsed:.2f}s, AC is <{_MAX_SECONDS}s"
    peak_mb = peak / 1024 / 1024
    assert peak_mb < _MAX_MEMORY_MB, f"export peaked at {peak_mb:.1f}MB, AC is <{_MAX_MEMORY_MB}MB"


# V-070 AC4: peak (native) memory and output size for the page-image
# rendering path, measured against `export_max_page_images`'s own
# memory-safety argument (`config.py`) -- reproducible from this test,
# not only from a one-off scratch script (`backend-critic` finding,
# 2026-08-25: the ticket's Q1 claim previously lived only in a code
# comment, unreproducible from anything committed).
_N_PAGES = 30
_MAX_INCREMENTAL_NATIVE_MB = 150


def _n_page_pdf(tmp_path: Path, n_pages: int) -> Path:
    from tests.test_ingest_pdf import PdfBuilder

    b = PdfBuilder()
    for i in range(n_pages):
        b.new_page().line(f"CHAPTER {i + 1} A REALISTIC HEADING", bold=True)
        b.line(
            f"This is real, findable quoted body text on page {i + 1}, long enough to be a "
            "realistic excerpt for search_for to match verbatim against, the same shape a "
            "citation-integrity or internal-agreement flag's own evidence_excerpt takes."
        )
    return b.save(tmp_path / "page_image_perf.pdf")


def test_page_image_rendering_stays_within_a_measured_native_memory_bound(tmp_path):
    """The ticket's own Q1 ("measure first: peak RSS and output size for
    34 flags on a 47-page manuscript, at several DPIs. Then set the cap in
    config") -- exercised here as an automated, re-runnable bound rather
    than a prose claim. 30 distinct pages, each with its own real
    page-anchored flag (so every one recovers a real `page_bbox`, the
    single most expensive case: a real `get_pixmap` render plus a real
    box). `_MAX_INCREMENTAL_NATIVE_MB` is set with real margin above the
    ~2MB/page (150dpi) this project has repeatedly measured for pages of
    this size -- generous enough not to flake across platforms/PyMuPDF
    versions, tight enough to catch a real regression (e.g. an accidental
    return to rendering the same page once per flag instead of once per
    page)."""
    import pymupdf

    from app.config import get_settings
    from app.ingest.pdf import extract_document
    from app.report.page_images import build_page_evidence_images

    path = _n_page_pdf(tmp_path, _N_PAGES)
    extraction = extract_document(str(path), get_settings())
    flags = [
        FlagSummaryOut(
            id=i,
            check_kind="citation_integrity",
            severity="high",
            criterion_text=None,
            evidence_excerpt=(
                f"This is real, findable quoted body text on page {i + 1}, long enough to be a "
                "realistic excerpt for search_for to match verbatim against, the same shape a "
                "citation-integrity or internal-agreement flag's own evidence_excerpt takes."
            ),
            page_anchor=f"p. {i + 1}",
            overridden=False,
        )
        for i in range(_N_PAGES)
    ]

    baseline_mb = _peak_native_memory_mb()
    start = time.perf_counter()
    doc = pymupdf.open(str(path))
    try:
        result = build_page_evidence_images(
            doc,
            extraction.section_tree,
            flags,
            source_format="pdf",
            unavailable_reason=None,
            llm_mode="real",
            settings=get_settings(),
        )
    finally:
        doc.close()
    elapsed = time.perf_counter() - start
    peak_mb = _peak_native_memory_mb()

    assert all(
        v.image_png is not None and v.image_png.startswith(b"\x89PNG") for v in result.values()
    )
    total_output_mb = sum(len(v.image_png) for v in result.values()) / 1024 / 1024
    incremental_mb = peak_mb - baseline_mb
    assert incremental_mb < _MAX_INCREMENTAL_NATIVE_MB, (
        f"rendering {_N_PAGES} distinct page images added {incremental_mb:.1f}MB native "
        f"memory (baseline {baseline_mb:.1f}MB -> peak {peak_mb:.1f}MB), AC is "
        f"<{_MAX_INCREMENTAL_NATIVE_MB}MB incremental; elapsed={elapsed:.2f}s, "
        f"total_output={total_output_mb:.2f}MB"
    )
