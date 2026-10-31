"""BUG-566: rubric grids reach the decomposer cell by cell, every stored level
descriptor is the document's own words from one cell, and repeated copies are
read once with filled-in values withheld. No network and no database: the
model is scripted and a spy session stands in for the audit log."""

import re
from pathlib import Path
from typing import Any

import pytest

from app.config import get_settings
from app.ingest.schemas import ExtractionResult, SectionTree, TableBlock, TextBlock
from app.rubric.decompose import raw_text_for_decomposition
from app.rubric.schemas import ParsedCriterion, ParsedLevel
from app.rubric.source import DecompositionSource, build_decomposition_source
from app.rubric.validate import attempt_decomposition
from app.rubric.verbatim import match_form, verify_against_source
from tests.rubric_grid_pdf import HEADER, STUDENTS, grid_pdf
from tests.rubric_grid_pdf import ROWS as PDF_ROWS
from tests.test_rubric_validate import ScriptedLLM, _SpySession

SETTINGS = get_settings()
RUBRIC_PDF = Path(__file__).resolve().parents[2] / "Rubric-for-Oral-Presentation.pdf"
rubric_pdf_only = pytest.mark.skipif(
    not RUBRIC_PDF.exists(),
    reason="the reproduction rubric is local-only (D-007); run this suite locally",
)

NAMES = ["Beginner", "Acceptable", "Proficient", "Exemplary"]

# A grid shaped like the one that failed: some Beginner cells are one short
# sentence, so in flattened reading order they sit above their own row label
# and beside the neighbouring row's cells.
GRID = [
    ["CRITERIA", "BEGIN ER 1", "ACCEPTABL E 2", "PROFICIEN T 3", "", "EXEMPLAR Y 4", "SCORE"],
    [
        "Thesis",
        "The thesis is unclear.",
        "The thesis is stated but drifts across the chapters.",
        "The thesis is stated in one sentence.",
        "",
        "The thesis is stated in one sentence and frames every chapter.",
        "",
    ],
    [
        "Evidence",
        "Claims lack support. Data are missing or misread.",
        "Some claims are supported. Data are summarised loosely.",
        "Most claims are supported by correctly read data.",
        "",
        "Every claim is supported by correctly read, well chosen data.",
        "",
    ],
    [
        "Citations",
        "Sources are rarely cited.",
        "Sources are cited now and then, often without page numbers.",
        "Sources are cited consistently, with occasional omissions.",
        "",
        "Every borrowed idea is cited completely and consistently.",
        "",
    ],
]


def _block(text: str, page: int | None = 1, bbox=None, paragraph=None) -> TextBlock:
    return TextBlock(
        page=page, paragraph=paragraph, bbox=bbox, text=text, max_font_size=11.0, bold_ratio=0.0
    )


def _extraction(
    tables: list[TableBlock], blocks: list[TextBlock], *, anchor: str = "page"
) -> ExtractionResult:
    return ExtractionResult(
        page_count=max((b.page or 0 for b in blocks), default=0),
        anchor_kind=anchor,
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        section_tree=SectionTree(source="none", sections=[]),
        blocks=blocks,
        images=[],
        tables=tables,
    )


def _pdf_grid(rows: list[list[str]], page: int = 1, header: list[str] | None = None):
    """A one-page PDF extraction: a header line above the table, then the
    table's fragments in the interleaved order flattening produced."""
    table = TableBlock(page=page, bbox=(0.0, 100.0, 600.0, 700.0), rows=rows)
    inside = (10.0, 200.0, 50.0, 210.0)
    fragments = [
        rows[1][1],  # the short Beginner cell comes out above its row label
        rows[1][0],
        rows[1][3],
        rows[1][2],
        rows[1][5],
        rows[2][1].split(". ")[0] + ".",  # the next row's first sentence
        *rows[2][1].split(". ")[1:],
        rows[2][0],
    ]
    blocks = [_block(line, page, (10.0, 20.0, 300.0, 30.0)) for line in header or []]
    blocks += [_block(text, page, inside) for text in fragments]
    return table, blocks


def _response(rows: list[list[str]], *, retype=lambda s: s) -> dict[str, Any]:
    """What a faithful model returns: each row's label and its four level
    cells, optionally retyped (straight apostrophes, joined split words)."""
    criteria = []
    for row in rows[1:]:
        cells = [cell for cell in row[1:] if cell][:4]
        criteria.append(
            {
                "text": f"{row[0]}: graded on the source's four levels",
                "type": "semantic",
                "evidence_needed": "The whole manuscript.",
                "weight": 1,
                "levels": [
                    {"level": i, "name": NAMES[i - 1], "descriptor": retype(cell), "points": i}
                    for i, cell in enumerate(cells, start=1)
                ],
            }
        )
    return {"criteria": criteria}


def _shifted(rows: list[list[str]], invented: str) -> dict[str, Any]:
    """The production failure: Beginner and Acceptable merged into level 1
    (plus the next row's first sentence), Proficient stored as level 2, an
    invented sentence as level 3."""
    response = _response(rows)
    first = response["criteria"][0]["levels"]
    next_first_sentence = rows[2][1].split(". ")[0] + "."
    first[0]["descriptor"] = f"{rows[1][1]} {rows[1][2]} {next_first_sentence}"
    first[1]["descriptor"] = rows[1][3]
    first[2]["descriptor"] = invented
    return response


