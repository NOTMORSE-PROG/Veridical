"""V-031 tests: statistics extraction — inferential (via statcheck_python's
own regexes) and descriptive (table-column inference, built here since
statcheck doesn't cover tables at all)."""

from app.checks.forensics.extract import (
    ReportedStat,
    extract_all_stats,
    extract_descriptive_stats,
    extract_inferential_stats,
    normalize_for_statcheck,
)
from app.checks.forensics.keywords import load_keywords
from app.ingest.schemas import ExtractionResult, SectionTree, TableBlock, TextBlock


def _block(
    text: str, *, page: int | None = 1, paragraph: int | None = None, furniture=False
) -> TextBlock:
    return TextBlock(
        text=text,
        page=page,
        paragraph=paragraph,
        max_font_size=11.0,
        bold_ratio=0.0,
        is_furniture=furniture,
    )


def test_t_test_extracted_with_anchor():
    blocks = [_block("Results showed a significant effect, t(28) = 2.45, p = .021.", page=12)]
    stats = extract_inferential_stats(blocks)
    assert len(stats) == 1
    s = stats[0]
    assert s.kind == "inferential"
    assert s.test_type == "t"
    assert s.df2 == 28.0
    assert s.test_value == 2.45
    assert s.p_value == 0.021
    assert s.p_comparison == "="
    assert s.anchor == "p. 12"
    assert s.source == "text"


def test_f_test_and_chi_square_and_r_all_extracted():
    text = (
        "A second test found F(2, 45) = 3.10, p < .05. No effect was found, r(50) = .12, p = .398."
    )
    stats = extract_inferential_stats([_block(text)])
    types = {s.test_type for s in stats}
    assert types == {"F", "r"}
    f_stat = next(s for s in stats if s.test_type == "F")
    assert f_stat.df1 == 2.0
    assert f_stat.df2 == 45.0
    assert f_stat.p_comparison == "<"


def test_unicode_chi_square_normalized_and_extracted():
    """Real gap found live: statcheck's regex expects a literal 'X' for
    chi-square (same convention its own file_to_txt.py uses for HTML chi
    entities) — the raw Unicode χ/² PyMuPDF actually extracts needs its
    own normalization first."""
    stats = extract_inferential_stats([_block("χ²(2, N = 100) = 5.67, p = .059.")])
    assert len(stats) == 1
    assert stats[0].test_type == "Chi2"
    assert stats[0].df1 == 2.0
    assert stats[0].test_value == 5.67


def test_thousands_separator_comma_normalized():
    """Real gap found live: 'N = 1,000' breaks statcheck's df parsing
    unless the thousands comma is stripped first — narrowly, so the
    ordinary APA comma between clauses is never touched."""
    stats = extract_inferential_stats([_block("χ²(2, N = 1,000) = 5.67, p = .059.")])
    assert len(stats) == 1
    assert stats[0].df1 == 2.0


def test_ordinary_apa_comma_between_clauses_survives_normalization():
    normalized = normalize_for_statcheck("t(28) = 2.45, p = .021")
    assert normalized == "t(28) = 2.45, p = .021"


def test_no_stats_in_plain_prose():
    stats = extract_inferential_stats([_block("This chapter discusses the findings in detail.")])
    assert stats == []


def test_furniture_blocks_excluded():
    stats = extract_inferential_stats([_block("t(28) = 2.45, p = .021.", furniture=True)])
    assert stats == []


def test_paragraph_anchor_used_when_no_page():
    stats = extract_inferential_stats([_block("t(28) = 2.45, p = .021.", page=None, paragraph=7)])
    assert stats[0].anchor == "¶7"


def test_extractor_never_crashes_on_arbitrary_text():
    garbage = [
        "",
        "   ",
        "(((((((((",
        "​​​",
        "a" * 5000,
        "p < .05 with no test statistic at all",
        "\x00\x01 binary-ish garbage",
        "t(",
        "= = = = =",
    ]
    for text in garbage:
        stats = extract_inferential_stats([_block(text)])
        assert isinstance(stats, list)  # never raises, whatever happens


def test_descriptive_stats_matched_by_column_header():
    table = TableBlock(
        page=20,
        rows=[
            ["Group", "n", "M", "SD"],
            ["Control", "30", "3.45", "0.62"],
            ["Treatment", "32", "4.01", "0.58"],
        ],
        source="native",
    )
    stats = extract_descriptive_stats([table])
    by_group = {(s.group_label, s.stat_name): s.value for s in stats}
    assert by_group[("Control", "n")] == 30.0
    assert by_group[("Control", "mean")] == 3.45
    assert by_group[("Control", "sd")] == 0.62
    assert by_group[("Treatment", "mean")] == 4.01
    assert all(s.source == "table" and s.anchor == "p. 20" for s in stats)


def test_low_confidence_vision_table_excluded():
    """V-007 contract: a vision read the model wasn't sure about must
    never feed a forensics check (ticket AC)."""
    table = TableBlock(
        page=21,
        rows=[["Group", "n"], ["A", "10"]],
        source="vision",
        low_confidence=True,
    )
    assert extract_descriptive_stats([table]) == []


def test_confident_vision_table_marked_as_image_table_source():
    table = TableBlock(
        page=22,
        rows=[["Group", "%"], ["A", "45.2"]],
        source="vision",
        low_confidence=False,
    )
    stats = extract_descriptive_stats([table])
    assert len(stats) == 1
    assert stats[0].source == "image_table"
    assert stats[0].stat_name == "percentage"
    assert stats[0].value == 45.2


def test_table_with_no_matching_columns_yields_nothing():
    table = TableBlock(page=1, rows=[["Foo", "Bar"], ["x", "y"]], source="native")
    assert extract_descriptive_stats([table]) == []


def test_non_numeric_cell_under_matched_column_skipped_not_crashed():
    table = TableBlock(
        page=1, rows=[["Group", "n"], ["A", "not a number"], ["B", "12"]], source="native"
    )
    stats = extract_descriptive_stats([table])
    assert len(stats) == 1
    assert stats[0].value == 12.0


def test_extraction_never_prints_to_stdout(capsys):
    """statcheck's own extract_stats() prints a snippet of the matched
    text on certain internal parse failures (confirmed by reading its
    source) — manuscript content must never reach real logs this way."""
    texts = [
        "t(28) = 2.45, p = .021.",
        "χ²(2, N = 1,000) = 5.67, p = .059.",
        "garbage that partially matches t( = , p",
        "F(,) = xx, p = ...",
    ]
    extract_inferential_stats([_block(t) for t in texts])
    captured = capsys.readouterr()
    assert captured.out == ""


def test_forensics_keywords_load_from_default_file():
    keywords = load_keywords()
    assert "n" in keywords.n_headers
    assert "sd" in keywords.sd_headers
    assert "%" in keywords.percentage_headers


def test_extract_all_stats_combines_text_and_tables():
    extraction = ExtractionResult(
        page_count=2,
        anchor_kind="page",
        image_only=False,
        text_chars=100,
        section_tree=SectionTree(source="none", nodes=[]),
        blocks=[_block("t(28) = 2.45, p = .021.", page=1)],
        images=[],
        tables=[TableBlock(page=2, rows=[["Group", "n"], ["A", "10"]], source="native")],
    )
    stats = extract_all_stats(extraction)
    kinds = {s.kind for s in stats}
    assert kinds == {"inferential", "descriptive"}
    assert all(isinstance(s, ReportedStat) for s in stats)
