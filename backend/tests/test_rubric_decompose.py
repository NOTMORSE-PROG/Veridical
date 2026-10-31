"""V-010: rubric decomposition — prompt substitution, schema validation,
weight normalization. All on the LLMClient interface, zero network.
"""

from typing import Any

import pytest

from app.config import get_settings
from app.ingest.schemas import TextBlock
from app.llm.base import LLMClient
from app.llm.fake import FakeLLMClient
from app.rubric.decompose import (
    RubricParseError,
    decompose_rubric,
    raw_text_for_decomposition,
)


class SpyLLM(LLMClient):
    """Scripted response + prompt capture, mirrors test_ingest_vision.py's
    SpyLLM pattern."""

    def __init__(self, response: dict[str, Any]):
        self.prompts: list[str] = []
        self.versions: list[str] = []
        self._response = response

    async def complete(
        self, prompt_type: str, prompt: str, *, prompt_version: str = "unversioned", **context: Any
    ) -> dict[str, Any]:
        self.prompts.append(prompt)
        self.versions.append(prompt_version)
        return self._response


def _block(text: str, *, furniture: bool = False) -> TextBlock:
    return TextBlock(page=1, text=text, max_font_size=11.0, bold_ratio=0.0, is_furniture=furniture)


def test_raw_text_strips_furniture_and_blank_lines():
    blocks = [
        _block("Page 3", furniture=True),
        _block(""),
        _block("Criterion: contains an abstract."),
        _block("   "),
        _block("Running Header", furniture=True),
        _block("Criterion: cites at least five sources."),
    ]
    text = raw_text_for_decomposition(blocks)
    assert text == "Criterion: contains an abstract.\nCriterion: cites at least five sources."


async def test_decompose_rubric_normalizes_weights_to_sum_100():
    criteria = await decompose_rubric("irrelevant for the fake client", FakeLLMClient())
    total = sum(c.weight for c in criteria)
    assert total == pytest.approx(100.0)
    assert {c.type for c in criteria} <= {"structural", "semantic"}


async def test_decompose_rubric_raises_on_missing_criteria_key():
    with pytest.raises(RubricParseError):
        await decompose_rubric("x", SpyLLM({"not_criteria": []}))


async def test_decompose_rubric_raises_on_empty_criteria_list():
    with pytest.raises(RubricParseError):
        await decompose_rubric("x", SpyLLM({"criteria": []}))


async def test_row_fidelity_prompt_has_separate_cache_identity_and_preserves_source():
    client = SpyLLM({"criteria": [{"text": "Source requirement", "type": "semantic", "weight": 1}]})
    source = "Independent row A: quality.\nIndependent row B: count."
    await decompose_rubric(source, client)
    assert client.versions == ["v6"]
    assert client.prompts[0].endswith(source + "\n")
    # The withheld-value marker is configuration, substituted into the prompt.
    assert "{withheld_marker}" not in client.prompts[0]
    assert get_settings().rubric_withheld_value_marker in client.prompts[0]


async def test_decompose_rubric_raises_on_bad_type_enum():
    with pytest.raises(RubricParseError):
        await decompose_rubric(
            "x",
            SpyLLM({"criteria": [{"text": "Has a title page", "type": "objective", "weight": 5}]}),
        )


async def test_decompose_rubric_raises_on_blank_criterion_text():
    with pytest.raises(RubricParseError):
        await decompose_rubric(
            "x", SpyLLM({"criteria": [{"text": "   ", "type": "structural", "weight": 5}]})
        )


# --- V-069: levels validation through the real decompose_rubric path -------

_TIP_SCALE_RESPONSE = {
    "criteria": [
        {
            "text": "Introduction states and previews structure",
            "type": "semantic",
            "evidence_needed": "Read the introduction",
            "weight": 10,
            "levels": [
                {"level": 1, "name": "Beginner", "descriptor": "no structure", "points": 1},
                {"level": 2, "name": "Acceptable", "descriptor": "states the topic", "points": 2},
                {
                    "level": 3,
                    "name": "Proficient",
                    "descriptor": "states and previews",
                    "points": 3,
                },
                {"level": 4, "name": "Exemplary", "descriptor": "engaging", "points": 4},
            ],
        }
    ]
}


async def test_decompose_rubric_round_trips_a_valid_levelled_scale():
    criteria = await decompose_rubric("x", SpyLLM(_TIP_SCALE_RESPONSE))
    assert criteria[0].levels is not None
    assert [lvl.name for lvl in criteria[0].levels] == [
        "Beginner",
        "Acceptable",
        "Proficient",
        "Exemplary",
    ]


