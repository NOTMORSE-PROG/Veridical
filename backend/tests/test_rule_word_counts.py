"""F3.2: parsed format criteria reach deterministic rules, and a named
section's word limit is counted from the manuscript's own text."""

from types import SimpleNamespace

import pytest

from app.checks.rules import RuleContext, find_matching_rule, get_rule
from app.checks.rules.numerals import normalize_numerals
from app.ingest.schemas import SectionTree, TextBlock
from app.models.enums import ResultOutcome
from app.report.rule_facts import project_rule_facts, rule_facts_presentation

WORD_COUNT = "section_word_count"


def criterion(text, evidence=None):
    return SimpleNamespace(text=text, evidence=evidence, type="structural", levels=None)


@pytest.mark.parametrize(
    ("text", "evidence", "rule_id"),
    [
        # Live rubric parses (2026-10-29): the parser always writes evidence.
        (
            "Manuscript contains an abstract of at most 250 words",
            "A reader will check the abstract section to verify that the word count does not "
            "exceed 250 words.",
            WORD_COUNT,
        ),
        (
            "Abstract must be at most 250 words.",
            "A reader would count the words in the abstract section of the manuscript.",
            WORD_COUNT,
        ),
        (
            "Reference list must have at least five major sources.",
            "A reader would count the entries in the reference list at the end of the manuscript.",
            "reference_count_min_max",
        ),
        (
            "The manuscript must include an abstract.",
            "A reader would look for the abstract section at the beginning of the manuscript.",
            "required_section_present",
        ),
        ("The abstract should not exceed two hundred fifty (250) words.", None, WORD_COUNT),
        ("Abstract: between 150 and 250 words.", None, WORD_COUNT),
        # Compound or extended requirements keep instructor-reviewed grading.
        (
            "Reference list has at least five major sources, no more than two internet-only sites",
            "A reader will review the reference list to count the total number of major sources.",
            None,
        ),
        (
            "The abstract must be at most 250 words.",
            "A reader would count only peer-reviewed words in the abstract.",
            None,
        ),
        (
            "Reference list must have at least five (5) references.",
            "A reader would check that each entry is from a peer-reviewed journal.",
            None,
        ),
        (
            "The abstract must be at most 250 words.",
            "A reader would check it is under 200 words.",
            None,
        ),
        ("The abstract must be 250 words.", None, None),
        ("Abstract must be at most five (6) words.", None, None),
    ],
)
def test_parsed_criteria_route_by_their_complete_wording(text, evidence, rule_id):
    rule = find_matching_rule(criterion(text, evidence))
    assert getattr(rule, "rule_id", None) == rule_id


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("at least five (5) references", "at least 5 references"),
        ("two hundred and fifty words", "250 words"),
        ("between one hundred fifty and two hundred fifty", "between 150 and 250"),
        ("twenty-five sources", "25 sources"),
        ("at least five (6) references", "at least five (6) references"),
        ("often tenth", "often tenth"),
    ],
)
def test_numerals_are_normalized_without_guessing(text, expected):
    assert normalize_numerals(text) == expected


def line(text, *, page=None, paragraph=None, furniture=False):
    return TextBlock(
        text=text,
        page=page,
        paragraph=paragraph,
        max_font_size=11,
        bold_ratio=0,
        is_furniture=furniture,
    )


def tree(*nodes):
    return SectionTree.model_validate(
        {
            "schema_version": 1,
            "source": "embedded_toc",
            "nodes": [
                {
                    "title": t,
                    "level": 1,
                    "page": p,
                    "paragraph": q,
                    "numbering": None,
                    "children": [],
                }
                for t, p, q in nodes
            ],
        }
    )


def context(blocks, section_tree, *, anchor_kind="page", unreliable=frozenset(), **extra):
    return RuleContext(
        manuscript_id=1,
        anchor_kind=anchor_kind,
        page_count=9,
        section_tree=section_tree,
        geometry=[],
        tables=[],
        vision_status="none",
        has_images=False,
        citations=[],
        margin_tolerance_pts=6.0,
        citation_style_min_ratio=0.7,
        table_caption_min_ratio=0.8,
        blocks=tuple(blocks),
        unreliable_pages=unreliable,
        **extra,
    )


def words(n, start=0):
    return " ".join(f"w{start + i}" for i in range(n))


