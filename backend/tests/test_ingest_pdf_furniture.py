"""F1: preserve repeated body text without weakening marginal-order guards."""

import hashlib
import re
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from itertools import product
from pathlib import Path

import pymupdf
import pytest

from app.config import Settings
from app.ingest import sections
from app.ingest.normalize import normalize
from app.ingest.patterns import load_patterns
from app.ingest.pdf import _mark_furniture, extract_document
from app.ingest.schemas import Line


def _pages(case="body", *, reverse=False):
    pages = {}
    for page in (1, 2, 3):
        entries = [
            (f"{page}.1 Evidence {chr(64 + page)}", (72, 40, 330, 55)),
            ("A repeated substantive statement follows the heading.", (72, 70, 350, 85)),
            (f"Ordinary continuation with distinct evidence {page}.", (72, 150, 370, 165)),
            (str(page), (72, 930, 85, 945)),
            ("Running document footer", (72, 970, 300, 985)),
        ]
        if case == "no_heading":
            entries.pop(0)
        elif case == "repeated_heading":
            entries[0] = ("1.1 Common heading", entries[0][1])
        elif case == "heading_below":
            entries[0] = (entries[0][0], (72, 100, 330, 115))
        elif case == "heading_other_column":
            entries[0] = (entries[0][0], (420, 40, 600, 55))
        elif case == "spanning_two_columns":
            entries[0] = (entries[0][0], (72, 40, 600, 55))
            entries[2] = (entries[2][0], (72, 150, 250, 165))
            entries.insert(3, (f"Right column continuation {page}", (420, 150, 600, 165)))
        elif case == "no_common_column":
            entries[2] = (entries[2][0], (350, 150, 600, 165))
        elif case == "touching":
            entries[0] = (entries[0][0], (72, 40, 330, 70))
        elif case == "overlap":
            entries.insert(3, (f"Overlapping continuation {page}", (72, 155, 370, 170)))
        elif case == "toc":
            entries.insert(0, ("TABLE OF CONTENTS", (72, 10, 330, 25)))
        elif case == "no_continuation":
            entries.pop(2)
        elif case == "explicit_number":
            entries[1] = (str(page), entries[1][1])
        elif case == "custom_heading":
            entries[0] = (f"PART {chr(64 + page)} - Evidence", entries[0][1])
        elif case == "genuine_header":
            entries.insert(0, (f"Running title - page {page}", (72, 10, 330, 25)))
        lines = [
            Line(page, index, box, text, 11, 0, False, f"{page}:{index}:0")
            for index, (text, box) in enumerate(entries)
        ]
        pages[page] = lines[::-1] if reverse else lines
    return pages


def _classify(pages, *, patterns=None, **overrides):
    before = deepcopy(pages)
    _mark_furniture(
        pages,
        dict.fromkeys(pages, 1000),
        patterns or load_patterns(),
        Settings(_env_file=None, **overrides),
    )
    # Classification may change inclusion only; never text, coordinates, IDs,
    # input storage order, or any other original line property.
    for page, lines in pages.items():
        for old, new in zip(before[page], lines, strict=True):
            assert {k: v for k, v in vars(old).items() if k != "is_furniture"} == {
                k: v for k, v in vars(new).items() if k != "is_furniture"
            }
    return pages


def _body_lines(pages):
    return [
        line
        for lines in pages.values()
        for line in lines
        if line.text == "A repeated substantive statement follows the heading."
    ]


@pytest.mark.parametrize("reverse", [False, True])
def test_repeated_first_body_line_remains_substantive_using_geometry(reverse):
    pages = _classify(_pages(reverse=reverse))
    assert len(_body_lines(pages)) == 3
    assert all(not line.is_furniture for line in _body_lines(pages))
    assert all(
        line.is_furniture for lines in pages.values() for line in lines if line.bbox[1] >= 900
    )


@pytest.mark.parametrize(
    "case",
    [
        "no_heading",
        "repeated_heading",
        "heading_below",
        "heading_other_column",
        "spanning_two_columns",
        "no_common_column",
        "touching",
        "overlap",
        "toc",
        "no_continuation",
    ],
)
def test_insufficient_flow_evidence_keeps_original_repetition_classification(case):
    assert all(line.is_furniture for line in _body_lines(_classify(_pages(case))))


def test_explicit_page_numbers_and_digit_masked_genuine_headers_stay_furniture():
    explicit = _classify(_pages("explicit_number"))
    assert all(
        line.is_furniture
        for lines in explicit.values()
        for line in lines
        if line.bbox == (72, 70, 350, 85)
    )
    genuine = _classify(_pages("genuine_header"))
    assert all(
        line.is_furniture
        for lines in genuine.values()
        for line in lines
        if line.text.startswith("Running title")
    )
    assert all(not line.is_furniture for line in _body_lines(genuine))