def _criteria(response: dict[str, Any]) -> list[ParsedCriterion]:
    return [ParsedCriterion.model_validate(c) for c in response["criteria"]]


def _verify(criteria, source, settings=SETTINGS) -> tuple[list[ParsedCriterion], list[str]]:
    verified, issues = verify_against_source(criteria, source, settings)
    return verified, [issue.message for issue in issues]


def _codes(criteria, source) -> list[tuple[str, int, int | None]]:
    return [
        (i.code, i.criterion, i.level) for i in verify_against_source(criteria, source, SETTINGS)[1]
    ]


# --- what the model reads ---------------------------------------------------


def test_a_recovered_grid_reaches_the_model_cell_by_cell():
    table, blocks = _pdf_grid(GRID, header=["Rubric for the written paper"])
    source = build_decomposition_source(_extraction([table], blocks), SETTINGS)
    assert "Rubric for the written paper" in source.prompt_text
    # Each cell sits in its own row; the empty hairline column is dropped.
    assert "T1.R2.C2: The thesis is unclear." in source.prompt_text
    assert "T1.R3.C2: Claims lack support. Data are missing or misread." in source.prompt_text
    assert "T1.R2.C5: The thesis is stated in one sentence and frames" in source.prompt_text
    # The interleaved fragments are not sent a second time as loose text.
    assert source.outside_lines == ("Rubric for the written paper",)
    assert source.prompt_text.index("Rubric for") < source.prompt_text.index("[TABLE T1")


def test_a_prose_rubric_reads_exactly_as_before():
    lines = ["Format requirements", "Margins: 1 inch on every side.", "Font: 12 point serif."]
    blocks = [_block(line) for line in lines]
    extraction = _extraction([], blocks)
    source = build_decomposition_source(extraction, SETTINGS)
    assert source.prompt_text == raw_text_for_decomposition(blocks)
    assert source.cells == ()
    assert source.gate_blocks == blocks


def test_a_docx_table_reaches_the_model_between_its_paragraphs():
    """DOCX table text never reached the decomposer before: its cells live in
    `tables`, not in the paragraph blocks."""
    blocks = [
        _block("Part A: written paper", page=None, paragraph=0),
        _block("Part B: notes", page=None, paragraph=2),
    ]
    table = TableBlock(paragraph=1, rows=GRID)
    source = build_decomposition_source(_extraction([table], blocks, anchor="paragraph"), SETTINGS)
    text = source.prompt_text
    assert (
        text.index("Part A") < text.index("T1.R2.C2: The thesis is unclear.") < text.index("Part B")
    )


def test_a_table_too_small_to_be_a_grid_stays_as_text():
    blocks = [_block("Title page"), _block("Name only")]
    table = TableBlock(page=1, bbox=(0.0, 0.0, 1.0, 1.0), rows=[["Name only"]])
    source = build_decomposition_source(_extraction([table], blocks), SETTINGS)
    assert source.cells == ()
    assert "Name only" in source.prompt_text


# --- repeated copies and filled-in values -----------------------------------


def _copies():
    header = ["Rating sheet", "Name of student: _", "Section: A1"]
    first, first_blocks = _pdf_grid(GRID, page=1, header=header)
    filled = [row[:] for row in GRID]
    filled[1][6] = "3"  # a score written on the second copy only
    second, second_blocks = _pdf_grid(
        filled, page=2, header=["Rating sheet", "Name of student: _Dela Cruz, Juana", "Section: A1"]
    )
    return _extraction([first, second], first_blocks + second_blocks)


def test_a_grid_repeated_per_student_is_read_once_without_the_filled_in_values():
    source = build_decomposition_source(_copies(), SETTINGS)
    marker = SETTINGS.rubric_withheld_value_marker
    assert source.prompt_text.count("[TABLE ") == 1
    assert source.copies_merged == 1
    assert "repeats on page(s) 2" in source.prompt_text
    assert "Dela Cruz" not in source.prompt_text
    assert "Juana" not in source.prompt_text
    assert f"T1.R2.C6: {marker}" in source.prompt_text  # the differing score cell
    assert "Name of student: _" in source.prompt_text
    assert source.prompt_text.count("Rating sheet") == 1
    assert any("Dela Cruz" in value for value in source.withheld)
    assert all(value not in source.prompt_text for value in source.withheld)
    # Nor can a withheld value pass the source check.
    assert all("Dela Cruz" not in line for line in source.outside_lines)
    assert all("Dela Cruz" not in cell.text for cell in source.cells)


def test_a_page_that_only_shares_a_letterhead_keeps_its_own_text():
    first, first_blocks = _pdf_grid(GRID, page=1, header=["Institute letterhead"])
    other_rows = [["Item", "Rule"], ["Margins", "One inch"], ["Font", "Twelve point"]]
    second = TableBlock(page=2, bbox=(0.0, 100.0, 600.0, 700.0), rows=other_rows)
    second_blocks = [
        _block("Institute letterhead", 2, (10.0, 20.0, 300.0, 30.0)),
        _block("Format checklist for the final copy", 2, (10.0, 40.0, 300.0, 50.0)),
    ]
    source = build_decomposition_source(
        _extraction([first, second], first_blocks + second_blocks), SETTINGS
    )
    assert source.prompt_text.count("[TABLE ") == 2
    assert "Format checklist for the final copy" in source.prompt_text