def pdf_abstract(body_words):
    return [
        line("A Study of Things", page=1),
        line("ABSTRACT", page=3),
        line(words(body_words // 2), page=3),
        line(words(body_words - body_words // 2, 1000), page=3),
        line("Keywords: tutoring, chatbot, mathematics", page=3),
        line("Page iii", page=3, furniture=True),
        # Not recorded in the section tree: still ends the abstract.
        line("TABLE OF CONTENTS", page=4),
        line(words(400, 5000), page=4),
        line("CHAPTER 1", page=5),
        line("INTRODUCTION", page=5),
        line(words(300, 9000), page=5),
    ]


PDF_TREE = tree(
    ("A Study of Things", 1, None), ("ABSTRACT", 3, None), ("CHAPTER 1 INTRODUCTION", 5, None)
)
MAX_250 = criterion("Abstract must be at most 250 words.")


def run(crit, ctx):
    return get_rule(WORD_COUNT).run(crit, ctx)


def test_pdf_abstract_is_counted_between_its_heading_and_the_next_heading():
    result = run(MAX_250, context(pdf_abstract(240), PDF_TREE))
    assert result.outcome == ResultOutcome.passed
    assert result.anchor == "page 3"
    assert result.detail["actual_words"] == 240
    # The keyword line ends the prose; it and the later lines are left out.
    assert result.detail["excluded_lines"] == 3
    assert result.detail["counted_until"] == '"Keywords: tutoring, chatbot, mathematics"'


def test_clear_violation_fails_with_its_count():
    result = run(MAX_250, context(pdf_abstract(300), PDF_TREE))
    assert result.outcome == ResultOutcome.failed
    assert result.detail["actual_words"] == 300


def test_near_miss_is_escalated_for_the_instructor_not_failed():
    result = run(MAX_250, context(pdf_abstract(253), PDF_TREE))
    assert result.outcome == ResultOutcome.unverifiable
    assert "within 3 of the stated limit" in result.detail["reason"]


def test_minimum_bound_and_line_break_hyphenation():
    blocks = [
        line("ABSTRACT", page=2),
        line("This tutoring informa-", page=2),
        line("tion system works well.", page=2),
        line("CHAPTER 1", page=3),
    ]
    result = run(
        criterion("The abstract must have at least 7 words."),
        context(blocks, tree(("ABSTRACT", 2, None), ("CHAPTER 1", 3, None))),
    )
    # "information" is one word: 6 words, so the 7-word minimum is missed by 1.
    assert result.detail["actual_words"] == 6
    assert result.outcome == ResultOutcome.unverifiable


def test_hyphenated_break_inside_one_block_counts_once():
    blocks = [
        line("ABSTRACT", page=2),
        line("a bank of teacher-\nvalidated items", page=2),
        line("CHAPTER 1", page=3),
    ]
    result = run(
        criterion("The abstract must have at least 2 words."),
        context(blocks, tree(("ABSTRACT", 2, None), ("CHAPTER 1", 3, None))),
    )
    # "a bank of teacher-validated items": five words, not six.
    assert result.detail["actual_words"] == 5


def test_docx_paragraph_span_is_exact():
    blocks = [
        line("Title", paragraph=0),
        line("ABSTRACT", paragraph=1),
        line(words(120), paragraph=2),
        line(words(40, 500), paragraph=3),
        line("CHAPTER 1 INTRODUCTION", paragraph=4),
        line(words(900, 900), paragraph=5),
    ]
    result = run(
        criterion("Abstract: between 150 and 250 words."),
        context(
            blocks,
            tree(("Title", None, 0), ("ABSTRACT", None, 1), ("CHAPTER 1 INTRODUCTION", None, 4)),
            anchor_kind="paragraph",
        ),
    )
    assert result.outcome == ResultOutcome.passed
    assert result.detail["actual_words"] == 160
    assert result.anchor == "paragraph 1"


def test_unresolved_reading_order_is_not_counted():
    result = run(MAX_250, context(pdf_abstract(100), PDF_TREE, unreliable=frozenset({3})))
    assert result.outcome == ResultOutcome.unverifiable


def test_section_running_past_the_page_limit_is_not_counted():
    blocks = [line("ABSTRACT", page=2)] + [line(words(50), page=p) for p in range(2, 8)]
    result = run(MAX_250, context(blocks, tree(("ABSTRACT", 2, None))))
    assert result.outcome == ResultOutcome.unverifiable


def test_heading_that_cannot_be_located_is_not_guessed():
    blocks = [line("ABSTRACT", page=3), line("ABSTRACT", page=3), line(words(20), page=3)]
    result = run(MAX_250, context(blocks, tree(("ABSTRACT", 3, None))))
    assert result.outcome == ResultOutcome.unverifiable


def test_missing_section_is_escalated_not_failed():
    result = run(MAX_250, context(pdf_abstract(10), tree(("CHAPTER 1 INTRODUCTION", 5, None))))
    assert result.outcome == ResultOutcome.escalated


def test_saved_word_count_projects_reviewable_facts():
    result = run(MAX_250, context(pdf_abstract(240), PDF_TREE))
    facts = project_rule_facts(
        {**result.detail, "rule_id": WORD_COUNT},
        criterion_type="structural",
        kind="structural",
        outcome=result.outcome.value,
    )
    assert facts is not None and facts.actual_words == 240
    paragraphs, _ = rule_facts_presentation(facts)
    assert paragraphs[0].startswith("Recorded word count: 240 words")
    assert "at most 250" in paragraphs[1]
    # A saved outcome that disagrees with its own numbers is not shown as fact.
    assert (
        project_rule_facts(
            {**result.detail, "rule_id": WORD_COUNT},
            criterion_type="structural",
            kind="structural",
            outcome="failed",
        )
        is None
    )


# --- Independent backend review, 2026-10-29: evidence must not flip or add a bound.


@pytest.mark.parametrize(
    ("text", "evidence"),
    [
        ("at least 5 references", "A reader would check that there are not more than 5 entries"),
        (
            "The reference list must contain no more than 30 sources.",
            "A reader would count the entries in the reference list to check there are "
            "at least 30.",
        ),
        (
            "The reference list must have at most 5 sources.",
            "A reader would check that there are " + chr(0x2265) + " 5 entries.",
        ),
        (
            "The reference list must have at least 10 sources.",
            "A reader would check that all cited works are listed in the reference list.",
        ),
        (
            "The abstract must have at least 150 words.",
            "A reader would check that the abstract does not exceed 150 words.",
        ),
    ],
)
def test_evidence_that_flips_or_adds_a_requirement_abstains(text, evidence):
    assert find_matching_rule(criterion(text, evidence)) is None


def test_restating_evidence_with_the_same_bound_still_routes():
    rule = find_matching_rule(
        criterion(
            "Manuscript contains an abstract of at most 250 words",
            "A reader will check the abstract section to verify that the word count does not "
            "exceed 250 words.",
        )
    )
    assert rule is not None and rule.rule_id == WORD_COUNT


@pytest.mark.parametrize(
    "text",
    ["between two and three hundred words", "one hundred and two hundred", "five (6) entries"],
)
def test_ambiguous_numerals_stay_unparsed(text):
    assert normalize_numerals(text) == text


def test_target_section_is_the_earliest_mentioned_one():
    from app.checks.rules.sections import identify_target_section

    crit = criterion(
        "Abstract must be at most 250 words.",
        "A reader would count the words from the abstract to the table of contents.",
    )
    assert identify_target_section(crit) == "abstract"


def test_docx_section_without_a_later_heading_is_not_counted():
    blocks = [line("ABSTRACT", paragraph=1), line(words(200), paragraph=2)]
    result = run(MAX_250, context(blocks, tree(("ABSTRACT", None, 1)), anchor_kind="paragraph"))
    assert result.outcome == ResultOutcome.unverifiable


def test_docx_unrecorded_heading_inside_the_span_is_not_counted_through():
    blocks = [
        line("ABSTRACT", paragraph=1),
        line(words(200), paragraph=2),
        line("ABSTRAK", paragraph=3),
        line(words(900, 500), paragraph=4),
        line("CHAPTER 1", paragraph=5),
    ]
    result = run(
        MAX_250,
        context(
            blocks, tree(("ABSTRACT", None, 1), ("CHAPTER 1", None, 5)), anchor_kind="paragraph"
        ),
    )
    assert result.outcome == ResultOutcome.unverifiable


def test_pdf_repeated_title_or_unknown_heading_is_not_a_boundary():
    for second_heading in ("ABSTRACT", "ABSTRAK", "1. INTRODUCTION"):
        blocks = [
            line("ABSTRACT", page=3),
            line(words(200), page=3),
            line(second_heading, page=4),
            line(words(100, 300), page=4),
            line("CHAPTER 1", page=5),
        ]
        result = run(MAX_250, context(blocks, tree(("ABSTRACT", 3, None), ("CHAPTER 1", 5, None))))
        assert result.outcome == ResultOutcome.unverifiable, second_heading


@pytest.mark.parametrize(
    "label",
    [
        "Keywords: tutoring, chatbot,",
        "Index Terms" + chr(0x2014) + "tutoring, chatbot,",
        "Keywords",
        "KEYWORDS " + chr(0x2014) + " tutoring,",
    ],
)
def test_keyword_line_and_its_wrapped_list_are_not_counted(label):
    blocks = [
        line("ABSTRACT", page=3),
        line(words(248), page=3),
        line(label, page=3),
        line("algebra, bilingual instruction, mathematics education, Grade 7", page=3),
        line("CHAPTER 1", page=5),
    ]
    result = run(MAX_250, context(blocks, tree(("ABSTRACT", 3, None), ("CHAPTER 1", 5, None))))
    assert result.outcome == ResultOutcome.passed
    assert result.detail["actual_words"] == 248
