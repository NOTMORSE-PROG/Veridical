"""F1.1 reading order: geometry controls, not manuscript accuracy estimates."""

from collections import Counter

import pymupdf
import pytest

from app.config import Settings
from app.ingest.patterns import load_patterns
from app.ingest.pdf import _extract_open_document


def extract(doc, **overrides):
    settings = Settings(
        _env_file=None,
        veridical_fake_llm=True,
        ingest_reading_order_strategy="native_regions_v1",
        **overrides,
    )
    return _extract_open_document(doc, load_patterns(), settings)


def native_lines(doc):
    return Counter(
        (page.number + 1, tuple(line["bbox"]), "".join(s["text"] for s in line["spans"]))
        for page in doc
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        if "".join(s["text"] for s in line["spans"]).strip()
    )


def content(result):
    return [
        line
        for block in result.blocks
        if not block.is_furniture
        for line in block.text.splitlines()
    ]


def placed(entries, *, rotation=0, cropped=False):
    doc = pymupdf.open()
    page = doc.new_page(width=660, height=840)
    for x, y, text in entries:
        page.insert_text((x, y), text, fontsize=10)
    if cropped:
        page.set_cropbox(pymupdf.Rect(35, 35, 640, 800))
    page.set_rotation(rotation)
    return doc


@pytest.mark.parametrize("storage", ["left", "right", "rows"])
@pytest.mark.parametrize("columns", [2, 3])
def test_off_center_columns_independent_of_storage_order(storage, columns):
    groups = [
        [(x, 110 + row * 30, f"Column{col} line{row}") for row in range(3)]
        for col, x in enumerate([55, 280, 500][:columns])
    ]
    if storage == "rows":
        entries = [group[row] for row in range(3) for group in groups]
    else:
        entries = [
            item for group in (groups if storage == "left" else groups[::-1]) for item in group
        ]
    with placed(entries) as doc:
        result = extract(doc)
    assert result.reading_order[0].status == "ordered"
    assert content(result) == [item[2] for group in groups for item in group]
    ids = [source_id for block in result.blocks for source_id in block.source_line_ids]
    assert len(ids) == len(set(ids)) == len(entries)


def test_reversed_single_column_is_geometric_not_storage_order():
    with placed([(70, 170, "Third"), (70, 110, "First"), (70, 140, "Second")]) as doc:
        result = extract(doc)
    assert result.reading_order[0].status == "ordered"
    assert content(result) == ["First", "Second", "Third"]


def test_spanning_heading_separates_column_bands():
    entries = [
        (55, 110, "Upper left first"),
        (400, 110, "Upper right first"),
        (55, 140, "Upper left second"),
        (400, 140, "Upper right second"),
        (150, 220, "A full width heading crossing the column gutter"),
        (55, 300, "Lower left first"),
        (400, 300, "Lower right first"),
        (55, 330, "Lower left second"),
        (400, 330, "Lower right second"),
    ]
    with placed(entries) as doc:
        result = extract(doc)
    assert result.reading_order[0].status == "ordered"
    assert content(result) == [entries[i][2] for i in [0, 2, 1, 3, 4, 5, 7, 6, 8]]


@pytest.mark.parametrize("cropped", [False, True])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_original_geometry_links_and_page_state_survive(cropped, rotation):
    entries = [
        (55, 110, "Left first"),
        (400, 110, "Right first"),
        (55, 150, "Left second"),
        (400, 150, "Right second"),
    ]
    with placed(entries, cropped=cropped, rotation=rotation) as doc:
        page = doc[0]
        # Rectangles supplied to insert_link use unrotated source coordinates.
        box = page.search_for("Left first")[0]
        page.insert_link(
            {"kind": pymupdf.LINK_URI, "from": box, "uri": "https://source.example/item"}
        )
        doc = pymupdf.open(stream=doc.tobytes(), filetype="pdf")
        before = native_lines(doc)
        crop_before = tuple(doc[0].cropbox)
        legacy = _extract_open_document(doc, load_patterns(), Settings(_env_file=None))
        result = extract(doc)
        assert native_lines(doc) == before
        assert tuple(doc[0].cropbox) == crop_before and doc[0].rotation == rotation
        assert result.uri_links == legacy.uri_links
        assert result.geometry == legacy.geometry
        assert result.images == legacy.images and result.tables == legacy.tables
        # Row-first native blocks span both columns; the new output correctly
        # splits those groups. Each original line keeps its exact rectangle.
        assert Counter((b.page, b.bbox, b.text) for b in result.blocks) == before
        assert result.reading_order[0].status == "ordered"
        assert content(result) == [entries[i][2] for i in [0, 2, 1, 3]]
        doc.close()


