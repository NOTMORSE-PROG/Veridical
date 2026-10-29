"""F1: zero extracted records must preserve unresolved source membership."""

from types import SimpleNamespace

import pytest

from app.ingest import receipts
from app.ingest.quality import SOURCE_REFERENCE_BOUNDARY_INGEST_NOTE
from app.ingest.schemas import ExtractionResult, SectionTree


@pytest.mark.parametrize(
    "issues,count,expected",
    [
        (("ambiguous_reference_end",), 0, True),
        ((), 0, False),
        ((), 2, False),
    ],
)
def test_upload_receipt_preserves_reference_boundary_limit(monkeypatch, issues, count, expected):
    monkeypatch.setattr(receipts, "reading_order_ingest_note", lambda *args: None)
    monkeypatch.setattr(
        receipts,
        "select_references",
        lambda *args: SimpleNamespace(issues=issues, membership_issues=()),
    )
    result = ExtractionResult(
        anchor_kind="page",
        image_only=False,
        page_count=1,
        text_chars=100,
        blocks=[],
        images=[],
        tables=[],
        section_tree=SectionTree(source="none", nodes=[]),
    )
    manuscript = SimpleNamespace(id=1, group_label="Control", ingest_status="done")
    proposal = SimpleNamespace(
        title=None, short_name=None, program=None, adviser=None, members=[], extraction_failed=True
    )
    receipt = receipts.ingestion_receipt(manuscript, result, count, proposal, None)
    assert receipt.citations == count
    assert (SOURCE_REFERENCE_BOUNDARY_INGEST_NOTE in receipt.notes) is expected