# --- the source check -------------------------------------------------------


def _grid_source() -> DecompositionSource:
    table, blocks = _pdf_grid(GRID)
    return build_decomposition_source(_extraction([table], blocks), SETTINGS)


def test_a_faithful_draft_is_stored_byte_for_byte_as_the_cells():
    source = _grid_source()
    retyped = _response(GRID, retype=lambda s: s.replace("summarised", "summa-\nrised"))
    criteria, issues = _verify(_criteria(retyped), source, SETTINGS)
    assert issues == []
    for criterion, row in zip(criteria, GRID[1:], strict=True):
        assert [level.descriptor for level in criterion.levels] == [c for c in row[1:] if c][:4]


def test_the_production_shift_is_reported_not_stored_as_parsed():
    source = _grid_source()
    _, issues = _verify(
        _criteria(_shifted(GRID, "The thesis is clearly stated.")), source, SETTINGS
    )
    text = " ".join(issues)
    assert "'Thesis: graded" in text
    assert "joins text from more than one place" in text
    assert "not in the original document" in text


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda lv: lv[0].update(descriptor="The thesis is unclear and unfocused."), "not in the"),
        (lambda lv: lv[3].update(descriptor="stated in one sentence and frames"), "part of"),
        (
            lambda lv: lv[0].update(descriptor="The thesis is unclear. Claims lack support."),
            "joins text",
        ),
        # Two levels swapped: right cells, wrong order.
        (
            lambda lv: (
                lv[1].update(descriptor=GRID[1][3]),
                lv[2].update(descriptor=GRID[1][2]),
            ),
            "sits under the column",
        ),
        # A level taken from the next row.
        (lambda lv: lv[0].update(descriptor=GRID[2][1]), "cell of the table row"),
    ],
)
def test_each_way_a_descriptor_can_leave_its_cell_is_named(mutate, expected):
    response = _response(GRID)
    mutate(response["criteria"][0]["levels"])
    _, issues = _verify(_criteria(response), _grid_source(), SETTINGS)
    assert any(expected in issue for issue in issues), issues


def test_a_level_left_out_of_its_row_is_named():
    response = _response(GRID)
    levels = response["criteria"][0]["levels"]
    del levels[1]
    for i, level in enumerate(levels, start=1):
        level["level"] = i
    _, issues = _verify(_criteria(response), _grid_source(), SETTINGS)
    assert any("left out" in issue for issue in issues), issues


def test_two_criteria_cannot_share_a_cell():
    response = _response(GRID)
    response["criteria"][2]["levels"] = response["criteria"][1]["levels"]
    _, issues = _verify(_criteria(response), _grid_source(), SETTINGS)
    assert any("same table cell" in issue for issue in issues), issues


def test_a_criterion_named_for_another_row_is_named():
    response = _response(GRID)
    response["criteria"][0]["text"] = "Citations: graded on the source's four levels"
    response["criteria"][0]["evidence_needed"] = "The reference list."
    _, issues = _verify(_criteria(response), _grid_source(), SETTINGS)
    assert any("levels of the table row 'Thesis'" in issue for issue in issues), issues


def test_a_grid_with_its_best_level_first_is_accepted():
    flipped = [[row[0], *reversed(row[1:6]), row[6]] for row in GRID]
    table, blocks = _pdf_grid(flipped)
    source = build_decomposition_source(_extraction([table], blocks), SETTINGS)
    response = _response(GRID)  # levels still ascend Beginner -> Exemplary
    criteria, issues = _verify(_criteria(response), source, SETTINGS)
    assert issues == []
    assert criteria[0].levels[0].descriptor == "The thesis is unclear."


def test_a_grid_laid_out_with_levels_as_rows_is_accepted():
    transposed = [list(column) for column in zip(*GRID, strict=True)]
    table = TableBlock(page=1, bbox=(0.0, 100.0, 600.0, 700.0), rows=transposed)
    source = build_decomposition_source(_extraction([table], []), SETTINGS)
    criteria, issues = _verify(_criteria(_response(GRID)), source, SETTINGS)
    assert issues == []
    assert criteria[2].levels[3].descriptor == GRID[3][5]


def test_prose_levels_must_be_one_passage_of_the_text():
    lines = [
        "Discussion of findings, rated as follows.",
        "Weak: findings are restated without",
        "comparison to prior work.",
        "Strong: findings are compared critically with prior work.",
    ]
    source = DecompositionSource.from_text([_block(x) for x in lines], "\n".join(lines))

    def criterion(weak: str) -> ParsedCriterion:
        return ParsedCriterion(
            text="Findings are discussed against prior literature",
            type="semantic",
            weight=1,
            levels=[
                ParsedLevel(level=1, name="Weak", descriptor=weak, points=1),
                ParsedLevel(
                    level=2,
                    name="Strong",
                    descriptor="findings are compared critically with prior work.",
                    points=2,
                ),
            ],
        )

    wrapped = "findings are restated without comparison to prior work."
    assert verify_against_source([criterion(wrapped)], source, SETTINGS)[1] == []
    _, issues = _verify([criterion("findings are vague.")], source, SETTINGS)
    assert len(issues) == 1 and "not in the original document" in issues[0]


