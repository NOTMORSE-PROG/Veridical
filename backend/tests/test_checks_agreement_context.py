"""What the agreement judge is shown around a pair: the text near the
finding, the headings, and the manuscript's own scope statements."""

from app.checks.agreement.context import DocumentContext
from app.checks.agreement.cues import load_cues
from app.checks.agreement.extract import Statement
from app.checks.agreement.pair import Candidate, _build_prompt
from app.config import get_settings
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock

CUES = load_cues()
SETTINGS = get_settings()


def _block(text: str, page: int) -> TextBlock:
    return TextBlock(text=text, page=page, paragraph=None, max_font_size=11.0, bold_ratio=0.0)


def _extraction(blocks: list[TextBlock]) -> ExtractionResult:
    return ExtractionResult(
        page_count=max(b.page for b in blocks),
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        section_tree=SectionTree(source="none", nodes=[]),
        blocks=blocks,
        images=[],
    )


THESIS = _extraction(
    [
        _block("Objectives of the Study", 3),
        _block("1. To build an offline attendance module for all campuses.", 3),
        _block("Scope and Delimitation", 4),
        _block(
            "This study covers only the pilot deployment in one campus. "
            "Later phases of the rollout are outside the evaluation reported here.",
            4,
        ),
        _block("Results and Discussion", 20),
        _block(
            "The attendance records were collected for four weeks. "
            "Results showed that the module was not yet able to sync offline in the pilot. "
            "Teachers reported fewer missing records. The survey closed in May.",
            20,
        ),
        _block("Conclusion", 30),
        _block("The weather was fine during the trial.", 30),
    ]
)
INTENT = Statement(
    "intent",
    "To build an offline attendance module for all campuses.",
    "p. 3",
    3,
    None,
    "intent_list",
)
OUTCOME = Statement(
    "outcome",
    "Results showed that the module was not yet able to sync offline in the pilot.",
    "p. 20",
    20,
    None,
    "results showed",
)


def test_scope_statements_come_from_the_scope_section_and_cue_patterns():
    document = DocumentContext.build(THESIS, CUES, SETTINGS)
    texts = [s.text for s in document.scope_statements]
    assert "This study covers only the pilot deployment in one campus." in texts
    assert all("weather" not in t for t in texts)


def test_a_pair_carries_the_sentences_around_the_finding_and_its_headings():
    context = DocumentContext.build(THESIS, CUES, SETTINGS).for_pair(INTENT, OUTCOME)
    assert context.before == "The attendance records were collected for four weeks."
    assert context.after.startswith("Teachers reported fewer missing records.")
    assert context.finding_heading == "Results and Discussion"
    assert context.scope[0].text == "This study covers only the pilot deployment in one campus."


def test_the_window_and_the_scope_list_are_bounded_by_settings():
    settings = SETTINGS.model_copy(
        update={
            "agreement_context_sentences_before": 0,
            "agreement_context_sentences_after": 1,
            "agreement_scope_statements_per_pair": 1,
        }
    )
    context = DocumentContext.build(THESIS, CUES, settings).for_pair(INTENT, OUTCOME)
    assert context.before == ""
    assert context.after == "Teachers reported fewer missing records."
    assert len(context.scope) == 1


def test_the_prompt_shows_the_context_for_each_pair():
    context = DocumentContext.build(THESIS, CUES, SETTINGS).for_pair(INTENT, OUTCOME)
    prompt = _build_prompt([Candidate(INTENT, OUTCOME, 0.8, context)])
    assert "TEXT AROUND THE FINDING" in prompt
    assert "SCOPE/LIMITATION STATEMENT (p. 4)" in prompt
    assert 'under "Results and Discussion"' in prompt
    assert "objective_reported_not_done" in prompt
