"""F4: a recommendation heading is not a results heading, even when it names a conclusion."""

from dataclasses import replace

import pytest

from app.checks.agreement.cues import load_cues
from app.checks.agreement.extract import _heading_section, extract_statements
from app.config import Settings
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock

RESULT = "The kiosk reduced the average queue time by 30% in every clinic."
RECOMMENDATION = (
    "The model achieved a test accuracy of 66.71%, so future development should focus on accuracy."
)


def cues():
    return load_cues(Settings(_env_file=None).agreement_cues_file)


def blocks(rows):
    built = [
        TextBlock(page=1, text=text, max_font_size=12.0, bold_ratio=1.0 if heading else 0.0)
        for text, heading in rows
    ]
    return ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(t) for t, _h in rows),
        blocks=built,
        section_tree=SectionTree(source="none", nodes=[]),
        images=[],
    )


def results(rows, agreement_cues=None):
    out = extract_statements(
        blocks(rows), settings=Settings(_env_file=None), cues=agreement_cues or cues()
    )
    return [s.text for s in out.outcomes]


@pytest.mark.parametrize(
    "heading",
    [
        "1. Recommendation for Conclusion 1",
        "3. Recommendation for Conclusion 3",
        "Recommendations Based on the Conclusions",
        "5.3 Recommendations",
        # Whatever numbering or chapter prefix comes first.
        "(a) Recommendation for Conclusion 1",
        "1) Recommendation for Conclusion 1",
        "4.5.3. Recommendation for Conclusion 2",
        "Chapter 5: Recommendation for Conclusion 1",
        # Other forms of the word, and a suggestion.
        "Recommended Actions for Conclusion 1",
        "Suggestions Based on the Findings",
        # Listed with something that is not a results section.
        "Recommendations and Future Work Based on the Findings",
    ],
)
def test_a_recommendation_heading_is_not_a_results_heading(heading):
    assert _heading_section(heading, cues()) == "other"


@pytest.mark.parametrize(
    "heading",
    [
        "Summary, Conclusions, and Recommendations",
        "Conclusions and Recommendations",
        # Recommendations listed first, with the other sections of the chapter.
        "Recommendations and Conclusions",
        "Recommendations, Summary and Conclusions",
        "RECOMMENDATIONS AND SUMMARY OF FINDINGS",
        "A. Recommendations and Findings",
        "5.2 Conclusion",
        "Summary of Findings",
    ],
)
def test_a_chapter_title_that_also_names_recommendations_stays_a_results_heading(heading):
    assert _heading_section(heading, cues()) == "outcomes"


def test_a_recommendation_under_its_own_heading_is_not_offered_as_a_result():
    rows = [
        ("Conclusions and Recommendations", True),
        (RESULT, False),
        ("1. Recommendation for Conclusion 1", True),
        (RECOMMENDATION, False),
    ]
    assert results(rows) == [RESULT]


def test_the_rule_is_data():
    packaged = cues()
    without = replace(
        packaged,
        outcome_heading_exclusion_patterns=tuple(
            p for p in packaged.outcome_heading_exclusion_patterns if "recommend" not in p
        ),
    )
    assert len(without.outcome_heading_exclusion_patterns) == (
        len(packaged.outcome_heading_exclusion_patterns) - 1
    )
    rows = [("1. Recommendation for Conclusion 1", True), (RECOMMENDATION, False)]
    assert results(rows, packaged) == []
    assert results(rows, without) == [RECOMMENDATION]
