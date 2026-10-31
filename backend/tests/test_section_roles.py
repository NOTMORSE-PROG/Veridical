"""Where in a thesis a statement or quote sits: the shared section map."""

import pytest

from app.checks.section_roles import SectionMap, chapter_number, load_section_roles
from app.config import get_settings
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock

ROLES = load_section_roles()
SETTINGS = get_settings()


@pytest.mark.parametrize(
    "title, role",
    [
        ("CHAPTER 2: REVIEW OF RELATED LITERATURE", "literature"),
        ("Chapter 3 Methodology Introduction", "methodology"),
        ("Chapter 1 Introduction Background and Rationale", "introduction"),
        ("Chapter 5 Summary, Conclusion, and Recommendations", "conclusion"),
        ("CHAPTER 5: ANALYSIS AND DISCUSSION OF THE", "results"),
        ("Table of Contents", "table_of_contents"),
        ("APPENDIX B AUTHOR'S VITAE", "biography"),
        ("Chapter 1 The Problem and Its Background", "literature"),
        ("3 AirSpecs - Sensing Human-Environment Interaction", "other"),
    ],
)
def test_titles_map_to_roles(title, role):
    assert ROLES.role_of(title) == role


@pytest.mark.parametrize(
    "title, number",
    [
        ("CHAPTER 3: RESEARCH METHODOLOGY", 3),
        ("Chapter IV Results", 4),
        ("3 AirSpecs - Sensing", 3),
        ("Abstract", None),
        ("I. Background", None),
    ],
)
def test_chapter_numbers(title, number):
    assert chapter_number(title) == number


def _block(text, page):
    return TextBlock(text=text, page=page, paragraph=None, max_font_size=11.0, bold_ratio=0.0)


def _map(blocks, nodes=()):
    extraction = ExtractionResult(
        page_count=40,
        anchor_kind="page",
        image_only=False,
        text_chars=0,
        section_tree=SectionTree(source="heuristics" if nodes else "none", nodes=list(nodes)),
        blocks=blocks,
        images=[],
    )
    return SectionMap(
        extraction,
        ROLES,
        heading_max_words=SETTINGS.agreement_heading_max_words,
        locate_chars=SETTINGS.agreement_context_locate_chars,
    )


ORGANIZED = "This chapter is organized into several thematic sections."


def test_a_chapter_organization_line_in_chapter_2_is_literature_and_chapter_2():
    sections = _map(
        [
            _block("1 Introduction", 23),
            _block("2 Background and Related Work", 29),
            _block(ORGANIZED, 29),
        ],
        [
            SectionNode(title="1 Introduction", level=1, page=23, children=[]),
            SectionNode(title="2 Background and Related Work", level=1, page=29, children=[]),
        ],
    )
    where = sections.locate(ORGANIZED, "p. 29", 29)
    assert where.chapter_role == "literature" and where.chapter_number == 2


def test_a_table_of_contents_page_is_table_of_contents():
    toc_line = "Chapter 4 The System 44 Chapter 5 Summary, Conclusion, and Recommendations 83."
    sections = _map(
        [_block("TABLE OF CONTENTS", 4), _block(toc_line, 4), _block("Chapter 1", 9)],
        [
            SectionNode(title="TABLE OF CONTENTS", level=1, page=4, children=[]),
            SectionNode(title="Chapter 4 44 The System", level=1, page=5, children=[]),
            SectionNode(title="Chapter 1 Introduction", level=1, page=9, children=[]),
            SectionNode(title="Chapter 4 The System Overview", level=1, page=52, children=[]),
        ],
    )
    where = sections.locate(toc_line, "p. 4", 4)
    assert where.chapter_role == "table_of_contents"
    # The contents entry for chapter 4 on p. 5 is not taken for the chapter.
    assert sections.page_roles([6])[6] == "table_of_contents"
