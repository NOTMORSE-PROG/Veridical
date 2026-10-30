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
    criteria, issues = verify_against_source(_criteria(retyped), source, SETTINGS)
    assert issues == []
    for criterion, row in zip(criteria, GRID[1:], strict=True):
        assert [level.descriptor for level in criterion.levels] == [c for c in row[1:] if c][:4]


def test_the_production_shift_is_reported_not_stored_as_parsed():
    source = _grid_source()
    _, issues = verify_against_source(
        _criteria(_shifted(GRID, "The thesis is clearly stated.")), source, SETTINGS
    )
    text = " ".join(issues)
    assert "'Thesis: graded" in text
    assert "joins text from more than one table cell" in text
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
            "one row of the table",
        ),
        # A level taken from the next row.
        (lambda lv: lv[0].update(descriptor=GRID[2][1]), "one row of the table"),
    ],
)
def test_each_way_a_descriptor_can_leave_its_cell_is_named(mutate, expected):
    response = _response(GRID)
    mutate(response["criteria"][0]["levels"])
    _, issues = verify_against_source(_criteria(response), _grid_source(), SETTINGS)
    assert any(expected in issue for issue in issues), issues


def test_a_level_left_out_of_its_row_is_named():
    response = _response(GRID)
    levels = response["criteria"][0]["levels"]
    del levels[1]
    for i, level in enumerate(levels, start=1):
        level["level"] = i
    _, issues = verify_against_source(_criteria(response), _grid_source(), SETTINGS)
    assert any("left out" in issue for issue in issues), issues


def test_two_criteria_cannot_share_a_cell():
    response = _response(GRID)
    response["criteria"][2]["levels"] = response["criteria"][1]["levels"]
    _, issues = verify_against_source(_criteria(response), _grid_source(), SETTINGS)
    assert any("same table cell" in issue for issue in issues), issues


def test_a_criterion_named_for_another_row_is_named():
    response = _response(GRID)
    response["criteria"][0]["text"] = "Citations: graded on the source's four levels"
    response["criteria"][0]["evidence_needed"] = "The reference list."
    _, issues = verify_against_source(_criteria(response), _grid_source(), SETTINGS)
    assert any("levels of the table row 'Thesis'" in issue for issue in issues), issues


def test_a_grid_with_its_best_level_first_is_accepted():
    flipped = [[row[0], *reversed(row[1:6]), row[6]] for row in GRID]
    table, blocks = _pdf_grid(flipped)
    source = build_decomposition_source(_extraction([table], blocks), SETTINGS)
    response = _response(GRID)  # levels still ascend Beginner -> Exemplary
    criteria, issues = verify_against_source(_criteria(response), source, SETTINGS)
    assert issues == []
    assert criteria[0].levels[0].descriptor == "The thesis is unclear."


def test_a_grid_laid_out_with_levels_as_rows_is_accepted():
    transposed = [list(column) for column in zip(*GRID, strict=True)]
    table = TableBlock(page=1, bbox=(0.0, 100.0, 600.0, 700.0), rows=transposed)
    source = build_decomposition_source(_extraction([table], []), SETTINGS)
    criteria, issues = verify_against_source(_criteria(_response(GRID)), source, SETTINGS)
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
    _, issues = verify_against_source([criterion("findings are vague.")], source, SETTINGS)
    assert len(issues) == 1 and "not in the original document" in issues[0]


def test_criteria_without_levels_are_untouched():
    plain = ParsedCriterion(text="Has an abstract, in my own words", type="structural", weight=1)
    criteria, issues = verify_against_source([plain], _grid_source(), SETTINGS)
    assert criteria == [plain] and issues == []


def test_matching_ignores_only_spacing_case_punctuation_and_compatibility_forms():
    assert match_form("The paper’s eﬃcient in-\ndepth ­review") == match_form(
        "the paper's efficient indepth review"
    )
    assert match_form("a n academic paper") == match_form("an academic paper")
    assert match_form("purpose is clear") != match_form("purpose is unclear")


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


# --- the real rubric that exposed this (local-only, D-007) --------------------


def _real_source():
    from app.ingest import pdf as pdf_ingest

    return build_decomposition_source(
        pdf_ingest.extract_document(str(RUBRIC_PDF), SETTINGS), SETTINGS
    )


def _real_rows(source: DecompositionSource) -> list[list[str]]:
    """The grid as the extractor read it: every row whose label is followed by
    four level cells (header and total rows have fewer)."""
    by_row: dict[int, list[str]] = {}
    for cell in source.cells:
        by_row.setdefault(cell.row, []).append(cell.text)
    rows = [cells for cells in by_row.values() if len(cells) == 5]
    return [["CRITERIA", *NAMES], *rows]


@rubric_pdf_only
def test_the_real_rubric_is_one_grid_of_nine_rows_with_no_names_sent():
    source = _real_source()
    assert source.prompt_text.count("[TABLE ") == 1
    assert source.copies_merged == 2
    assert len(_real_rows(source)) - 1 == 9
    assert source.withheld, "the copies carry filled-in names"
    for value in source.withheld:
        assert value not in source.prompt_text


@rubric_pdf_only
def test_all_36_real_descriptors_are_stored_byte_for_byte_as_their_cells():
    source = _real_source()
    rows = _real_rows(source)

    def retype(text: str) -> str:
        # How the model actually retyped them: straight apostrophes and a
        # word the extractor split rejoined.
        return re.sub(r"\ba n\b", "an", text.replace("’", "'"))

    criteria, issues = verify_against_source(
        _criteria(_response(rows, retype=retype)), source, SETTINGS
    )
    assert issues == []
    stored = [level.descriptor for c in criteria for level in c.levels]
    expected = [cell for row in rows[1:] for cell in row[1:]]
    assert len(stored) == 36
    assert stored == expected


@rubric_pdf_only
async def test_the_real_rubric_shift_seen_in_production_is_not_parsed():
    source = _real_source()
    rows = _real_rows(source)
    shifted = _shifted(rows, "The introduction is clearly stated.")
    llm = ScriptedLLM([shifted])
    outcome = await attempt_decomposition(
        _SpySession(), 1, source.gate_blocks, source.prompt_text, llm, SETTINGS, source=source
    )
    assert outcome.needs_review
    assert any("not in the original document" in issue for issue in outcome.issues)
