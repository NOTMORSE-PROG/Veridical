"""V-081: source-location identity is separate from quote relevance."""

import pytest

from app.checks.semantic import _normalize, _resolve_source_excerpts, _verify_quotes
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


SENTENCE = (
    "The project methodology presents the procedures and processes used to develop "
    "the tool in a structured and efficient manner, covering design and testing."
)


@pytest.mark.parametrize("mark", ["...", "…", ". . ."])
def test_trailing_omission_proposal_stores_literal_source_text(mark):
    blocks = [block("Chapter 3 opening.", page=23), block(SENTENCE, page=24)]
    proposal = SENTENCE[:115] + mark
    assert _verify_quotes([proposal], blocks, "page") is None
    quotes, anchors = _resolve_source_excerpts([proposal], blocks, "page")
    assert anchors == ["page 24"]
    stored = quotes[0]
    assert stored in SENTENCE and not stored.endswith(".") and "…" not in stored
    # The cut word is completed from the source, never left as a fragment.
    assert stored.endswith("structured and efficient")


def test_leading_omission_proposal_stores_literal_source_text():
    blocks = [block(SENTENCE, paragraph=7)]
    # The mark cuts "procedures" mid-word; the stored excerpt completes it.
    proposal = "..." + SENTENCE[40:]
    quotes, anchors = _resolve_source_excerpts([proposal], blocks, "paragraph")
    assert anchors == ["paragraph 7"]
    assert quotes[0] in SENTENCE and quotes[0].startswith("procedures and processes")


def test_literal_proposals_are_stored_unchanged_beside_resolved_ones():
    blocks = [block("Results", page=2), block(SENTENCE, page=24)]
    quotes, anchors = _resolve_source_excerpts(["Results", SENTENCE[:90] + "..."], blocks, "page")
    assert quotes[0] == "Results" and anchors == ["page 2", "page 24"]


def test_omission_core_with_two_locations_is_not_guessed():
    blocks = [block(SENTENCE, page=5), block(SENTENCE, page=9)]
    assert _resolve_source_excerpts([SENTENCE[:100] + "..."], blocks, "page") is None


def test_short_omission_core_remains_rejected():
    blocks = [block("Measured motor output remained stable across trials.", page=3)]
    assert _resolve_source_excerpts(["Measured motor output..."], blocks, "page") is None


def test_interior_omission_remains_rejected():
    blocks = [block(SENTENCE, page=24)]
    proposal = SENTENCE[:50] + " ... " + SENTENCE[60:120] + "..."
    assert _resolve_source_excerpts([proposal], blocks, "page") is None


def test_one_unresolvable_proposal_rejects_the_whole_evidence_set():
    blocks = [block(SENTENCE, page=24)]
    assert (
        _resolve_source_excerpts([SENTENCE[:100] + "...", "Not in source"], blocks, "page") is None
    )


async def test_grading_stores_the_resolved_excerpt_not_the_proposal():
    from app.checks.semantic import SemanticBatch, grade_batch_verdicts
    from tests.test_checks_semantic import FakeCriterion
    from tests.test_semantic_partial_recovery import Scripted

    llm = Scripted(
        [
            {
                "verdicts": [
                    {
                        "index": 0,
                        "verdict": "pass",
                        "reasoning": "Scripted proposal",
                        "evidence_quotes": [SENTENCE[:115] + "..."],
                    }
                ]
            }
        ]
    )
    out = await grade_batch_verdicts(
        SemanticBatch("source", [block(SENTENCE, page=24)]),
        [FakeCriterion(1, "Methodology is described")],
        llm,
        None,
        "page",
    )
    assert out[1].verdict == "pass" and out[1].anchors == ["page 24"]
    assert out[1].quotes == [SENTENCE[: SENTENCE.index(" manner")]]
    assert len(llm.calls) == 1


