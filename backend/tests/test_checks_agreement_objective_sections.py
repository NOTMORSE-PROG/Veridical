"""Objectives come from the thesis's own objectives, not from another paper's
aim in the literature review or a process step in the methodology, and a goal
stated under "Background of the Study" in the introduction is kept."""

from app.checks.agreement.extract import Statement
from app.checks.agreement.sections import OBJECTIVES_NOT_IDENTIFIED_NOTE, gate_objectives
from app.checks.section_roles import SectionMap, load_section_roles
from app.config import get_settings
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock

SETTINGS = get_settings()

OWN = "To develop a booking module that lets patients reserve a clinic slot."
BACKGROUND_GOAL = "The primary goal is to determine the shortest clinic queue for each patient."
OTHER_PAPER = "They aim to build an open-source web application for virtual try-on."
PROCESS_STEP = "After receiving the updated status, the application will show it to the patient."
CHAPTER_AIM = "To collect a longitudinal dataset of queue times across three clinics."


def _block(text, page):
    return TextBlock(text=text, page=page, paragraph=None, max_font_size=11.0, bold_ratio=0.0)


def _extraction(blocks):
    return ExtractionResult(
        page_count=40,
        anchor_kind="page",
        image_only=False,
        text_chars=0,
        section_tree=SectionTree(source="none", nodes=[]),
        blocks=blocks,
        images=[],
    )


def _intent(text, page):
    return Statement("intent", text, f"p. {page}", page, None, "aims to")


def _gate(blocks, intents):
    sections = SectionMap(
        _extraction(blocks),
        load_section_roles(),
        heading_max_words=SETTINGS.agreement_heading_max_words,
        locate_chars=SETTINGS.agreement_context_locate_chars,
    )
    return gate_objectives(intents, sections)


THESIS = [
    _block("Chapter 1 Introduction", 2),
    _block("Background of the Study", 3),
    _block(BACKGROUND_GOAL, 3),
    _block(OWN, 4),
    _block("Chapter 2 Review of Related Literature", 8),
    _block(OTHER_PAPER, 9),
    _block("Chapter 3 Methodology", 15),
    _block(PROCESS_STEP, 16),
    _block("Chapter 4 Clinic Queue Study", 22),
    _block(CHAPTER_AIM, 23),
]
INTENTS = [
    _intent(BACKGROUND_GOAL, 3),
    _intent(OWN, 4),
    _intent(OTHER_PAPER, 9),
    _intent(PROCESS_STEP, 16),
    _intent(CHAPTER_AIM, 23),
]


def test_aims_outside_the_thesis_own_objectives_are_set_aside():
    gate = _gate(THESIS, INTENTS)
    assert [s.text for s in gate.kept] == [BACKGROUND_GOAL, OWN, CHAPTER_AIM]
    assert {item["chapter_role"] for item in gate.set_aside} == {"literature", "methodology"}
    detail = gate.detail()
    assert detail["n_objectives_set_aside"] == 2
    assert detail["objectives_section_identified"] is True


def test_a_goal_under_a_background_heading_in_the_introduction_is_kept():
    # "Background" names the literature role as a heading; only the chapter decides.
    gate = _gate(THESIS, [_intent(BACKGROUND_GOAL, 3)])
    assert [s.text for s in gate.kept] == [BACKGROUND_GOAL]


def test_when_no_chapter_can_be_classified_every_objective_is_kept_and_said_so():
    blocks = [_block(OWN, 4), _block(OTHER_PAPER, 9)]
    gate = _gate(blocks, [_intent(OWN, 4), _intent(OTHER_PAPER, 9)])
    assert len(gate.kept) == 2
    assert gate.detail()["note"] == OBJECTIVES_NOT_IDENTIFIED_NOTE