def test_repeated_text_is_not_deduplicated():
    with placed(
        [
            (55, 110, "Repeated"),
            (350, 110, "Repeated"),
            (55, 150, "Repeated"),
            (350, 150, "Repeated"),
        ]
    ) as doc:
        result = extract(doc)
    assert content(result) == ["Repeated"] * 4
    assert result.reading_order[0].source_line_count == 4


@pytest.mark.parametrize("issue", ["overlap", "direction", "budget"])
def test_uncertain_layout_retains_source_and_records_reason(issue):
    with placed([(70, 110, "First"), (70, 140, "Second")]) as doc:
        if issue == "overlap":
            doc[0].insert_text((70, 110), "Conflicting overprint", fontsize=10)
        if issue == "direction":
            doc[0].insert_text((400, 250), "Vertical", fontsize=10, rotate=90)
        legacy = _extract_open_document(doc, load_patterns(), Settings(_env_file=None))
        result = extract(
            doc, **({"ingest_reading_order_max_lines_per_page": 1} if issue == "budget" else {})
        )
    assert result.reading_order[0].status == "unresolved"
    assert result.reading_order[0].reason
    assert [(b.text, b.bbox) for b in result.blocks] == [(b.text, b.bbox) for b in legacy.blocks]


def test_table_uncertainty_does_not_remove_native_table_arithmetic_source():
    with placed(
        [
            (70, 100, "Name"),
            (180, 100, "Value"),
            (70, 130, "Count"),
            (180, 130, "3"),
            (350, 100, "Unordered side prose"),
            (55, 220, "Left prose"),
            (350, 220, "Right prose"),
        ]
    ) as doc:
        page = doc[0]
        for x in [60, 170, 280]:
            page.draw_line((x, 80), (x, 140))
        for y in [80, 110, 140]:
            page.draw_line((60, y), (280, y))
        result = extract(doc)
    assert result.reading_order[0].status == "unresolved"
    assert result.reading_order[0].reason == "native_table_flow_interleaved"
    assert result.tables[0].rows == [["Name", "Value"], ["Count", "3"]]


def test_old_extraction_is_not_retrospectively_marked_ordered():
    with placed([(70, 110, "Legacy")]) as doc:
        result = _extract_open_document(
            doc, load_patterns(), Settings(_env_file=None, ingest_reading_order_strategy="legacy")
        )
    assert result.reading_order is None


def test_native_mixed_furniture_block_keeps_its_inclusion_semantics(monkeypatch):
    import app.ingest.pdf as pdf

    def mark_one(lines_by_page, *_args):
        lines_by_page[1][0].is_furniture = True

    monkeypatch.setattr(pdf, "_mark_furniture", mark_one)
    with placed(
        [
            (55, 110, "Left header"),
            (350, 110, "Right header"),
            (55, 150, "Left body"),
            (350, 150, "Right body"),
        ]
    ) as doc:
        legacy = _extract_open_document(doc, load_patterns(), Settings(_env_file=None))
        result = extract(doc)
    assert all(not block.is_furniture for block in legacy.blocks)
    assert all(not block.is_furniture for block in result.blocks)
    assert Counter(content(result)) == Counter(content(legacy))


@pytest.mark.parametrize("corruption", ["missing", "extra", "text", "bbox", "font"])
def test_proposal_requires_exact_source_line_occurrence_inventory(monkeypatch, corruption):
    import copy

    original = pymupdf.Page.get_text

    def damaged(page, *args, **kwargs):
        value = original(page, *args, **kwargs)
        if not kwargs.get("flags", 0) & pymupdf.TEXT_SEGMENT:
            return value
        value = copy.deepcopy(value)
        stack = list(value["blocks"])
        while stack:
            node = stack.pop()
            if node.get("lines"):
                line = node["lines"][0]
                if corruption == "missing":
                    node["lines"].pop(0)
                elif corruption == "extra":
                    node["lines"].append(copy.deepcopy(line))
                elif corruption == "text":
                    line["spans"][0]["text"] += " invented"
                elif corruption == "bbox":
                    line["bbox"] = (0, 0, 1, 1)
                else:
                    line["spans"][0]["font"] = "Substituted"
                break
            stack.extend(node.get("blocks", []))
        return value

    monkeypatch.setattr(pymupdf.Page, "get_text", damaged)
    with placed([(55, 110, "Original one"), (55, 150, "Original two")]) as doc:
        result = extract(doc)
    assert result.reading_order[0].status == "unresolved"
    assert result.reading_order[0].reason == "segment_identity_mismatch"
    assert content(result) == ["Original one", "Original two"]


