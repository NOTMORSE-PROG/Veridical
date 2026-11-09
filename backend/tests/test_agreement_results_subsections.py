"""F4: a subheading inside Results keeps Results reading on; a new section ends it."""

import json
from dataclasses import replace

import pytest

from app.checks.agreement.cues import load_cues
from app.checks.agreement.extract import extract_statements
from app.config import Settings
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock

RESULT = "The kiosk reduced the average queue time by 30% in every clinic."
FULL = 460.0


def blocks(rows):
    """(text, type size, bold) rows on one page; body lines run full width."""
    built = [
        TextBlock(
            page=1,
            text=text,
            max_font_size=size,
            bold_ratio=bold,
            bbox=(72.0, 100.0 + 14 * i, 72.0 + (FULL if text.endswith(".") else 200.0), 112.0),
        )
        for i, (text, size, bold) in enumerate(rows)
    ]
    return ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(t) for t, _s, _b in rows),
        blocks=built,
        section_tree=SectionTree(source="none", nodes=[]),
        images=[],
    )


def results(rows, cues=None):
    out = extract_statements(blocks(rows), settings=Settings(_env_file=None), cues=cues)
    return [s.text for s in out.outcomes]


@pytest.mark.parametrize(
    "opener,subheading",
    [
        # A numbered child of the heading that opened Results.
        (("4.5 Key Findings", 14.0, 1.0), ("4.5.1 Performance in the Field", 13.0, 1.0)),
        # A bold, clearly smaller unnumbered subheading.
        (("Results and Evaluation", 17.2, 1.0), ("Model Performance Comparison", 14.3, 1.0)),
        # A label line.
        (("Results", 12.0, 1.0), ("Queue Time:", 12.0, 1.0)),
        # A note in brackets.
        (("Results", 12.0, 1.0), ("(Appendix 5)", 12.0, 0.0)),
        # A generic word that only ends Results at the start of a heading.
        (("4.5 Key Findings", 14.0, 1.0), ("4.5.2 Background Noise Reduction", 13.0, 1.0)),
        # Exactly the margin smaller is enough.
        (("Results and Evaluation", 15.5, 1.0), ("Model Performance Comparison", 14.0, 1.0)),
        # Bold for half the line is enough.
        (("Results and Evaluation", 17.2, 1.0), ("Model Performance Comparison", 14.3, 0.5)),
    ],
)
def test_a_subheading_inside_results_keeps_results_reading_on(opener, subheading):
    assert results([opener, subheading, (RESULT, 12.0, 0.0)]) == [RESULT]


@pytest.mark.parametrize(
    "opener,heading",
    [
        # A numbered heading that is not a child of the Results heading.
        (("4.5 Key Findings", 14.0, 1.0), ("4.6 Project Overview", 14.0, 1.0)),
        # Under an unnumbered chapter title, a numbered heading is a section of
        # its own, however small its type.
        (("Conclusions", 24.7, 1.0), ("6.1 Summary of Research", 17.2, 1.0)),
        # Same type size, unnumbered: not recognised as a subheading.
        (("Results and Evaluation", 14.0, 1.0), ("Project Overview", 14.0, 1.0)),
        # Smaller but not bold: a running header, not a subheading.
        (("FINDINGS", 19.9, 1.0), ("Chapter 8: Overview", 12.0, 0.0)),
        # A section that ends the results ends them.
        (("4.5 Key Findings", 14.0, 1.0), ("4.5.2 Limitations", 13.0, 1.0)),
        (("4.5 Key Findings", 14.0, 1.0), ("4.5.2 Background of the Study", 13.0, 1.0)),
        (("Results and Evaluation", 17.2, 1.0), ("Recommendations", 14.0, 1.0)),
        (("Results", 12.0, 1.0), ("Future Work:", 12.0, 1.0)),
        (("Results and Evaluation", 17.2, 1.0), ("Lessons Learned", 14.0, 1.0)),
        (("Results and Evaluation", 17.2, 1.0), ("Related Systems", 14.0, 1.0)),
        (("4.5 Key Findings", 14.0, 1.0), ("4.5.3 Comparison with Existing Systems", 13.0, 1.0)),
        (("Results and Evaluation", 17.2, 1.0), ("Implications for Practice", 14.0, 1.0)),
        # A note, source or legend label is not a result's label.
        (("Results", 12.0, 1.0), ("Source:", 12.0, 1.0)),
        # An objectives heading ends Results, whatever its shape.
        (("4.3 Results", 14.0, 1.0), ("Objective 1:", 12.0, 1.0)),
        (("4.3 Results", 14.0, 1.0), ("4.3.1 Objective 1", 13.0, 1.0)),
        # "4.10" is not a child of "4.1".
        (("4.1 Key Findings", 14.0, 1.0), ("4.10 Project Plan", 13.0, 1.0)),
        # Not quite the margin smaller, or bold for under half the line.
        (("Results and Evaluation", 15.4, 1.0), ("Model Performance Comparison", 14.0, 1.0)),
        (("Results and Evaluation", 17.2, 1.0), ("Model Performance Comparison", 14.3, 0.4)),
        # Results opened together with another kind of section is not extended.
        (("4 Results and Discussion", 14.0, 1.0), ("4.1 Project Plan", 13.0, 1.0)),
        (("Discussion, Future Work, and Conclusion", 24.7, 1.0), ("Synthesis", 17.2, 1.0)),
    ],
)
def test_a_new_section_still_ends_results(opener, heading):
    assert results([opener, heading, (RESULT, 12.0, 0.0)]) == []


def test_a_result_before_the_subheading_is_still_read():
    rows = [
        ("4.5 Key Findings", 14.0, 1.0),
        ("The portal reduced booking errors by 12% in the pilot.", 12.0, 0.0),
        ("4.5.1 Performance in the Field", 13.0, 1.0),
        (RESULT, 12.0, 0.0),
    ]
    assert results(rows) == ["The portal reduced booking errors by 12% in the pilot.", RESULT]


def test_the_subsection_rules_are_data():
    settings = Settings(_env_file=None)
    cues = load_cues(settings.agreement_cues_file)
    rows = [("4.5 Key Findings", 14.0, 1.0), ("4.5.1 Performance", 13.0, 1.0), (RESULT, 12.0, 0.0)]
    assert results(rows, cues) == [RESULT]
    off = replace(
        cues, results_subsection_policy={**cues.results_subsection_policy, "enabled": False}
    )
    assert results(rows, off) == []
    packaged = json.loads(
        (settings.agreement_cues_file or _default_cues_path()).read_text(encoding="utf-8")
    )
    assert packaged["results_subsection_policy"]["enabled"] is True


def _default_cues_path():
    from pathlib import Path

    import app.checks.agreement.cues as module

    return Path(module.__file__).parent / "data" / "cues.json"
