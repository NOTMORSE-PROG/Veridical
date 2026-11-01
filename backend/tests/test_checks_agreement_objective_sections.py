"""Objectives come from the thesis's own objectives, not from another paper's
aim in the literature review or a process step in the methodology, and a goal
stated under "Background of the Study" in the introduction is kept."""

from app.checks.agreement.extract import Statement
from app.checks.agreement.sections import (
    OBJECTIVES_ALL_SET_ASIDE_NOTE,
    OBJECTIVES_NOT_IDENTIFIED_NOTE,
    gate_objectives,
)
from app.checks.section_roles import SectionMap, load_section_roles
from app.config import get_settings
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock

SETTINGS = get_settings()

OWN = "To develop a booking module that lets patients reserve a clinic slot."
BACKGROUND_GOAL = "The primary goal is to determine the shortest clinic queue for each patient."
OTHER_PAPER = "They aim to build an open-source web application for virtual try-on."
PROCESS_STEP = "After receiving the updated status, the application will show it to the patient."
CHAPTER_AIM = "To collect a longitudinal dataset of queue times across three clinics."


def _block(text, page):
    return TextBlock(text=text, page=page, paragraph=None, max_font_size=11.0, bold_ratio=0.0)


def _extraction(blocks, nodes=()):
    return ExtractionResult(
        page_count=40,
        anchor_kind="page",
        image_only=False,
        text_chars=0,
        section_tree=SectionTree(source="heuristics" if nodes else "none", nodes=list(nodes)),
        blocks=blocks,
        images=[],
    )


def _intent(text, page):
    return Statement("intent", text, f"p. {page}", page, None, "aims to")


