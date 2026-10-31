"""Candidate results come from the thesis's own results sections, and when
those cannot be told apart the run says so instead of quietly using all."""

from app.checks.agreement.cues import load_cues
from app.checks.agreement.extract import Statement
from app.checks.agreement.sections import NOT_IDENTIFIED_NOTE, gate_results
from app.checks.section_roles import SectionMap, load_section_roles
from app.config import get_settings
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock

CUES = load_cues()
SETTINGS = get_settings()


def _block(text, page):
    return TextBlock(text=text, page=page, paragraph=None, max_font_size=11.0, bold_ratio=0.0)


REVIEW = "Results showed that the earlier kiosk improved queues by a third (Cruz, 2021)."
OWN = "Results showed that the kiosk shortened the average queue to four minutes."
BLOCKS = [
    _block("Chapter 2 Review of Related Literature", 5),
    _block(REVIEW, 6),
    _block("Chapter 4 Results and Discussion", 20),
    _block(OWN, 21),
]


def _statement(text, page):
    return Statement("outcome", text, f"p. {page}", page, None, "results showed")


def _extraction(blocks, nodes=()):
    return ExtractionResult(
        page_count=30,
        anchor_kind="page",
        image_only=False,
        text_chars=0,
        section_tree=SectionTree(source="heuristics" if nodes else "none", nodes=list(nodes)),
        blocks=blocks,
        images=[],
    )


def _gate(extraction):
    sections = SectionMap(
        extraction,
        load_section_roles(),
        heading_max_words=SETTINGS.agreement_heading_max_words,
        locate_chars=SETTINGS.agreement_context_locate_chars,
    )
    return gate_results([_statement(REVIEW, 6), _statement(OWN, 21)], sections)


def test_a_literature_review_summary_is_set_aside_by_the_section_tree():
    nodes = [
        SectionNode(title="CHAPTER 2: REVIEW OF RELATED LITERATURE", level=1, page=5, children=[]),
        SectionNode(title="CHAPTER 4: RESULTS AND DISCUSSION", level=1, page=20, children=[]),
    ]
    gate = _gate(_extraction(BLOCKS, nodes))
    assert [s.text for s in gate.kept] == [OWN]
    assert gate.set_aside[0]["anchor"] == "p. 6"
    assert gate.detail()["chapter_source"] == "section_tree"


def test_chapter_headings_in_the_text_are_used_when_the_tree_has_none():
    gate = _gate(_extraction(BLOCKS))
    assert [s.text for s in gate.kept] == [OWN]
    assert gate.detail()["chapter_source"] == "inline_headings"


def test_when_no_chapter_can_be_classified_everything_is_kept_and_said_so():
    blocks = [_block(REVIEW, 6), _block(OWN, 21)]
    gate = _gate(_extraction(blocks))
    assert len(gate.kept) == 2
    detail = gate.detail()
    assert detail["results_section_identified"] is False
    assert detail["note"] == NOT_IDENTIFIED_NOTE


def test_a_tree_of_front_and_back_matter_alone_is_not_used_as_chapters():
    """A tree listing only "DEFINITION OF TERMS" and "APPENDIX" would make every
    page between them look like one non-results chapter."""
    nodes = [
        SectionNode(title="DEFINITION OF TERMS", level=1, page=2, children=[]),
        SectionNode(title="APPENDIX", level=1, page=28, children=[]),
    ]
    gate = _gate(_extraction(BLOCKS, nodes))
    assert [s.text for s in gate.kept] == [OWN]
    assert gate.detail()["chapter_source"] == "inline_headings"


def test_a_bare_chapter_heading_takes_the_title_on_the_next_line():
    blocks = [
        _block("Chapter 2", 5),
        _block("Review of Related Literature", 5),
        _block(REVIEW, 6),
        _block("Chapter 4", 20),
        _block("Results and Discussion", 20),
        _block(OWN, 21),
    ]
    gate = _gate(_extraction(blocks))
    assert [s.text for s in gate.kept] == [OWN]
    assert gate.set_aside[0]["chapter"] == "Chapter 2 Review of Related Literature"
