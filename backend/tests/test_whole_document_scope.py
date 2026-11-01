"""BUG-607: a level that describes the whole paper is never "supported" by one quote.

The wording below is T.I.P.'s Rubric for Oral Presentation as VERIDICAL stores
it (criterion text, evidence request and level descriptors). On real theses the
support check passed single sentences for these levels, including an
acknowledgement thanking the grammarian offered for "occasional errors".
"""

from types import SimpleNamespace

import pytest

from app.checks.support import _capability_failure, apply_support, locatable_subclaim
from app.models.enums import ResultOutcome

NAMES = ("BEGINNER", "ACCEPTABLE", "PROFICIENT", "EXEMPLARY")


def criterion(text, evidence, *descriptors):
    return SimpleNamespace(
        text=text,
        evidence=evidence,
        levels=[
            {"name": NAMES[i], "level": i + 1, "points": float(i + 1), "descriptor": d}
            for i, d in enumerate(descriptors)
        ],
    )


INTRODUCTION = criterion(
    "Introduction: Introduction quality, main topic presentation, and paper structure preview.",
    "A reader examines the introductory section to evaluate how clearly it states the main topic "
    "and previews the paper's structure.",
    "There is no clear introduction of main topic and the structure of the paper is missing.",
    "The introduction states the main topic but does not adequately preview the structure of the "
    "paper.",
    "The introduction states the main topic and previews the structure of the paper.",
    "The introduction is engaging, states the main topic and previews the structure of the paper.",
)
PURPOSE = criterion(
    "Purpose: Clarity and conciseness of the paper's central purpose or argument.",
    "A reader checks the statement of purpose for clarity, conciseness, and framing as a single "
    "sentence.",
    "The purpose or argument is generally unclear.",
    "The central purpose or argument is not consistently clear throughout the paper.",
    "The paper's purpose is clearly stated in a single sentence.",
    "The paper's purpose is clearly and concisely stated in a single sentence, which is engaging, "
    "and thought provoking.",
)
CONTENT = criterion(
    "Content: Support for the central purpose, depth of analysis, and insightfulness of "
    "information.",
    "A reader evaluates the body of the paper to judge the quality of analysis, use of relevant "
    "information, and support for the core argument.",
    "Analysis is vague or not evident. Reader is confused or may be misinformed.",
    "Information supports a central purpose or argument at times. Analysis is basic or general. "
    "Reader gains few insights.",
    "Information provides reasonable support for a central purpose or argument and displays "
    "evidence of a basic analysis of a significant topic. Reader gains some insights.",
    "Balanced presentation of relevant and legitimate information that clearly supports a central "
    "purpose or argument and shows a thoughtful, in-depth analysis of a significant topic. Reader "
    "gains important insights.",
)
ORGANIZATION = criterion(
    "Organization: Logical arrangement and flow of ideas supporting the argument.",
    "A reader reviews the logical progression and transitions between ideas to assess the overall "
    "flow and line of reasoning.",
    "The writing is not logically organized. Frequently, ideas fail to make sense together. The "
    "reader cannot identify a line of reasoning and loses interest.",
    "In general, the writing is arranged logically, although occasionally ideas fail to make sense "
    "together. The reader is fairly clear about what writer intends.",
    "The ideas are arranged logically to support the central purpose or argument. They are usually "
    "clearly linked to each other. For the most part, the reader can follow the line of reasoning.",
    "The ideas are arranged logically to support the purpose or argument. They flow smoothly from "
    "one to another and are clearly linked to each other. The reader can follow the line of "
    "reasoning.",
)
GRAMMAR = criterion(
    "Grammar, Spelling, Writing Mechanics (punctuation, italics, capitalization, etc.): Absence of "
    "mechanical and grammatical errors.",
    "A reader examines the manuscript for grammar, spelling, punctuation, italics, and "
    "capitalization errors.",
    "There are so many errors that meaning is obscured. The reader is confused and stops reading.",
    "The writing has many errors, and the reader is distracted by them.",
    "There are occasional errors, but they don't represent a major distraction or obscure meaning.",
    "The writing is free or almost free of errors.",
)
TONE = criterion(
    "Tone: Professionalism and appropriateness of the writing tone for an academic research paper.",
    "A reader evaluates the diction and style of the text to determine if it maintains a "
    "professional and academic tone.",
    "The tone is unprofessional. It is not appropriate for an academic research paper.",
    "The tone is not consistently professional or appropriate for an academic research paper.",
    "The tone is generally professional. For the most part, it is appropriate for an academic "
    "research paper.",
    "The tone is consistently professional and appropriate for an academic research paper.",
)
BATCH = SimpleNamespace(label="whole document", scope_patterns_file=None)