def _gate(blocks, intents, nodes=()):
    sections = SectionMap(
        _extraction(blocks, nodes),
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


def test_chapter_one_keeps_its_objectives_whatever_its_title_says():
    # "The Problem and Its Background" reads as literature by its words, and
    # "Research Design and Objectives" as methodology; both are Chapter 1.
    for title in ("Chapter 1 The Problem and Its Background", "Chapter 1 Research Design"):
        blocks = [
            _block(title, 2),
            _block(OWN, 4),
            _block("Chapter 2 Review of Related Literature", 8),
            _block(OTHER_PAPER, 9),
        ]
        gate = _gate(blocks, [_intent(OWN, 4), _intent(OTHER_PAPER, 9)])
        assert [s.text for s in gate.kept] == [OWN], title
        assert [item["chapter_role"] for item in gate.set_aside] == ["literature"]


def test_steps_in_results_conclusion_and_appendix_chapters_are_set_aside():
    blocks = [
        _block("Chapter 1 Introduction", 2),
        _block(OWN, 4),
        _block("Chapter 4 Results and Discussion", 20),
        _block(PROCESS_STEP, 21),
        _block("Chapter 5 Summary, Conclusions and Recommendations", 30),
        _block("The system aims to shorten clinic queues for every patient.", 31),
        _block("Appendices", 36),
        _block("The project aims to explore clinic booking in three cities.", 37),
    ]
    intents = [
        _intent(OWN, 4),
        _intent(PROCESS_STEP, 21),
        _intent("The system aims to shorten clinic queues for every patient.", 31),
        _intent("The project aims to explore clinic booking in three cities.", 37),
    ]
    nodes = [
        SectionNode(title="CHAPTER 1 INTRODUCTION", level=1, page=2, children=[]),
        SectionNode(title="CHAPTER 4 RESULTS AND DISCUSSION", level=1, page=20, children=[]),
        SectionNode(title="CHAPTER 5 SUMMARY AND CONCLUSIONS", level=1, page=30, children=[]),
        SectionNode(title="APPENDICES", level=1, page=36, children=[]),
    ]
    gate = _gate(blocks, intents, nodes)
    assert [s.text for s in gate.kept] == [OWN]
    assert [item["chapter_role"] for item in gate.set_aside] == [
        "results",
        "conclusion",
        "appendix",
    ]


def test_a_thesis_whose_every_aim_would_be_set_aside_keeps_them_and_says_so():
    # Better to compare a possible non-objective than to report that a thesis
    # stating aims has none.
    blocks = [_block("Chapter 2 Background and Related Work", 8), _block(OTHER_PAPER, 9)]
    gate = _gate(blocks, [_intent(OTHER_PAPER, 9)])
    assert [s.text for s in gate.kept] == [OTHER_PAPER] and gate.set_aside == []
    detail = gate.detail()
    assert detail["note"] == OBJECTIVES_ALL_SET_ASIDE_NOTE
    assert detail["objectives_section_identified"] is True
    # The audit still shows which aim the chapter map judged not to be one.
    assert [item["chapter_role"] for item in detail["kept_despite_role"]] == ["literature"]


def test_chapter_one_split_into_two_tree_nodes_keeps_its_number():
    # The extractor can give "CHAPTER 1" and its title as two nodes on one page.
    nodes = [
        SectionNode(title="CHAPTER 1", level=1, page=2, children=[]),
        SectionNode(title="THE PROBLEM AND ITS BACKGROUND", level=1, page=2, children=[]),
        SectionNode(title="CHAPTER 2", level=1, page=8, children=[]),
        SectionNode(title="REVIEW OF RELATED LITERATURE", level=1, page=8, children=[]),
    ]
    blocks = [_block("This study aims to cut clinic queues in half.", 1), _block(OWN, 4)]
    blocks.append(_block(OTHER_PAPER, 9))
    intents = [
        _intent("This study aims to cut clinic queues in half.", 1),
        _intent(OWN, 4),
        _intent(OTHER_PAPER, 9),
    ]
    gate = _gate(blocks, intents, nodes)
    assert OWN in [s.text for s in gate.kept]
    assert [item["chapter_role"] for item in gate.set_aside] == ["literature"]


def test_a_background_heading_outside_chapter_one_does_not_set_an_aim_aside():
    # Only the chapter decides: an aim under "Background" in a study chapter
    # whose title names no role ("3 AirSpecs") is that chapter's objective.
    blocks = [
        _block("Chapter 1 Introduction", 2),
        _block(OWN, 4),
        _block("Chapter 3 AirSpecs Field Study", 20),
        _block("Background", 21),
        _block(CHAPTER_AIM, 21),
    ]
    gate = _gate(blocks, [_intent(OWN, 4), _intent(CHAPTER_AIM, 21)])
    assert [s.text for s in gate.kept] == [OWN, CHAPTER_AIM]


def test_the_rules_are_data_and_a_format_can_change_them(tmp_path):
    import json

    from app.checks.section_roles import _DEFAULT_FILE

    roles = load_section_roles()
    # The packaged data holds the rules the check used to carry in code.
    assert roles.objectives_kept_chapters == frozenset({1})
    assert "literature" in roles.objectives_excluded
    assert "introduction" not in roles.objectives_excluded
    assert "introduction" in roles.results_excluded
    # A format whose first chapter is a literature review can say so.
    custom = json.loads(_DEFAULT_FILE.read_text(encoding="utf-8"))
    custom["agreement"]["objectives_always_kept_chapters"] = []
    path = tmp_path / "roles.json"
    path.write_text(json.dumps(custom), encoding="utf-8")
    blocks = [
        _block("Chapter 1 Background of the Study", 2),
        _block(OTHER_PAPER, 3),
        _block("Chapter 2 Introduction", 8),
        _block(OWN, 9),
    ]
    sections = SectionMap(
        _extraction(blocks),
        load_section_roles(path),
        heading_max_words=SETTINGS.agreement_heading_max_words,
        locate_chars=SETTINGS.agreement_context_locate_chars,
    )
    gate = gate_objectives([_intent(OTHER_PAPER, 3), _intent(OWN, 9)], sections)
    assert [s.text for s in gate.kept] == [OWN]


def test_a_roles_file_without_the_agreement_rules_keeps_the_packaged_ones(tmp_path):
    import json

    from app.checks.section_roles import _DEFAULT_FILE

    custom = json.loads(_DEFAULT_FILE.read_text(encoding="utf-8"))
    del custom["agreement"]
    path = tmp_path / "roles.json"
    path.write_text(json.dumps(custom), encoding="utf-8")
    assert load_section_roles(path).objectives_excluded == load_section_roles().objectives_excluded
