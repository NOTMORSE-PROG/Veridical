"""V-007 vision-pass tests — all on the LLMClient interface, zero network.

The fake fixture (app/llm/fixtures/image_table_extraction.json) is the
deterministic answer in fake-LLM mode; a spy client covers quota
accounting and the low-confidence / chart / garbage response paths.
"""

import asyncio
from typing import Any

from app.config import get_settings
from app.ingest import vision
from app.ingest.patterns import load_patterns
from app.ingest.pdf import extract_document
from app.llm.base import LLMClient
from app.llm.fake import FakeLLMClient
from tests.test_ingest_pdf import PdfBuilder

KNOWN_ROWS = [
    ["Group", "N", "Mean", "SD"],
    ["Students", "12", "3.42", "0.51"],
    ["Instructors", "5", "4.20", "0.37"],
]


class SpyLLM(LLMClient):
    """Scripted responses + call/context recording."""

    def __init__(self, responses: list[dict[str, Any]] | None = None):
        self.calls: list[dict[str, Any]] = []
        self._responses = responses

    async def complete(self, prompt_type: str, prompt: str, **context: Any) -> dict[str, Any]:
        self.calls.append({"prompt_type": prompt_type, "prompt": prompt, **context})
        if self._responses is None:
            return {"kind": "other", "confidence": "high"}
        return self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]


def _pdf_with_images(tmp_path, *, n_results_images: int, n_intro_images: int = 0, tiny: int = 0):
    """Ch1 (intro) and Ch4 RESULTS AND DISCUSSION; images placed per chapter."""
    b = PdfBuilder()
    b.new_page().line("CHAPTER 1 INTRODUCTION", bold=True).line("Prose about the study.")
    for _ in range(n_intro_images):
        b.image(rect=(100, 300, 300, 450))
    b.new_page().line("CHAPTER 4 RESULTS AND DISCUSSION", bold=True).line("Findings prose.")
    y = 200
    for _ in range(n_results_images):
        b.image(rect=(100, y, 320, y + 120))
        y += 130
    for _ in range(tiny):
        b.image(rect=(100, y, 120, y + 20))  # 400 pt² — decorative
        y += 25
    return b.save(tmp_path / "figures.pdf")


def _run(pdf_path, llm):
    settings = get_settings()
    patterns = load_patterns(settings.ingest_patterns_file)
    result = extract_document(str(pdf_path), settings)
    calls = asyncio.run(vision.read_images(result, pdf_path, llm, patterns, settings))
    return result, calls


def test_fake_llm_round_trips_known_table_values(tmp_path):
    """Acceptance: a screenshot table becomes structured rows with numbers
    intact, deterministically, in fake-LLM mode (no keys, no quota)."""
    path = _pdf_with_images(tmp_path, n_results_images=1)
    result, calls = _run(path, FakeLLMClient())
    assert calls == 1
    assert result.vision_status == "done"
    vision_tables = [t for t in result.tables if t.source == "vision"]
    assert len(vision_tables) == 1
    table = vision_tables[0]
    assert table.rows == KNOWN_ROWS  # digits exactly as in the fixture
    assert table.low_confidence is False and table.page == 2
    # F6 availability: high-confidence vision tables expose their numbers.
    assert any("3.42" in cell for row in table.rows for cell in row)


def test_quota_accounting_filter_priority_and_cap(tmp_path, monkeypatch):
    """N images -> exactly the expected number of calls: tiny images never
    call, results-chapter images outrank intro ones, cap is hard."""
    monkeypatch.setenv("INGEST_VISION_MAX_IMAGES", "2")
    get_settings.cache_clear()
    path = _pdf_with_images(tmp_path, n_results_images=2, n_intro_images=2, tiny=3)
    spy = SpyLLM()
    result, calls = _run(path, spy)
    assert calls == 2 == len(spy.calls)
    # Both calls carried one PNG each and the versioned prompt type.
    for call in spy.calls:
        assert call["prompt_type"] == vision.PROMPT_TYPE
        assert call["prompt_version"] == vision.PROMPT_VERSION
        assert len(call["images"]) == 1 and call["images"][0][:4] == b"\x89PNG"
    # The two selected were the RESULTS-chapter images (page 2), not the
    # intro ones — priority ordering decided within the cap.
    settings = get_settings()
    chosen = vision.select_images(result, load_patterns(settings.ingest_patterns_file), settings)
    assert [img.page for img in chosen] == [2, 2]