def test_criteria_without_levels_are_untouched():
    plain = ParsedCriterion(text="Has an abstract, in my own words", type="structural", weight=1)
    criteria, issues = _verify([plain], _grid_source(), SETTINGS)
    assert criteria == [plain] and issues == []


def test_matching_ignores_only_spacing_case_punctuation_and_compatibility_forms():
    assert match_form("The paper’s eﬃcient in-\ndepth ­review") == match_form(
        "the paper's efficient indepth review"
    )
    assert match_form("a n academic paper") == match_form("an academic paper")
    assert match_form("purpose is clear") != match_form("purpose is unclear")


# --- layouts a correct draft must pass, and drafts that must not --------------


def _source(rows: list[list[str]], *, page: int = 1) -> DecompositionSource:
    table = TableBlock(page=page, bbox=(0.0, 100.0, 600.0, 700.0), rows=rows)
    return build_decomposition_source(_extraction([table], []), SETTINGS)


def test_each_stored_level_records_the_cell_it_came_from():
    criteria, _ = _verify(_criteria(_response(GRID)), _grid_source())
    source = criteria[1].levels[3].source
    assert source.match == "exact"
    assert (source.page, source.table, source.row, source.col) == (1, 1, 3, 5)
    assert source.cell_text == GRID[2][5]


def test_a_model_cannot_supply_its_own_provenance():
    response = _response(GRID)
    response["criteria"][0]["levels"][0]["descriptor"] = "Invented."
    response["criteria"][0]["levels"][0]["source"] = {"match": "exact", "row": 2, "col": 2}
    from app.rubric.decompose import _without_point_values

    parsed = [_without_point_values(c) for c in _criteria(response)]
    criteria, _ = _verify(parsed, _grid_source())
    assert criteria[0].levels[0].source.match == "unmatched"


def test_rows_that_repeat_the_same_cell_text_each_keep_their_own_row():
    """A scale whose cells read the same on every row must not pin every
    criterion to the first such row."""
    scale = ["Needs improvement (1)", "Fair (2)", "Good (3)", "Excellent (4)"]
    rows = [["Criterion", "1", "2", "3", "4"]] + [
        [label, *scale] for label in ["Introduction", "Methodology", "Results", "Discussion"]
    ]
    response = _response(rows)
    assert _codes(_criteria(response), _source(rows)) == []


def test_a_header_row_that_repeats_the_descriptors_is_not_mistaken_for_a_row():
    rows = [
        ["Area", "Weak", "Strong"],
        ["Thesis", "Weak", "Strong"],
        ["Method", "Unjustified.", "Justified."],
    ]
    response = _response(rows)
    for criterion, row in zip(response["criteria"], rows[1:], strict=True):
        criterion["levels"] = [
            {"level": i, "name": rows[0][i], "descriptor": row[i], "points": i} for i in (1, 2)
        ]
    assert _codes(_criteria(response), _source(rows)) == []


def test_one_row_with_its_levels_reversed_is_named():
    response = _response(GRID)
    levels = response["criteria"][1]["levels"]
    for level, cell in zip(levels, reversed([c for c in GRID[2][1:] if c][:4]), strict=True):
        level["descriptor"] = cell
    codes = _codes(_criteria(response), _grid_source())
    assert [c for c in codes if c[1] == 1] == [("wrong_column", 1, k) for k in range(1, 5)]


def test_a_level_under_a_header_with_other_points_is_named():
    """Every row reversed the same way passes the direction check, so the
    column headers' points are what catch it."""
    response = _response(GRID)
    for criterion, row in zip(response["criteria"], GRID[1:], strict=True):
        for level, cell in zip(
            criterion["levels"], reversed([c for c in row[1:] if c][:4]), strict=True
        ):
            level["descriptor"] = cell
    codes = _codes(_criteria(response), _grid_source())
    assert {code for code, _, _ in codes} == {"wrong_column"}
    assert len(codes) == 12  # 3 rows x 4 levels each under another header


def test_a_level_under_a_header_with_another_name_is_named():
    rows = [
        ["Criterion", "Beginning", "Developing", "Exemplary"],
        ["Thesis", "Vague.", "Stated.", "Sharp."],
        ["Method", "Unjustified.", "Partly justified.", "Justified."],
    ]
    response = _response(rows)
    for criterion in response["criteria"]:
        for level, name in zip(
            criterion["levels"], ["Beginning", "Developing", "Exemplary"], strict=True
        ):
            level["name"] = name
            level["points"] = 1
    swapped = response["criteria"][0]["levels"]
    swapped[0]["name"], swapped[2]["name"] = swapped[2]["name"], swapped[0]["name"]
    codes = _codes(_criteria(response), _source(rows))
    assert ("wrong_column", 0, 1) in codes and ("wrong_column", 0, 3) in codes


def test_swapping_two_similarly_named_rows_is_named():
    rows = [
        ["Criteria", "Low", "High"],
        ["Use of References", "Rarely cited.", "Always cited."],
        ["Quality of References", "Unreliable sources.", "Peer-reviewed sources."],
    ]
    response = _response(rows)
    first, second = response["criteria"]
    first["text"], second["text"] = second["text"], first["text"]
    codes = _codes(_criteria(response), _source(rows))
    assert ("wrong_row", 0, None) in codes and ("wrong_row", 1, None) in codes