def guard(item, level):
    return _capability_failure(BATCH, item, {"verdict": level})


@pytest.mark.parametrize(
    ("item", "level"),
    [
        # The levels the support check passed from one sentence on real theses.
        (CONTENT, "PROFICIENT"),
        (GRAMMAR, "PROFICIENT"),
        (GRAMMAR, "EXEMPLARY"),
        (TONE, "EXEMPLARY"),
        # Their neighbours on the same scales.
        (CONTENT, "EXEMPLARY"),
        (TONE, "PROFICIENT"),
        (ORGANIZATION, "PROFICIENT"),
        (ORGANIZATION, "EXEMPLARY"),
    ],
)
def test_a_whole_paper_level_needs_more_than_a_quote(item, level):
    failure = guard(item, level)
    assert failure is not None
    assert failure["scope_guard"] == "independent_assessment_required"
    assert "whole_paper_assessment_required" in failure["missing_capabilities"]
    # Under the decide policy the grade is kept with this caution, never "supported".
    assert failure["status"] == "unsupported" and failure["caution"]


@pytest.mark.parametrize("level", NAMES)
def test_every_grammar_level_is_whole_paper_because_of_the_criterion_itself(level):
    failure = guard(GRAMMAR, level)
    assert {
        "key": "criterion",
        "capabilities": ["whole_paper_assessment_required"],
        "decides": True,
    } in failure["guarded_obligations"]


@pytest.mark.parametrize("level", NAMES)
def test_every_grammar_level_is_whole_paper_by_its_own_descriptor_too(level):
    # A parse whose criterion line names no error rate still guards Grammar.
    descriptors = [item["descriptor"] for item in GRAMMAR.levels]
    assert guard(criterion("Grammar", None, *descriptors), level) is not None


@pytest.mark.parametrize(
    ("item", "level"),
    [
        (INTRODUCTION, "ACCEPTABLE"),
        (INTRODUCTION, "PROFICIENT"),
        (INTRODUCTION, "EXEMPLARY"),
        (PURPOSE, "PROFICIENT"),
        (PURPOSE, "EXEMPLARY"),
    ],
)
def test_locatable_levels_still_reach_the_support_check(item, level):
    # A reader examining or checking a section is not a whole-paper claim.
    assert guard(item, level) is None


@pytest.mark.parametrize(("item", "level"), [(INTRODUCTION, "PROFICIENT"), (PURPOSE, "PROFICIENT")])
def test_the_strict_locatable_gate_is_unchanged(item, level):
    assert locatable_subclaim(item, {"verdict": level})


# The same format as parsed for an earlier version: its Purpose evidence request
# says "clearly and consistently stated", which is about the purpose statement,
# not about the whole paper.
PURPOSE_EARLIER_PARSE = criterion(
    "Purpose",
    "A reader evaluates whether the paper's purpose or central argument is clearly and "
    "consistently stated.",
    "The purpose or argument is generally unclear. Central purpose or argument is not clearly "
    "identified.",
    "The paper's purpose is clearly stated in a single sentence. The central purpose or argument "
    "is not consistently clear throughout the paper.",
    "The paper's purpose is clearly stated.",
    "The paper's purpose is clearly and concisely stated in a single sentence, which is engaging, "
    "and thought provoking.",
)