def test_custom_heading_data_is_used_without_format_literals():
    custom = replace(
        load_patterns(), chapter=re.compile(r"^PART (?P<num>[A-Z]) - (?P<title>.+)$", re.IGNORECASE)
    )
    assert all(
        not line.is_furniture
        for line in _body_lines(_classify(_pages("custom_heading"), patterns=custom))
    )
    assert all(line.is_furniture for line in _body_lines(_classify(_pages("custom_heading"))))


def test_extra_geometry_proof_respects_existing_line_budget():
    pages = _classify(_pages(), ingest_reading_order_max_lines_per_page=4)
    assert all(line.is_furniture for line in _body_lines(pages))


@pytest.mark.parametrize(
    "box",
    [
        (72, float("nan"), 370, 165),
        (72, 150, float("inf"), 165),
        (370, 150, 72, 165),
        (72, 165, 370, 150),
    ],
)
def test_invalid_source_geometry_cannot_support_body_restoration(box):
    pages = _pages()
    for lines in pages.values():
        lines[2].bbox = box
    # NaN is not equal to itself, so compare the non-geometry source inventory
    # directly here instead of the ordinary immutable-line helper.
    _mark_furniture(pages, dict.fromkeys(pages, 1000), load_patterns(), Settings(_env_file=None))
    assert all(line.is_furniture for line in _body_lines(pages))


@pytest.mark.parametrize("case", ["empty", "no_candidate", "over_budget", "invalid_geometry"])
def test_ineligible_flow_never_starts_an_additional_toc_scan(monkeypatch, case):
    def unexpected(*_args):
        raise AssertionError("Ineligible source must not trigger a TOC scan")

    monkeypatch.setattr(sections, "toc_page_numbers", unexpected)
    pages = _pages()
    settings = Settings(_env_file=None)
    if case == "empty":
        pages = {}
    elif case == "no_candidate":
        pages = {1: [Line(1, 0, (72, 30, 350, 45), "." * 16000 + "x", 11, 0)]}
    elif case == "over_budget":
        settings = settings.model_copy(update={"ingest_reading_order_max_lines_per_page": 4})
    else:
        for lines in pages.values():
            lines[2].bbox = (72, 150, float("inf"), 165)
    _mark_furniture(pages, dict.fromkeys(pages, 1000), load_patterns(), settings)


def test_packaged_toc_suffix_keeps_boolean_search_semantics():
    original = re.compile(r"[.\u2026\s]*\d{1,4}\s*$")
    current = load_patterns().toc_line_suffix
    alphabet = (".", "\u2026", " ", "1", "x", "\n", "\t", "\u0661")
    for length in range(5):
        for chars in product(alphabet, repeat=length):
            text = "".join(chars)
            assert bool(current.search(text)) == bool(original.search(text)), repr(text)
    for text in ("12345", "Text ... 1234\n", "Text \u0661\u0662\u2003", "1\n\n", "", "1234x"):
        assert bool(current.search(text)) == bool(original.search(text))


@pytest.mark.parametrize("strategy", ["native_regions_v1", "legacy"])
def test_immutable_pdf_preserves_all_original_lines_and_recovers_177_body_characters(strategy):
    path = Path(__file__).parent / "fixtures/ingest/native.pdf"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert before == "7f8d6922626fe44585b47fc9ed22dc6dbc53414be671337c3c753b8fe491f0ad"
    result = extract_document(
        str(path), Settings(_env_file=None, ingest_reading_order_strategy=strategy)
    )
    assert result.text_chars == 1973
    assert all(
        not block.is_furniture
        for block in result.blocks
        if block.text.startswith("Body prose sentence 0")
    )
    assert any(block.is_furniture and "Draft" in block.text for block in result.blocks)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    if strategy == "legacy":
        assert result.reading_order is None
        return
    assert len(result.reading_order) == 6
    assert all(page.status == "ordered" for page in result.reading_order)
    actual = Counter(
        (block.page, span.bbox, block.text[span.start : span.end])
        for block in result.blocks
        for span in block.source_lines or []
    )
    with pymupdf.open(path) as source:
        expected = Counter(
            (
                page.number + 1,
                tuple(line["bbox"]),
                normalize("".join(s["text"] for s in line["spans"])),
            )
            for page in source
            for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
            if "".join(s["text"] for s in line["spans"]).strip()
        )
    assert actual == expected
    ids = [source_id for block in result.blocks for source_id in block.source_line_ids or []]
    assert len(ids) == len(set(ids)) == 49