def test_a_shared_scale_legend_is_accepted_for_every_criterion():
    legend = [
        ["Rating", "Description"],
        ["Poor", "Falls well short of the requirement."],
        ["Good", "Meets the requirement."],
        ["Excellent", "Exceeds the requirement."],
    ]
    descriptors = [row[1] for row in legend[1:]]
    criteria = [
        ParsedCriterion(
            text=name,
            type="semantic",
            weight=1,
            levels=[
                ParsedLevel(level=i, name=n, descriptor=d, points=i)
                for i, (n, d) in enumerate(
                    zip(["Poor", "Good", "Excellent"], descriptors, strict=True), 1
                )
            ],
        )
        for name in ["Introduction", "Methods", "Results"]
    ]
    assert _codes(criteria, _source(legend)) == []


def test_a_level_column_dropped_from_every_row_is_named():
    response = _response(GRID)
    for criterion in response["criteria"]:
        del criterion["levels"][1]
        for i, level in enumerate(criterion["levels"], start=1):
            level["level"] = i
    codes = _codes(_criteria(response), _grid_source())
    assert [code for code, _, _ in codes].count("level_left_out") == 3


def test_the_top_level_column_dropped_from_every_row_is_named():
    """An edge column is a level column when its header carries a score."""
    response = _response(GRID)
    for criterion in response["criteria"]:
        del criterion["levels"][3]
    codes = _codes(_criteria(response), _grid_source())
    assert [code for code, _, _ in codes].count("level_left_out") == 3


def _assert_no_orphan_levels(criteria, issues):
    """Every level that is not traced, or traced to the wrong place, carries
    an issue of its own; every traced level keeps its cell."""
    flagged = {(i.criterion, i.level) for i in issues}
    for index, criterion in enumerate(criteria):
        for level in criterion.levels or []:
            if level.source.match == "unmatched":
                assert (index, level.level) in flagged, (criterion.text, level.level)
            elif level.source.table is not None:
                assert level.source.page is not None and level.source.cell_text


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: _shifted_levels(c[0], GRID, "The thesis is clearly stated."),
        lambda c: c[0]["levels"][1].update(descriptor=GRID[1][3]),
        lambda c: c[0]["levels"][0].update(descriptor=GRID[2][1]),
        lambda c: [
            lv.update(descriptor=cell)
            for lv, cell in zip(
                c[1]["levels"], [GRID[2][5], GRID[2][3], GRID[2][2], GRID[2][1]], strict=True
            )
        ],
    ],
)
def test_no_level_is_left_unmatched_or_misplaced_without_its_own_issue(mutate):
    response = _response(GRID)
    mutate(response["criteria"])
    criteria, issues = verify_against_source(_criteria(response), _grid_source(), SETTINGS)
    assert issues
    _assert_no_orphan_levels(criteria, issues)
    misplaced = {(i.criterion, i.level) for i in issues if i.code in {"wrong_row", "wrong_column"}}
    for index, criterion in enumerate(criteria):
        for level, row in zip(criterion.levels, [GRID[index + 1]] * 4, strict=True):
            cells = [c for c in row[1:] if c][:4]
            if level.source.match == "exact" and level.descriptor != cells[level.level - 1]:
                assert (index, level.level) in misplaced, (index, level.level)


def _shifted_levels(criterion, rows, invented):
    first = criterion["levels"]
    first[0]["descriptor"] = f"{rows[1][1]} {rows[1][2]} {rows[2][1].split('. ')[0]}."
    first[1]["descriptor"] = rows[1][3]
    first[2]["descriptor"] = invented


def test_a_merged_span_drafted_as_one_level_is_not_a_left_out_level():
    rows = [
        ["Criterion", "Low", "Middle", "High"],
        ["Thesis", "Vague.", "Stated.", "Sharp."],
        ["Depth", "Shallow.", "Adequate.", "Adequate."],  # a DOCX span read twice
    ]
    response = _response(rows)
    response["criteria"][1]["levels"] = response["criteria"][1]["levels"][:2]
    assert _codes(_criteria(response), _source(rows)) == []


def test_a_level_cell_that_differs_between_copies_is_named_not_dropped():
    first = [row[:] for row in GRID]
    second = [row[:] for row in GRID]
    second[1][3] = "The thesis is stated in one sentence. ✓"
    tables = [
        TableBlock(page=p, bbox=(0.0, 100.0, 600.0, 700.0), rows=rows)
        for p, rows in ((1, first), (2, second))
    ]
    source = build_decomposition_source(_extraction(tables, []), SETTINGS)
    response = _response(GRID)
    response["criteria"][0]["levels"] = [
        lvl for lvl in response["criteria"][0]["levels"] if lvl["descriptor"] != GRID[1][3]
    ]
    for i, level in enumerate(response["criteria"][0]["levels"], start=1):
        level["level"] = i
    assert ("other", 0, None) in _codes(_criteria(response), source)
    marker = SETTINGS.rubric_withheld_value_marker
    response["criteria"][0]["levels"].insert(
        2, {"level": 3, "name": "Proficient", "descriptor": marker, "points": 3}
    )
    for i, level in enumerate(response["criteria"][0]["levels"], start=1):
        level["level"] = i
    assert ("other", 0, 3) in _codes(_criteria(response), source)