async def test_decompose_rubric_rejects_an_all_zero_points_scale():
    """backend review finding: an all-zero-points scale makes
    max_points 0, which crashed the resolve endpoint's division with a
    raw ZeroDivisionError -- now caught at the source, before it can ever
    reach a stored Criterion."""
    zero_scale = {
        "criteria": [
            {
                "text": "x",
                "type": "semantic",
                "weight": 10,
                "levels": [
                    {"level": 1, "name": "None", "descriptor": "x", "points": 0},
                    {"level": 2, "name": "Some", "descriptor": "y", "points": 0},
                ],
            }
        ]
    }
    with pytest.raises(RubricParseError):
        await decompose_rubric("x", SpyLLM(zero_scale))


async def test_decompose_rubric_rejects_a_single_rung_scale():
    single_rung = {
        "criteria": [
            {
                "text": "x",
                "type": "semantic",
                "weight": 10,
                "levels": [{"level": 1, "name": "Only", "descriptor": "x", "points": 1}],
            }
        ]
    }
    with pytest.raises(RubricParseError):
        await decompose_rubric("x", SpyLLM(single_rung))


async def test_decompose_handles_prose_style_input_with_no_numbering():
    """The format-agnosticism claim, at the code level: nothing here
    branches on bullets/numbering/section names — a plain paragraph goes
    through the exact same path as a numbered rubric."""
    prose = (
        "Panelists should judge whether the presenter speaks clearly and "
        "keeps within the fifteen minute limit, and whether the slide deck "
        "has no more than twelve slides."
    )
    scripted = {
        "criteria": [
            {
                "text": "Presenter speaks clearly",
                "type": "semantic",
                "evidence_needed": "Oral delivery, clarity",
                "weight": 1,
            },
            {
                "text": "Presentation stays within the fifteen minute limit",
                "type": "structural",
                "evidence_needed": "Timed duration",
                "weight": 1,
            },
            {
                "text": "Slide deck has no more than twelve slides",
                "type": "structural",
                "evidence_needed": "Slide count",
                "weight": 1,
            },
        ]
    }
    criteria = await decompose_rubric(prose, SpyLLM(scripted))
    assert len(criteria) == 3
    # Rounding each weight to 3 decimals (an uneven 3-way split) can leave
    # a cent-level remainder — real drift, not a bug; tolerance reflects it.
    assert sum(c.weight for c in criteria) == pytest.approx(100.0, abs=0.01)


async def test_raw_text_with_literal_braces_does_not_break_prompt_substitution():
    """decompose_rubric uses str.replace, not str.format; the prompt file
    itself is full of literal `{`/`}` in its JSON example, and rubric text
    can legitimately contain braces too (e.g. citation markers)."""
    spy = SpyLLM({"criteria": [{"text": "x", "type": "structural", "weight": 1}]})
    raw_text = "Cite sources like {Smith, 2020} inline, in braces: {a: b}."
    await decompose_rubric(raw_text, spy)
    assert raw_text in spy.prompts[0]
    assert "{rubric_text}" not in spy.prompts[0]


async def test_bug_333_decompose_rubric_normalizes_em_and_en_dashes_in_drafted_fields():
    """BUG-333: neither the fake fixture nor model-returned criterion fields
    may carry typographic em/en dashes to the instructor review screen."""
    fixture_criteria = await decompose_rubric("x", FakeLLMClient())
    for item in fixture_criteria:
        assert "\u2014" not in item.text and "\u2013" not in item.text
        if item.evidence_needed:
            assert "\u2014" not in item.evidence_needed and "\u2013" not in item.evidence_needed

    scripted = {
        "criteria": [
            {
                "text": "Chapter 1 \u2014 states the problem clearly",
                "type": "semantic",
                "evidence_needed": "Chapter 1, sections 1.1\u20131.2 \u2014 clarity and depth",
                "weight": 10,
                "levels": [
                    {
                        "level": 1,
                        "name": "Beginner",
                        "descriptor": "Weak statement \u2014 lacks scope",
                        "points": 1,
                    },
                    {
                        "level": 2,
                        "name": "Exemplary",
                        "descriptor": "Clear statement \u2013 well scoped",
                        "points": 2,
                    },
                ],
            }
        ]
    }
    parsed = await decompose_rubric("x", SpyLLM(scripted))
    assert parsed[0].text == "Chapter 1: states the problem clearly"
    assert parsed[0].evidence_needed == "Chapter 1, sections 1.1-1.2: clarity and depth"
    assert parsed[0].levels is not None
    assert parsed[0].levels[0].descriptor == "Weak statement: lacks scope"
    assert parsed[0].levels[1].descriptor == "Clear statement: well scoped"
