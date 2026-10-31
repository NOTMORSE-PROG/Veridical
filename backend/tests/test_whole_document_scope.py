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
    assert "collection_assessment_required" in failure["missing_capabilities"]
    # Under the decide policy the grade is kept with this caution, never "supported".
    assert failure["status"] == "unsupported" and failure["caution"]


@pytest.mark.parametrize("level", NAMES)
def test_every_grammar_level_is_whole_paper_because_of_the_criterion_itself(level):
    failure = guard(GRAMMAR, level)
    assert {"key": "criterion", "capabilities": ["collection_assessment_required"]} in failure[
        "guarded_obligations"
    ]


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