def test_a_passage_must_be_whole_words_and_end_at_a_boundary():
    lines = [
        "Purpose: The paper's purpose is clearly stated in a single sentence.",
        "Poorly: the purpose is missing.",
        "Weak (1) = the purpose is",
        "unclear; Strong (2) = the purpose is clear.",
    ]
    source = DecompositionSource.from_text([_block(x) for x in lines], "\n".join(lines))

    def levels(*descriptors: str) -> list[ParsedCriterion]:
        return [
            ParsedCriterion(
                text="Purpose",
                type="semantic",
                weight=1,
                levels=[
                    ParsedLevel(level=i, name=f"L{i}", descriptor=d, points=i)
                    for i, d in enumerate(descriptors, start=1)
                ],
            )
        ]

    ok = levels("the purpose is unclear", "the purpose is clear.")
    assert _codes(ok, source) == []
    assert _codes(levels("Poor", "the purpose is clear."), source)[0][0] == "not_in_source"
    prefix = levels("The paper's purpose is clearly stated.", "the purpose is clear.")
    assert _codes(prefix, source)[0][0] == "not_in_source"
    # Text may list a scale either way round, but not out of order.
    assert _codes(levels("the purpose is clear.", "the purpose is unclear"), source) == []
    shuffled = levels("the purpose is unclear", "the purpose is missing.", "the purpose is clear.")
    assert ("not_one_row", 0, None) in _codes(shuffled, source)


def test_an_unlabelled_name_line_on_a_copy_is_withheld():
    header_a = ["Panel rating sheet", "Juana Dela Cruz", "Section A1"]
    header_b = ["Panel rating sheet", "Pedro Santos", "Section A1"]
    first, first_blocks = _pdf_grid(GRID, page=1, header=header_a)
    second, second_blocks = _pdf_grid(GRID, page=2, header=header_b)
    source = build_decomposition_source(
        _extraction([first, second], first_blocks + second_blocks), SETTINGS
    )
    assert "Juana" not in source.prompt_text and "Pedro" not in source.prompt_text
    assert source.unread_copy_lines == 0


async def test_text_found_only_on_a_copy_is_reported_as_unread():
    header = [
        "Rating sheet for the written paper and its oral defense",
        "Section A1, second semester",
        "Panel members sign below after the defense",
    ]
    first, first_blocks = _pdf_grid(GRID, page=1, header=header)
    second, second_blocks = _pdf_grid(
        GRID, page=2, header=[*header, "Speaks for at most 40 minutes."]
    )
    source = build_decomposition_source(
        _extraction([first, second], first_blocks + second_blocks), SETTINGS
    )
    assert source.unread_copy_lines == 1
    assert "40 minutes" not in source.prompt_text
    outcome = await attempt_decomposition(
        _SpySession(),
        1,
        source.gate_blocks,
        source.prompt_text,
        ScriptedLLM([_response(GRID)]),
        SETTINGS,
        source=source,
    )
    assert outcome.needs_review and outcome.attempts == 1
    assert [d["code"] for d in outcome.issue_details] == ["other"]


def test_a_checklist_inside_one_cell_still_counts_as_separate_items():
    items = [f"• Requirement {i} applies to every chapter" for i in range(12)]
    rows = [["Section", "Requirements"], ["Format", " ".join(items)], ["Other", "None."]]
    table = TableBlock(page=1, bbox=(0.0, 100.0, 600.0, 700.0), rows=rows)
    inside = (10.0, 200.0, 50.0, 210.0)
    blocks = [_block("\n".join(items), 1, inside)]
    source = build_decomposition_source(_extraction([table], blocks), SETTINGS)
    from app.rubric.validate import folded_requirements

    two = [ParsedCriterion(text=f"Rule {i}", type="structural", weight=1) for i in range(2)]
    assert folded_requirements(two, source.checklist_text, SETTINGS) is not None


async def test_the_best_failed_draft_keeps_the_rows_rather_than_dropping_them():
    """Each retyped descriptor is its own issue; ranking by raw count would
    prefer a draft that dropped most rows."""
    full = _response(GRID)
    for criterion in full["criteria"]:
        criterion["levels"][0]["descriptor"] += " Retyped."
    short = {"criteria": [_response(GRID)["criteria"][0]]}
    table, blocks = _pdf_grid(GRID)
    source = build_decomposition_source(_extraction([table], blocks), SETTINGS)
    outcome = await attempt_decomposition(
        _SpySession(),
        1,
        source.gate_blocks,
        source.prompt_text,
        ScriptedLLM([full, short, short]),
        SETTINGS,
        source=source,
    )
    assert outcome.needs_review
    assert len(outcome.criteria) == 3