def test_low_confidence_reading_is_marked_and_kept_out_of_hard_data(tmp_path):
    responses = [{"kind": "table", "confidence": "low", "rows": [["A", "?"]], "caption": None}]
    path = _pdf_with_images(tmp_path, n_results_images=1)
    result, _ = _run(path, SpyLLM(responses))
    [table] = [t for t in result.tables if t.source == "vision"]
    assert table.low_confidence is True  # F6 hard checks must skip it


def test_charts_are_classified_not_hallucinated(tmp_path):
    responses = [{"kind": "chart", "confidence": "high"}]
    path = _pdf_with_images(tmp_path, n_results_images=1)
    result, calls = _run(path, SpyLLM(responses))
    assert calls == 1
    assert [t for t in result.tables if t.source == "vision"] == []
    assert result.equations == []


def test_equation_reading_merges_as_equation_block(tmp_path):
    responses = [{"kind": "equation", "confidence": "high", "latex": "t(24) = 2.31, p < .05"}]
    path = _pdf_with_images(tmp_path, n_results_images=1)
    result, _ = _run(path, SpyLLM(responses))
    assert len(result.equations) == 1
    assert result.equations[0].latex == "t(24) = 2.31, p < .05"


def test_garbage_model_response_is_tolerated(tmp_path):
    responses = [{"totally": "unexpected", "shape": 42}]
    path = _pdf_with_images(tmp_path, n_results_images=1)
    result, calls = _run(path, SpyLLM(responses))
    assert calls == 1  # the call happened, the answer was discarded
    assert [t for t in result.tables if t.source == "vision"] == []
    assert result.vision_status == "done"


def test_no_images_means_no_calls(tmp_path):
    b = PdfBuilder()
    b.new_page().line("CHAPTER 1 INTRODUCTION", bold=True).line("Only text.")
    path = b.save(tmp_path / "noimg.pdf")
    spy = SpyLLM()
    result, calls = _run(path, spy)
    assert calls == 0 and spy.calls == []
    assert result.vision_status == "none"


class _StubSession:
    """Just enough AsyncSession surface for ingest_manuscript."""

    async def commit(self) -> None:
        return None

    async def execute(self, *_args: Any, **_kw: Any) -> None:
        return None

    def add_all(self, items: Any) -> None:
        list(items)


def test_ingestion_survives_missing_llm_client(tmp_path, monkeypatch):
    """Real mode with no GEMINI_API_KEY configured: vision 'unavailable' is
    a recorded state, never an ingestion failure (ticket edge case,
    ENGINEERING honesty). V-009 makes real mode work WITH a key — this
    test pins down the "requested real mode, key missing" branch, which
    stays reachable (a deploy can still misconfigure the key)."""
    import app.llm as llm_module
    from app.ingest.service import ingest_manuscript
    from app.models import Manuscript
    from app.models.enums import IngestStatus

    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "0")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    llm_module._real_client = None
    path = _pdf_with_images(tmp_path, n_results_images=1)
    manuscript = Manuscript(instructor_id=1, group_label="G", file_ref=str(path))
    manuscript.id = 999  # no DB in this test; only the raw-store name uses it

    result = asyncio.run(ingest_manuscript(_StubSession(), manuscript, path, get_settings()))
    assert result.vision_status == "unavailable"
    assert manuscript.ingest_status == IngestStatus.done
    assert [t for t in result.tables if t.source == "vision"] == []