def test_a_purpose_statement_described_as_consistently_stated_stays_locatable():
    assert guard(PURPOSE_EARLIER_PARSE, "EXEMPLARY") is None
    assert guard(PURPOSE_EARLIER_PARSE, "PROFICIENT") is None
    assert locatable_subclaim(PURPOSE_EARLIER_PARSE, {"verdict": "EXEMPLARY"})


@pytest.mark.parametrize(
    "descriptor",
    [
        "The paper has numerous grammatical errors.",
        "There are frequent spelling and grammar errors.",
        "The writing has many grammatical and spelling errors.",
        "There are occasional grammatical or mechanical errors.",
        "The writing is error-free.",
        "The paper is free from grammatical, spelling, and mechanical errors.",
        "Errors are rare.",
        "The tone is inconsistently professional.",
        "Readers gain important insights.",
    ],
)
def test_other_formats_wording_for_a_whole_paper_property_is_recognised(descriptor):
    item = criterion("Writing", None, "Poor.", "Fair.", descriptor, "Best.")
    assert guard(item, "PROFICIENT") is not None


@pytest.mark.parametrize(
    "text",
    [
        "The system logs each error code it receives.",
        "A reader examines the methodology chapter.",
        "The study reports an error rate of 3%.",
        # How the format parser phrases an evidence request, about one place.
        "A reader can locate the purpose statement in chapter 1.",
        "The objectives are consistent with the problem statement.",
    ],
)
def test_wording_about_one_place_is_not_mistaken_for_the_whole_paper(text):
    item = criterion(text, None, "Done.", "Done well.", "Done very well.", "Done best.")
    assert guard(item, "PROFICIENT") is None


@pytest.mark.parametrize("decide", [True, False])
def test_a_whole_paper_weakness_claimed_from_one_quote_goes_to_the_instructor(decide):
    # The lowest level is a claim of weakness: it needs evidence of the
    # weakness, and one quote cannot show an error rate, so it is never kept.
    failure = guard(GRAMMAR, "BEGINNER")
    detail = {"verdict": "BEGINNER", "level": {"name": "BEGINNER"}, "evidence": []}
    outcome, updated = apply_support(
        ResultOutcome.passed, detail, failure, decide=decide, adverse=True
    )
    assert outcome == ResultOutcome.escalated
    assert updated["support_withheld"] and "verdict" not in updated


def test_a_whole_paper_grade_is_kept_with_a_caution_under_decide():
    failure = guard(TONE, "EXEMPLARY")
    detail = {"verdict": "EXEMPLARY", "level": {"name": "EXEMPLARY"}, "evidence": []}
    outcome, updated = apply_support(ResultOutcome.passed, detail, failure, decide=True)
    assert outcome == ResultOutcome.passed
    assert updated["support_caution"] and updated["support_check"]["status"] == "unsupported"


# BUG-609: three stored parses of the same T.I.P. format word the criterion
# line and the evidence request differently. The level descriptors are the
# format's own words; whether a level is guarded must follow them alone.
PARSE_WORDING = {
    "Purpose": [
        (
            "Statement and clarity of purpose or argument",
            "A reader evaluates how clearly and consistently the central purpose or argument is "
            "stated throughout the paper.",
        ),
        (
            "Purpose",
            "A reader evaluates whether the paper's purpose or central argument is clearly and "
            "consistently stated.",
        ),
        (
            "Purpose: Clarity and conciseness of the paper's central purpose or argument.",
            "A reader checks the statement of purpose for clarity, conciseness, and framing as a "
            "single sentence.",
        ),
    ],
    "Tone": [
        (
            "Professional tone appropriate for an academic research paper",
            "A reader assesses the overall voice and tone used throughout the document.",
        ),
        (
            "Tone",
            "A reader evaluates whether the tone of the writing is professional and appropriate "
            "for an academic research paper.",
        ),
        (TONE.text, TONE.evidence),
    ],
}


def _decision(failure):
    return None if failure is None else failure["missing_capabilities"]


