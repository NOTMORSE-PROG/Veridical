"""Headings repeat in the table of contents, so a quotation of one cannot be
located. The grading prompt asks for quotations that occur once, and the one
evidence-repair retry is told why each earlier quotation was rejected."""

from app.checks.semantic import (
    PROMPT_VERSION,
    SemanticBatch,
    _build_prompt,
    _repair_note,
    grade_batch_verdicts,
    prompt_file_for,
)
from app.ingest.schemas import TextBlock
from tests.test_checks_consistency import FakeCriterion

HEADING = "CHAPTER 3 PROJECT METHODOLOGY"
BODY = "This chapter explains how the clinic booking system was designed and tested."
BLOCKS = [
    TextBlock(page=2, text="TABLE OF CONTENTS", max_font_size=11, bold_ratio=0.0),
    TextBlock(page=2, text=HEADING, max_font_size=11, bold_ratio=0.0),
    TextBlock(page=2, text="CHAPTER 4 RESULTS AND DISCUSSION", max_font_size=11, bold_ratio=0.0),
    TextBlock(page=30, text=HEADING, max_font_size=14, bold_ratio=1.0),
    TextBlock(page=30, text=BODY, max_font_size=11, bold_ratio=0.0),
]
BATCH = SemanticBatch("whole document", BLOCKS)
ORGANIZATION = FakeCriterion(id=4, text="Organization")


def _response(*quotes: str) -> dict:
    return {
        "verdicts": [
            {
                "index": 0,
                "verdict": "pass",
                "reasoning": "The manuscript is organized into chapters.",
                "evidence_quotes": list(quotes),
            }
        ]
    }


class RecordingLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def complete(self, prompt_type, prompt, **kwargs):
        self.prompts.append(prompt)
        return self.responses.pop(0)


def test_current_prompt_asks_for_quotations_that_occur_once():
    text = prompt_file_for(PROMPT_VERSION).read_text(encoding="utf-8")
    assert "must occur only once in the source" in text
    assert "{repair_note}" in text
    prompt = _build_prompt(BATCH, [ORGANIZATION])
    assert "{repair_note}" not in prompt
    assert "EVIDENCE REPAIR" not in prompt


def test_repair_note_names_repeated_and_stitched_quotations():
    note = _repair_note(
        [HEADING, "TABLE OF CONTENTS CHAPTER 4 RESULTS AND DISCUSSION", BODY], BATCH, "page"
    )
    assert f'"{HEADING}" appears in 2 places.' in note
    assert "is not one continuous passage of the source" in note
    assert BODY not in note
    assert _repair_note([BODY], BATCH, "page") == ""
    assert _repair_note(None, BATCH, "page") == ""


async def test_repair_retry_is_told_why_and_can_recover():
    llm = RecordingLLM([_response(HEADING), _response(BODY)])
    graded = await grade_batch_verdicts(BATCH, [ORGANIZATION], llm, None, "page")
    first, repair = llm.prompts
    assert "EVIDENCE REPAIR" not in first
    assert f'"{HEADING}" appears in 2 places.' in repair
    result = graded[ORGANIZATION.id]
    assert result.verdict == "pass"
    assert result.anchors == ["page 30"]


def test_repair_note_names_a_quotation_already_used_by_another_criterion():
    note = _repair_note([BODY], BATCH, "page", {" ".join(BODY.lower().split())})
    assert "is already the evidence for another criterion" in note
    assert "appears in" not in note
