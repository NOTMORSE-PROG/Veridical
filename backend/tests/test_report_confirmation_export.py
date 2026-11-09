"""Saved finding judgments belong in the instructor's portable report (F8)."""

from datetime import UTC, datetime, timedelta, timezone

import fitz
import pytest

from app.report.export import build_report_pdf
from app.report.export_data import flag_export_detail
from tests.test_report_export_details import _data, _public_flag, _text


def print_data(flag):
    data = _data()
    data.flags = [
        data.flags[0].model_copy(
            update={
                "id": flag.id,
                "finding_confirmed": flag.finding_confirmed,
                "overridden": flag.overridden,
                "check_kind": flag.check_kind,
                "evidence_excerpt": flag.evidence_excerpt,
                "page_anchor": flag.page_anchor,
            }
        )
    ]
    data.flag_details = {flag.id: flag_export_detail(flag)}
    return data


def test_saved_confirmation_reaches_print_without_private_annotation():
    recorded_at = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    flag = _public_flag(
        finding_confirmed=True,
        finding_confirm_note="Checked against the saved source and arithmetic.",
        finding_confirmed_at=recorded_at,
    )
    detail = flag_export_detail(flag)
    assert detail.finding_confirm_note == flag.finding_confirm_note
    assert detail.finding_confirmed_at == recorded_at
    assert "annotation" not in detail.model_dump()
    data = print_data(flag)
    before = data.model_dump_json()
    text = _text(build_report_pdf(data))
    assert "Recorded instructor judgment: Confirmed" in text
    assert "Jan 2, 2026, 3:04 AM UTC" in text
    assert flag.finding_confirm_note in text
    assert flag.evidence_excerpt in text and flag.ai_reasoning in text
    assert flag.annotation not in text
    assert data.model_dump_json() == before


@pytest.mark.parametrize("note", [None, ""])
def test_confirmation_without_note_still_prints_its_saved_state(note):
    text = _text(
        build_report_pdf(
            print_data(_public_flag(finding_confirmed=True, finding_confirm_note=note))
        )
    )
    assert "Recorded instructor judgment: Confirmed" in text
    assert "Confirmed (" not in text


@pytest.mark.parametrize("overridden,confirmed", [(False, False), (True, True), (True, False)])
def test_open_or_dismissed_finding_cannot_print_stale_confirmation(overridden, confirmed):
    flag = _public_flag(
        overridden=overridden,
        override_reason="Saved dismissal reason." if overridden else None,
        finding_confirmed=confirmed,
        finding_confirm_note="STALE_CONFIRMATION_NOTE",
        finding_confirmed_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    detail = flag_export_detail(flag)
    assert detail.finding_confirm_note is None and detail.finding_confirmed_at is None
    text = _text(build_report_pdf(print_data(flag)))
    assert "STALE_CONFIRMATION_NOTE" not in text
    assert "Recorded instructor judgment: Confirmed" not in text
    if overridden:
        assert "Saved dismissal reason." in text and "Overridden" in text


def test_confirmation_note_is_literal_text_and_not_report_markup():
    note = '<b>Literal comment</b> & <img src="https://invalid.example/pixel">'
    flag = _public_flag(finding_confirmed=True, finding_confirm_note=note)
    text = _text(build_report_pdf(print_data(flag)))
    assert note in text
    assert "PRIVATE_ANNOTATION" not in text


@pytest.mark.parametrize("aware", [True, False])
def test_confirmation_time_preserves_known_zone_or_unqualified_stored_value(aware):
    recorded_at = datetime(2026, 1, 2, 11, 4)
    if aware:
        recorded_at = recorded_at.replace(tzinfo=timezone(timedelta(hours=8)))
    text = _text(
        build_report_pdf(
            print_data(_public_flag(finding_confirmed=True, finding_confirmed_at=recorded_at))
        )
    )
    assert ("Jan 2, 2026, 3:04 AM UTC" if aware else "2026-01-02T11:04:00") in text


def test_long_confirmation_preserves_tail_source_and_navigation():
    note = "CONFIRMATION_START " + "The saved source was checked. " * 400 + "CONFIRMATION_END"
    data = print_data(_public_flag(finding_confirmed=True, finding_confirm_note=note))
    with fitz.open(stream=build_report_pdf(data), filetype="pdf") as doc:
        text = " ".join(" ".join(page.get_text().split()) for page in doc)
        assert "CONFIRMATION_START" in text and "CONFIRMATION_END" in text
        assert data.flags[0].evidence_excerpt in text
        assert doc.get_toc()
        assert any(link.get("page", -1) >= 0 for page in doc for link in page.get_links())
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        if span["text"].strip():
                            assert page.rect.contains(fitz.Rect(span["bbox"]))