async def test_a_scaled_rubric_read_without_any_table_says_what_was_not_checked():
    """Flattened text can prove a descriptor's wording, not its row or level."""
    lines = [
        "Thesis. Weak: the thesis is vague. Strong: the thesis is sharp.",
        "Method. Weak: the method is unjustified. Strong: the method is justified.",
    ]
    blocks = [_block(x) for x in lines]

    def criterion(text: str, weak: str, strong: str) -> dict:
        return {
            "text": text,
            "type": "semantic",
            "weight": 1,
            "levels": [
                {"level": 1, "name": "Weak", "descriptor": weak, "points": 1},
                {"level": 2, "name": "Strong", "descriptor": strong, "points": 2},
            ],
        }

    response = {
        "criteria": [
            criterion("Thesis", "the thesis is vague.", "the thesis is sharp."),
            criterion("Method", "the method is unjustified.", "the method is justified."),
        ]
    }
    outcome = await attempt_decomposition(
        _SpySession(), 1, blocks, "\n".join(lines), ScriptedLLM([response]), SETTINGS
    )
    assert outcome.attempts == 1 and outcome.needs_review
    assert [d["code"] for d in outcome.issue_details] == ["other"]
    assert "No table structure" in outcome.issues[0]


async def test_issue_details_name_the_criterion_and_level():
    table, blocks = _pdf_grid(GRID)
    source = build_decomposition_source(_extraction([table], blocks), SETTINGS)
    outcome = await attempt_decomposition(
        _SpySession(),
        1,
        source.gate_blocks,
        source.prompt_text,
        ScriptedLLM([_shifted(GRID, "The thesis is clearly stated.")]),
        SETTINGS,
        source=source,
    )
    assert [d["message"] for d in outcome.issue_details] == outcome.issues
    assert [d["id"] for d in outcome.issue_details] == list(range(1, len(outcome.issues) + 1))
    invented = next(d for d in outcome.issue_details if d["code"] == "not_in_source")
    assert (invented["criterion"], invented["level"]) == (0, 3)


# --- through the retry loop ---------------------------------------------------


async def test_a_shifted_draft_is_retried_and_a_faithful_retry_is_parsed():
    table, blocks = _pdf_grid(GRID)
    source = build_decomposition_source(_extraction([table], blocks), SETTINGS)
    llm = ScriptedLLM([_shifted(GRID, "The thesis is clearly stated."), _response(GRID)])
    outcome = await attempt_decomposition(
        _SpySession(), 1, source.gate_blocks, source.prompt_text, llm, SETTINGS, source=source
    )
    assert outcome.attempts == 2
    assert not outcome.needs_review
    assert outcome.criteria[0].levels[0].descriptor == "The thesis is unclear."


async def test_a_draft_that_stays_shifted_is_left_for_the_instructor():
    """The guard is what keeps an invented descriptor from being stored as a
    plain "parsed" rubric: without it this outcome would pass every gate."""
    table, blocks = _pdf_grid(GRID)
    source = build_decomposition_source(_extraction([table], blocks), SETTINGS)
    llm = ScriptedLLM([_shifted(GRID, "The thesis is clearly stated.")])
    outcome = await attempt_decomposition(
        _SpySession(), 1, source.gate_blocks, source.prompt_text, llm, SETTINGS, source=source
    )
    assert outcome.needs_review
    assert outcome.attempts == SETTINGS.rubric_parse_max_retries + 1
    assert any("not in the original document" in issue for issue in outcome.issues)


async def test_the_retry_is_told_which_descriptor_failed():
    table, blocks = _pdf_grid(GRID)
    source = build_decomposition_source(_extraction([table], blocks), SETTINGS)

    class Spy(ScriptedLLM):
        def __init__(self, outcomes):
            super().__init__(outcomes)
            self.prompts: list[str] = []

        async def complete(self, prompt_type, prompt, **kwargs):
            self.prompts.append(prompt)
            return await super().complete(prompt_type, prompt, **kwargs)

    llm = Spy([_shifted(GRID, "The thesis is clearly stated."), _response(GRID)])
    await attempt_decomposition(
        _SpySession(), 1, source.gate_blocks, source.prompt_text, llm, SETTINGS, source=source
    )
    assert "PREVIOUS ATTEMPT FAILED" in llm.prompts[1]
    assert "level 'Proficient'" in llm.prompts[1]


# --- a real PDF grid, drawn in the test ------------------------------------


def _pdf_source(tmp_path, students=STUDENTS):
    from app.ingest import pdf as pdf_ingest

    path = grid_pdf(tmp_path / "grid.pdf", students)
    extraction = pdf_ingest.extract_document(str(path), SETTINGS)
    return extraction, build_decomposition_source(extraction, SETTINGS)


PDF_GRID = [HEADER[:5], *(row[:5] for row in PDF_ROWS)]


def test_a_drawn_pdf_grid_is_read_once_as_cells_without_the_names(tmp_path):
    extraction, source = _pdf_source(tmp_path)
    assert len(extraction.tables) == 3
    assert source.prompt_text.count("[TABLE ") == 1
    assert source.copies_merged == 2 and source.copy_pages == (1, 2, 3)
    assert "Dela Cruz" not in source.prompt_text and "Santos" not in source.prompt_text
    assert all(value not in source.prompt_text for value in source.withheld)
    assert "T1.R2.C2: The thesis is unclear." in source.prompt_text


def test_a_drawn_pdf_grid_stores_every_descriptor_as_its_cell(tmp_path):
    _, source = _pdf_source(tmp_path)
    retyped = _response(PDF_GRID, retype=lambda s: s.replace(",", " ,").upper())
    criteria, issues = _verify(_criteria(retyped), source, SETTINGS)
    assert issues == []
    stored = [level.descriptor for c in criteria for level in c.levels]
    assert stored == [cell for row in PDF_GRID[1:] for cell in row[1:]]
    assert {lv.source.page for c in criteria for lv in c.levels} == {1}