async def test_resolved_excerpt_cannot_become_a_second_evidence_owner():
    from app.checks.semantic import SemanticBatch, grade_batch_verdicts
    from tests.test_checks_semantic import FakeCriterion
    from tests.test_semantic_partial_recovery import Scripted

    exact = SENTENCE[: SENTENCE.index(" manner")]
    llm = Scripted(
        [
            {
                "verdicts": [
                    {
                        "index": index,
                        "verdict": "pass",
                        "reasoning": "Scripted proposal",
                        "evidence_quotes": [quote],
                    }
                    for index, quote in enumerate((exact, SENTENCE[:115] + "..."))
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
        SemanticBatch("source", [block(SENTENCE, page=24)]),
        [FakeCriterion(1, "First requirement"), FakeCriterion(2, "Second requirement")],
        llm,
        None,
        "page",
    )
    assert out[1].verdict == "pass"
    assert out[2].verdict is None and out[2].anchors is None


LIGATURE_SOURCE = (
    "Kulik, J. A., & Fletcher, J. D. (2016). Eﬀectiveness of intelligent tutoring "
    "systems: A meta-analytic review. Review of Educational Research, 86(1), 42\u2013 78. "
    "https:// doi.org/10.3102/0034654315581420"
)


def test_rendering_differences_store_the_source_characters():
    blocks = [block(LIGATURE_SOURCE, page=8)]
    proposal = (
        "Kulik, J. A., & Fletcher, J. D. (2016). Effectiveness of intelligent tutoring "
        "systems: A meta-analytic review. Review of Educational Research, 86(1), 42\u201378. "
        "https://doi.org/10.3102/0034654315581420"
    )
    assert _verify_quotes([proposal], blocks, "page") is None
    quotes, anchors = _resolve_source_excerpts([proposal], blocks, "page")
    assert anchors == ["page 8"]
    assert quotes == [LIGATURE_SOURCE]


def test_rendering_tolerance_never_accepts_a_changed_word():
    blocks = [block(LIGATURE_SOURCE, page=8)]
    changed = LIGATURE_SOURCE.replace("intelligent", "adaptive").replace("ﬀ", "ff")
    assert _resolve_source_excerpts([changed], blocks, "page") is None


def test_rendering_tolerance_still_requires_one_location():
    blocks = [block(LIGATURE_SOURCE, page=8), block(LIGATURE_SOURCE, page=9)]
    proposal = LIGATURE_SOURCE.replace("ﬀ", "ff").replace("https:// ", "https://")
    assert _resolve_source_excerpts([proposal], blocks, "page") is None


# --- Independent backend review, 2026-10-29: the rendering fold never merges
# letters or digits, and stored evidence is always the source's own text.


@pytest.mark.parametrize(
    ("source", "proposal"),
    [
        (
            "Each learner in the pilot involved 12 50-minute sessions with the tutor over a month.",
            "Each learner in the pilot involved 1250-minute sessions with the tutor over a month.",
        ),
        (
            "Of the respondents who started the posttest, 1 2 were excluded for missing pages.",
            "Of the respondents who started the posttest, 12 were excluded for missing pages.",
        ),
        (
            "The therapist reviewed every recorded tutoring session before the final interview.",
            "The the rapist reviewed every recorded tutoring session before the final interview.",
        ),
    ],
)
def test_rendering_fold_never_merges_letters_or_digits(source, proposal):
    assert _resolve_source_excerpts([proposal], [block(source, page=4)], "page") is None


def test_case_different_literal_proposal_stores_the_source_casing():
    source = "THE SYSTEM WAS EVALUATED WITH ONE HUNDRED TWENTY GRADE 7 LEARNERS IN MANILA."
    quotes, anchors = _resolve_source_excerpts(
        [source.capitalize()], [block(source, page=2)], "page"
    )
    assert quotes == [source] and anchors == ["page 2"]


def test_omission_proposal_with_a_ligature_duplicate_elsewhere_is_ambiguous():
    plain = "The tutoring sessions were effective for most learners in the pilot group."
    typeset = plain.replace("ff", chr(0xFB00))
    blocks = [block(plain, page=3), block(typeset, page=9)]
    assert _resolve_source_excerpts([plain[:60] + "..."], blocks, "page") is None


REPEATED = (
    "The main aim of this research is to propose a framework that improves how the "
    "booking system schedules clinic appointments."
)


def test_long_passage_repeated_in_an_appendix_takes_its_first_location():
    blocks = [
        block("Chapter 1 opens here.", page=17),
        block(REPEATED, page=18),
        block("Appendix C reproduces the journal article.", page=253),
        block(REPEATED, page=254),
    ]
    assert _verify_quotes([REPEATED], blocks, "page") is None
    assert _resolve_source_excerpts([REPEATED], blocks, "page") == ([REPEATED], ["page 18"])


def test_short_phrase_at_several_locations_stays_ambiguous():
    phrase = "The system was tested"
    blocks = [block(f"{phrase} with staff.", page=3), block(f"{phrase} with clients.", page=9)]
    assert _resolve_source_excerpts([phrase], blocks, "page") is None