@pytest.mark.parametrize(
    "budget", ["ingest_reading_order_max_nodes_per_page", "ingest_reading_order_max_depth"]
)
def test_region_budgets_retain_source_and_report_exhaustion(budget):
    with placed(
        [
            (55, 110, "Left one"),
            (350, 110, "Right one"),
            (55, 150, "Left two"),
            (350, 150, "Right two"),
        ]
    ) as doc:
        result = extract(doc, **{budget: 1})
    assert result.reading_order[0].status == "unresolved"
    assert result.reading_order[0].reason.endswith("budget_exhausted")
    assert len(content(result)) == 4


@pytest.mark.parametrize("offset", [2.1, 3, 8, 11.9])
def test_cross_row_overprint_is_not_an_ordered_paragraph(offset):
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((70, 110), "Primary claim printed here.", fontsize=12)
        page.insert_text((85, 110 + offset), "Conflicting diagonal overprint.", fontsize=12)
        result = extract(doc)
    assert result.reading_order[0].status == "unresolved"
    assert result.reading_order[0].reason == "overlapping_text"


def test_ordinary_font_box_overlap_is_not_ink_collision():
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((70, 110), "First readable line.", fontsize=12)
        page.insert_text((70, 124), "Second readable line.", fontsize=12)
        result = extract(doc)
    assert result.reading_order[0].status == "ordered"


@pytest.mark.parametrize("rows", [2, 3])
def test_narrow_gutter_survives_native_row_grouping(rows):
    entries = [
        (x, 110 + row * 40, f"{name} item {row}")
        for row in range(rows)
        for x, name in [(70, "Left"), (135, "Right")]
    ]
    with placed(entries) as doc:
        result = extract(doc)
    assert result.reading_order[0].status == "ordered"
    assert content(result) == [
        f"{name} item {row}" for name in ["Left", "Right"] for row in range(rows)
    ]


def test_fingerprint_preserving_single_leaf_is_not_trusted_as_a_flow(monkeypatch):
    original = pymupdf.Page.get_text

    def collapsed(page, *args, **kwargs):
        value = original(page, *args, **kwargs)
        if kwargs.get("flags", 0) & pymupdf.TEXT_SEGMENT:
            stack, raw_lines = list(value["blocks"]), []
            while stack:
                node = stack.pop()
                raw_lines.extend(node.get("lines", []))
                stack.extend(node.get("blocks", []))
            value["blocks"] = [{"lines": raw_lines}]
        return value

    monkeypatch.setattr(pymupdf.Page, "get_text", collapsed)
    with placed(
        [
            (55, 110, "Left first"),
            (400, 110, "Right first"),
            (55, 150, "Left second"),
            (400, 150, "Right second"),
        ]
    ) as doc:
        result = extract(doc)
    assert result.reading_order[0].status == "ordered"
    assert content(result) == ["Left first", "Left second", "Right first", "Right second"]


def test_mixed_header_is_an_atomic_margin_block(monkeypatch):
    import app.ingest.pdf as pdf

    def mark_one(lines_by_page, *_args):
        lines_by_page[1][0].is_furniture = True

    monkeypatch.setattr(pdf, "_mark_furniture", mark_one)
    entries = [
        (55, 80, "Left header"),
        (350, 80, "Right header"),
        (55, 150, "Left first"),
        (350, 150, "Right first"),
        (55, 180, "Left second"),
        (350, 180, "Right second"),
    ]
    with placed(entries) as doc:
        result = extract(doc)
    assert result.reading_order[0].status == "ordered"
    assert content(result) == [entries[i][2] for i in [0, 1, 2, 4, 3, 5]]
    assert result.blocks[0].text == "Left header\nRight header"
    assert not result.blocks[0].is_furniture


