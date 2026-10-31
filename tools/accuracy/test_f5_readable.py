"""The F5 lane checks the same readable-pages copy the pipeline hands the check."""

from tools.accuracy.lanes import f5

from app.config import Settings
from tests.test_integrity_unread_pages import CITES_REYES, _source


def _write(tmp_path, extraction):
    path = tmp_path / "host.json"
    path.write_text(extraction.model_dump_json(), encoding="utf-8")
    return path


def test_the_twin_leaves_out_the_pages_production_leaves_out(tmp_path):
    settings = Settings(analysis_unread_pages_max_ratio=0.5)
    path = _write(tmp_path, _source(CITES_REYES))
    readable, unread, refused = f5._readable(path, settings)
    assert unread == [2] and not refused
    assert CITES_REYES not in [block.text for block in readable.blocks]


def test_a_planted_copy_reads_the_same_pages_as_its_twin(tmp_path):
    settings = Settings(analysis_unread_pages_max_ratio=0.5)
    path = _write(tmp_path, _source(CITES_REYES))
    twin, unread, _ = f5._readable(path, settings)
    copy = f5._load(path, unread)
    assert [block.text for block in copy.blocks] == [block.text for block in twin.blocks]
    assert copy.reading_order is None


def test_a_document_production_refuses_is_reported_as_refused(tmp_path):
    settings = Settings(analysis_unread_pages_max_ratio=0.5)
    path = _write(tmp_path, _source(CITES_REYES, unresolved=(1, 2, 3)))
    _, unread, refused = f5._readable(path, settings)
    assert unread == [] and refused