@pytest.mark.parametrize("name", list(PARSE_WORDING))
@pytest.mark.parametrize("level", NAMES)
def test_the_guard_does_not_depend_on_how_the_parser_worded_the_criterion(name, level):
    scale = {"Purpose": PURPOSE, "Tone": TONE}[name]
    descriptors = [item["descriptor"] for item in scale.levels]
    decisions = {
        str(_decision(guard(criterion(text, evidence, *descriptors), level)))
        for text, evidence in PARSE_WORDING[name]
    }
    assert len(decisions) == 1, decisions


def test_a_single_sentence_purpose_stays_locatable_under_the_throughout_wording():
    text, evidence = PARSE_WORDING["Purpose"][0]
    item = criterion(text, evidence, *(level["descriptor"] for level in PURPOSE.levels))
    assert guard(item, "PROFICIENT") is None
    assert locatable_subclaim(item, {"verdict": "PROFICIENT"})


# Rubric 8's Use of References, whose levels were traced word for word to the
# T.I.P. format, with each stored parse's own criterion line and request.
USE_OF_REFERENCES_LEVELS = (
    "References are seldom cited to support statements.",
    "Although attributions are occasionally given, many statements seem unsubstantiated. The "
    "reader is confused about the source of information and ideas.",
    "Professionally legitimate sources that support claims are generally present and attribution "
    "is, for the most part, clear and fairly represented.",
    "Compelling evidence from professionally legitimate sources is given to support claims. "
    "Attribution is clear and fairly represented.",
)
USE_OF_REFERENCES_WORDING = [
    (
        "Use and attribution of references",
        "A reader checks whether claims are properly supported by citations and whether source "
        "attribution is clear and fair.",
    ),
    (
        "Use of References",
        "A reader checks whether claims are properly substantiated with sources and attributions.",
    ),
    (
        "Use of References: Presence of supporting references and clarity of attributions for "
        "claims.",
        "A reader inspects in-text attributions and reference usage to verify that claims are "
        "supported by legitimate sources.",
    ),
]
WHOLE = "whole_paper_assessment_required"
EXTERNAL = "external_verification_required"
COLLECTION = "collection_assessment_required"


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        # "Seldom cited" is a rate across the paper; it was guarded only by
        # one parse's evidence request until frequency words were data.
        ("BEGINNER", [WHOLE]),
        ("ACCEPTABLE", [WHOLE]),
        # "For the most part" also reads as a share of the collection.
        ("PROFICIENT", [COLLECTION, EXTERNAL, WHOLE]),
        ("EXEMPLARY", [EXTERNAL]),
    ],
)
@pytest.mark.parametrize(("text", "evidence"), USE_OF_REFERENCES_WORDING)
def test_use_of_references_is_guarded_the_same_way_in_every_parse(text, evidence, level, expected):
    failure = guard(criterion(text, evidence, *USE_OF_REFERENCES_LEVELS), level)
    assert failure is not None and sorted(failure["missing_capabilities"]) == expected


@pytest.mark.parametrize(
    ("item", "level", "expected"),
    [
        (PURPOSE, "PROFICIENT", None),
        (PURPOSE, "EXEMPLARY", None),
        (PURPOSE, "ACCEPTABLE", [WHOLE]),
        # Accepted (PM, 2026-11-01): "The tone is unprofessional" names no
        # rate or extent, as rubric 8 already treated it; Tone's other levels
        # keep the whole-paper caution through their own wording.
        (TONE, "BEGINNER", None),
        (TONE, "PROFICIENT", [COLLECTION, WHOLE]),
        (TONE, "EXEMPLARY", [WHOLE]),
    ],
)
def test_expected_guard_per_level_whichever_parse_worded_the_criterion(item, level, expected):
    name = "Purpose" if item is PURPOSE else "Tone"
    descriptors = [entry["descriptor"] for entry in item.levels]
    for text, evidence in PARSE_WORDING[name]:
        assert _decision(guard(criterion(text, evidence, *descriptors), level)) == expected