async def test_a_drawn_pdf_grid_with_the_production_shift_is_not_parsed(tmp_path):
    _, source = _pdf_source(tmp_path)
    llm = ScriptedLLM([_shifted(PDF_GRID, "The thesis is clearly stated.")])
    outcome = await attempt_decomposition(
        _SpySession(), 1, source.gate_blocks, source.prompt_text, llm, SETTINGS, source=source
    )
    assert outcome.needs_review
    codes = {(d["code"], d["criterion"], d["level"]) for d in outcome.issue_details}
    assert ("joins_cells", 0, 1) in codes and ("wrong_column", 0, 2) in codes


def test_a_drawn_pdf_grid_read_without_its_table_still_catches_an_invented_level(tmp_path):
    """A borderless or undetected grid reaches the check as flattened text."""
    extraction, _ = _pdf_source(tmp_path)
    source = build_decomposition_source(extraction.model_copy(update={"tables": []}), SETTINGS)
    assert source.cells == ()
    response = _response(PDF_GRID)
    response["criteria"][0]["levels"][2]["descriptor"] = "The thesis is stated."
    assert ("not_in_source", 0, 3) in _codes(_criteria(response), source)


# --- the real rubric that exposed this (local-only, D-007) --------------------


def _real_rows(source: DecompositionSource) -> list[list[str]]:
    """The grid as the extractor read it: every row whose label is followed by
    four level cells (header and total rows have fewer)."""
    by_row: dict[int, list[str]] = {}
    for cell in source.cells:
        by_row.setdefault(cell.row, []).append(cell.text)
    rows = [cells for cells in by_row.values() if len(cells) == 5]
    return [["CRITERIA", *NAMES], *rows]


@rubric_pdf_only
async def test_the_real_rubric_that_exposed_this():
    """One test, so the local-only file costs CI a single skip."""
    from app.ingest import pdf as pdf_ingest

    extraction = pdf_ingest.extract_document(str(RUBRIC_PDF), SETTINGS)
    source = build_decomposition_source(extraction, SETTINGS)
    rows = _real_rows(source)

    # One grid of nine rows, read once, with no student name sent.
    assert source.prompt_text.count("[TABLE ") == 1
    assert source.copies_merged == 2
    assert len(rows) - 1 == 9
    assert source.withheld, "the copies carry filled-in names"
    assert all(value not in source.prompt_text for value in source.withheld)

    # All 36 descriptors stored byte for byte as their cells, from the way the
    # model actually retyped them (straight apostrophes, a split word joined).
    def retype(text: str) -> str:
        return re.sub(r"\ba n\b", "an", text.replace("\u2019", "'"))

    criteria, issues = _verify(_criteria(_response(rows, retype=retype)), source, SETTINGS)
    assert issues == []
    stored = [level.descriptor for c in criteria for level in c.levels]
    assert len(stored) == 36
    assert stored == [cell for row in rows[1:] for cell in row[1:]]

    # The production Purpose row: traced cells keep their place, each
    # misplaced or untraced level carries its own issue.
    response = _response(rows)
    purpose = response["criteria"][1]["levels"]
    purpose[0]["descriptor"] = f"{rows[2][1]} {rows[3][1].split('. ')[0]}."
    purpose[1]["descriptor"] = rows[2][3]
    purpose[2]["descriptor"] = "The paper's purpose is clearly stated."
    checked, found = verify_against_source(_criteria(response), source, SETTINGS)
    _assert_no_orphan_levels(checked, found)
    levels = checked[1].levels
    assert [lv.source.match for lv in levels] == ["unmatched", "exact", "unmatched", "exact"]
    assert levels[1].source.page == 1 and levels[1].source.cell_text == rows[2][3]
    codes = {(i.code, i.level) for i in found if i.criterion == 1}
    # The invented level 3 is the start of the Proficient cell, cut short.
    assert codes == {("joins_cells", 1), ("wrong_column", 2), ("partial_cell", 3)}

    # Through the retry loop, the shift is never stored as parsed.
    llm = ScriptedLLM([_shifted(rows, "The introduction is clearly stated.")])
    outcome = await attempt_decomposition(
        _SpySession(), 1, source.gate_blocks, source.prompt_text, llm, SETTINGS, source=source
    )
    assert outcome.needs_review

    # A reversed row, and two similarly named rows swapped.
    response = _response(rows)
    for level, cell in zip(response["criteria"][1]["levels"], reversed(rows[2][1:]), strict=True):
        level["descriptor"] = cell
    assert ("wrong_column", 1, 1) in _codes(_criteria(response), source)
    response = _response(rows)
    use, quality = response["criteria"][6], response["criteria"][7]
    use["text"], quality["text"] = quality["text"], use["text"]
    codes = _codes(_criteria(response), source)
    assert ("wrong_row", 6, None) in codes and ("wrong_row", 7, None) in codes

    # Read without its table, production's invented level 3 is still caught.
    flat = build_decomposition_source(extraction.model_copy(update={"tables": []}), SETTINGS)
    response = _response(rows)
    response["criteria"][1]["levels"][2]["descriptor"] = "The paper's purpose is clearly stated."
    assert ("not_in_source", 1, 3) in _codes(_criteria(response), flat)
