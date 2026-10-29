"""V-081: source-location identity is separate from quote relevance."""

import pytest

from app.checks.semantic import _normalize, _verify_quotes
from app.ingest.schemas import TextBlock


def block(text, *, page=None, paragraph=None):
    return TextBlock(text=text, page=page, paragraph=paragraph, max_font_size=11, bold_ratio=0)


def test_exact_source_case_precedes_earlier_prose_occurrence():
    blocks = [
        block("The following results are intentionally constructed.", page=1),
        block("Summary of Findings\nResults\nA supported result.", page=2),
    ]
    before = [item.model_dump() for item in blocks]
    assert _verify_quotes(["Results"], blocks, "page") == ["page 2"]
    assert [item.model_dump() for item in blocks] == before
    assert _normalize("Results") == "results"


@pytest.mark.parametrize("quote", ["Repeated passage", "REPEATED PASSAGE"])
def test_repeated_quote_on_different_pages_has_no_unique_location(quote):
    blocks = [block("Repeated passage", page=1), block("Repeated passage", page=2)]
    assert _verify_quotes([quote], blocks, "page") is None


def test_repeated_quote_on_one_page_only_supports_that_page():
    blocks = [block("Repeated passage", page=2), block("Repeated passage", page=2)]
    assert _verify_quotes(["Repeated passage"], blocks, "page") == ["page 2"]


def test_repeated_quote_in_different_docx_paragraphs_is_ambiguous():
    blocks = [block("Repeated passage", paragraph=0), block("Repeated passage", paragraph=2)]
    assert _verify_quotes(["Repeated passage"], blocks, "paragraph") is None


@pytest.mark.parametrize("unknown_first", [True, False])
def test_unknown_location_cannot_be_hidden_by_known_occurrence(unknown_first):
    blocks = [block("Recorded passage"), block("Recorded passage", page=3)]
    if not unknown_first:
        blocks.reverse()
    assert _verify_quotes(["Recorded passage"], blocks, "page") is None


def test_case_insensitive_compatibility_requires_one_supported_location():
    assert _verify_quotes(["UPPER CASE"], [block("Upper case", page=3)], "page") == ["page 3"]


def test_multiblock_whitespace_and_start_location_remain_supported():
    blocks = [block("The document\nretains", page=2), block("its original source.", page=3)]
    assert _verify_quotes(["The document retains its original source."], blocks, "page") == [
        "page 2"
    ]


def test_substring_elsewhere_does_not_silently_win_over_later_heading():
    blocks = [block("ResultsArchive", page=1), block("Results", page=2)]
    assert _verify_quotes(["Results"], blocks, "page") is None


@pytest.mark.parametrize("quote", ["", "   ", "Not in source"])
def test_missing_or_empty_quote_remains_rejected(quote):
    assert _verify_quotes([quote], [block("Recorded passage", page=3)], "page") is None


def test_trailing_ellipsis_is_not_a_literal_source_quotation():
    blocks = [block("This is an exact quotation from the manuscript.", page=4)]
    for quote in (
        "This is an exact quotation from the manuscript...",
        "This is an exact quotation from the manuscript\u2026",
    ):
        assert _verify_quotes([quote], blocks, "page") is None


def test_dot_leader_does_not_prove_which_page_the_model_quoted():
    blocks = [
        block("CHAPTER 1 INTRODUCTION.............................................6", page=2),
        block("CHAPTER 1\nINTRODUCTION", page=6),
    ]
    assert _verify_quotes(["CHAPTER 1 INTRODUCTION"], blocks, "page") is None


@pytest.mark.parametrize("first_page", [1, None])
def test_prose_ellipsis_cannot_hide_a_real_second_quote_location(first_page):
    quote = "Measured motor output remained stable"
    blocks = [
        block(quote + " ... followed by an omitted qualification.", page=first_page),
        block(quote, page=2),
    ]
    assert _verify_quotes([quote], blocks, "page") is None


async def test_generated_omission_mark_cannot_create_a_second_evidence_owner():
    from app.checks.semantic import SemanticBatch, grade_batch_verdicts
    from tests.test_checks_semantic import FakeCriterion
    from tests.test_semantic_partial_recovery import Scripted

    quote = "Measured motor output remained stable"
    llm = Scripted(
        [
            {
                "verdicts": [
                    {
                        "index": index,
                        "verdict": "pass",
                        "reasoning": "Scripted proposal",
                        "evidence_quotes": [quote + suffix],
                    }
                    for index, suffix in enumerate(("", "..."))
                ]
            },
            {
                "verdicts": [
                    {
                        "index": 0,
                        "verdict": "uncertain",
                        "reasoning": "No independent literal evidence",
                        "evidence_quotes": [],
                    }
                ]
            },
        ]
    )
    out = await grade_batch_verdicts(
        SemanticBatch("source", [block(quote, page=3)]),
        [FakeCriterion(1, "First requirement"), FakeCriterion(2, "Second requirement")],
        llm,
        None,
        "page",
    )
    assert out[1].verdict == "pass"
    assert out[2].verdict is None and out[2].anchors is None
    assert len(llm.calls) == 2
