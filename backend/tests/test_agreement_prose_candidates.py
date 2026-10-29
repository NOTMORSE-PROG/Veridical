"""F4: ordinary prose is a candidate, while unknown recall never proves absence."""

from dataclasses import replace

import pytest

from app.checks.agreement.cues import load_cues
from app.checks.agreement.extract import extract_statements
from app.config import Settings
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock


def source(texts):
    return ExtractionResult(
        page_count=2,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(map(len, texts)),
        blocks=[
            TextBlock(page=1 if i < 2 else 2, text=t, max_font_size=12, bold_ratio=0)
            for i, t in enumerate(texts)
        ],
        section_tree=SectionTree(source="none", nodes=[]),
        images=[],
    )


@pytest.mark.parametrize(
    "noun,verb,subject",
    [("objective", "was", "Irrigation"), ("goal", "is", "Inventory"), ("aim", "was", "Astronomy")],
)
def test_nominal_intent_and_unnumbered_results_independent_of_domain(noun, verb, subject):
    intent = f"Its {noun} {verb} to reduce processing time while preserving output quality."
    result = f"The {subject.lower()} controller reduced measured processing time in the trials."
    d = source(
        [
            "Overview",
            intent,
            "Results",
            result,
            "References",
            "Example, A. (2020). Synthetic control.",
        ]
    )
    out = extract_statements(d, settings=Settings(_env_file=None))
    assert any(s.text == intent and s.page == 1 for s in out.intents)
    assert any(s.text == result and s.page == 2 for s in out.outcomes)
    assert not any("Synthetic control" in s.text for s in out.outcomes)


def test_unnumbered_results_keep_future_scope_and_source_guards():
    d = source(
        [
            "Results",
            "Future researchers could later implement biometric login.",
            "The new sensor is outside the scope of this study.",
        ]
    )
    out = extract_statements(d, settings=Settings(_env_file=None))
    assert out.outcomes == []


def test_outcome_heading_and_constructions_are_configurable_data():
    cues = replace(
        load_cues(),
        outcome_heading_synonyms=frozenset({"observations"}),
        intent_construction_patterns=(),
        outcome_prose_enabled=True,
    )
    out = extract_statements(
        source(["Observations", "The detector measured a stable current."]),
        cues=cues,
        settings=Settings(_env_file=None),
    )
    assert len(out.outcomes) == 1


def test_bibliography_fragments_and_prior_results_are_not_current_outcome_prose():
    d = source(
        [
            "Results",
            "Smith, A. (2020). Improved Sensor Results. Research Press.",
            "Prior Results",
            "Earlier sensors reduced processing time.",
        ]
    )
    assert extract_statements(d, settings=Settings(_env_file=None)).outcomes == []