def test_line_cap_precedes_hashing_and_segmenter(monkeypatch):
    import app.ingest.reading_order as ordering

    def forbidden(*_a, **_kw):
        raise AssertionError("Unadmitted inventory must not be hashed")

    monkeypatch.setattr(ordering, "_line_hash", forbidden)
    with placed([(70, 110, "First"), (70, 140, "Second")]) as doc:
        result = extract(doc, ingest_reading_order_max_lines_per_page=1)
    assert result.reading_order[0].reason == "line_budget_exhausted"
    assert result.reading_order[0].source_inventory_sha256 is None


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_original_inventory_is_unresolved_not_an_exception(value):
    from app.ingest.reading_order import propose_regions
    from app.ingest.schemas import Line

    with placed([(70, 110, "Original")]) as doc:
        raw = doc[0].get_text("dict")["blocks"]
        raw[0]["lines"][0]["spans"][0]["size"] = value
        lines = [Line(1, 0, (70, 100, 120, 113), "Original", 10, 0, source_id="1:0:0")]
        proposal = propose_regions(doc[0], raw, lines, Settings(_env_file=None))
    assert proposal.problem == "source_inventory_invalid"
    assert proposal.inventory_hash is None


@pytest.mark.parametrize("repeated", [False, True])
def test_atomic_mixed_margin_is_still_validated_as_canonical_source(repeated):
    from app.ingest.quality import reading_order_issues

    with pymupdf.open() as doc:
        for label in ["Alpha", "Beta", "Gamma"]:
            page = doc.new_page(width=600, height=800)
            header = "Repeated journal header" if repeated else f"{label} journal header"
            page.insert_text((70, 60), header, fontsize=12)
            page.insert_text((85, 63), f"Overprinted {label} statement.", fontsize=12)
            page.insert_text((70, 200), f"Ordinary {label} body.", fontsize=12)
        result = extract(doc)
    assert all(record.status == "unresolved" for record in result.reading_order)
    assert all(record.reason == "overlapping_text" for record in result.reading_order)
    assert len(reading_order_issues(result)) == 3
    assert any("Overprinted" in block.text and not block.is_furniture for block in result.blocks)


@pytest.mark.parametrize("adverse", [None, "no_common_x", "vertical_overlap"])
def test_nested_interleaved_regions_require_original_single_column_proof(adverse):
    from app.ingest.reading_order import Proposal, Region, order_page
    from app.ingest.schemas import Line

    boxes = [(70, 90, 170, 105), (70, 120, 280, 135), (70, 150, 220, 165)]
    if adverse == "no_common_x":
        # Pairwise/aggregate overlap does not prove a common column.
        boxes = [(70, 90, 170, 105), (160, 120, 280, 135), (180, 150, 260, 165)]
    elif adverse == "vertical_overlap":
        boxes[1] = (70, 100, 280, 135)
    lines = [
        Line(1, index, box, f"Source {index}", 10, 0, source_id=f"1:{index}:0")
        for index, box in enumerate(boxes)
    ]
    ids = [line.source_id for line in lines]
    proposal = Proposal(
        Region(
            [Region([Region([], [ids[0]]), Region([], [ids[2]])], []), Region([], [ids[1]])], []
        ),
        dict(zip(ids, [100, 130, 160], strict=False)),
        set(),
        "unchanged-source-hash",
    )
    before = [vars(line).copy() for line in lines]
    ordered, record = order_page(
        1, lines, proposal, has_tables=False, settings=Settings(_env_file=None)
    )
    assert [vars(line) for line in lines] == before
    assert record.source_inventory_sha256 == "unchanged-source-hash"
    assert ordered == lines
    if adverse:
        assert record.status == "unresolved"
        assert record.reason == "ambiguous_region_overlap"
    else:
        assert record.status == "ordered"


def test_original_structural_bibliography_preserves_source_and_two_references(tmp_path):
    from app.ingest.references import extract_references
    from tests.test_checks_structural_live import _abstract_pdf

    with pymupdf.open(_abstract_pdf(tmp_path)) as doc:
        inventory = native_lines(doc)
        original = _extract_open_document(
            doc, load_patterns(), Settings(_env_file=None, ingest_reading_order_strategy="legacy")
        )
        result = extract(doc)
        assert native_lines(doc) == inventory
    assert result.geometry == original.geometry
    assert result.tables == original.tables and result.images == original.images
    assert Counter((b.page, b.bbox, b.text) for b in result.blocks) == Counter(
        (b.page, b.bbox, b.text) for b in original.blocks
    )
    assert all(record.status == "ordered" for record in result.reading_order)
    assert all(record.source_inventory_sha256 for record in result.reading_order)
    assert len(extract_references(result, load_patterns())) == 2
