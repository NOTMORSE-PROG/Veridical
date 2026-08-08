"""V-034 tests: intent/outcome statement extraction (F4.1/F4.2).

Synthetic fixtures for the unit-level rule behavior (same convention as
V-027's `test_checks_citations_extract.py`), plus a hand-labeled synthetic
"second manuscript" recall fixture standing in for the ticket's "2 real
manuscripts" QA step — only ONE real manuscript with real capstone-style
prose exists locally (the owner's own proposal, no second real thesis
exists yet, same honest gap V0/V-031 already recorded). The owner's real
proposal PDF is hand-verified separately below (`demo_pdf_only`, local-only
per D-007, CI skips it) against ticket AC #1: its 4 specific objectives.
"""

from pathlib import Path

import pytest

from app.checks.agreement.cues import load_cues
from app.checks.agreement.extract import extract_statements
from app.config import get_settings
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock

CUES = load_cues()
SETTINGS = get_settings()


def _block(text: str, *, page: int | None = 1, paragraph: int | None = None) -> TextBlock:
    return TextBlock(text=text, page=page, paragraph=paragraph, max_font_size=11.0, bold_ratio=0.0)


def _extraction(blocks: list[TextBlock]) -> ExtractionResult:
    return ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        section_tree=SectionTree(source="none", nodes=[]),
        blocks=blocks,
        images=[],
    )


# --- rule 1: objective/outcome list detection ---------------------------------


def test_numbered_objectives_list_under_heading():
    blocks = [
        _block("1.3.2 Specific Objectives"),
        _block("1. To design a login module for returning users"),
        _block("using their existing school credentials."),
        _block("2. To build a dashboard summarizing weekly attendance."),
        _block("1.4 SIGNIFICANCE OF THE STUDY"),
        _block("3. This numbered line after the heading exit must NOT be captured."),
    ]
    out = extract_statements(_extraction(blocks), cues=CUES, settings=SETTINGS)
    assert len(out.intents) == 2
    assert "login module" in out.intents[0].text
    assert "credentials" in out.intents[0].text  # continuation line merged in
    assert "dashboard summarizing" in out.intents[1].text
    assert all("numbered line after" not in s.text for s in out.intents)


def test_findings_list_under_heading_is_outcomes():
    blocks = [
        _block("Summary of Findings"),
        _block("1. The login module correctly authenticated 98% of test users."),
        _block("2. The dashboard updated within two seconds of a new attendance entry."),
    ]
    out = extract_statements(_extraction(blocks), cues=CUES, settings=SETTINGS)
    assert len(out.outcomes) == 2
    assert not out.intents


def test_bulleted_list_marker_variants():
    blocks = [
        _block("Objectives"),
        _block("- To reduce manual grading time by half."),
        _block("(2) To provide instructors a printable summary report."),
    ]
    out = extract_statements(_extraction(blocks), cues=CUES, settings=SETTINGS)
    assert len(out.intents) == 2


# --- rule 2: modal-phrase cues, position-independent ---------------------------


def test_modal_intent_cue_anywhere_in_document():
    blocks = [
        _block("BACKGROUND OF THE STUDY"),
        _block(
            "Attendance tracking is currently manual. This study aims to automate the "
            "process for the whole department."
        ),
    ]
    out = extract_statements(_extraction(blocks), cues=CUES, settings=SETTINGS)
    assert len(out.intents) == 1
    assert "automate the process" in out.intents[0].text


def test_modal_outcome_cue_anywhere_in_document():
    blocks = [
        _block("DISCUSSION"),
        _block("Results showed a 40% reduction in manual grading time across all sections."),
    ]
    out = extract_statements(_extraction(blocks), cues=CUES, settings=SETTINGS)
    assert len(out.outcomes) == 1
    assert "40% reduction" in out.outcomes[0].text


def test_cue_deep_in_sentence_is_not_captured():
    """A real false positive found live against the owner's own proposal:
    a sentence TALKING ABOUT the concept of intent, cue word buried past
    the lead-word window, must not be captured as a real intent."""
    blocks = [
        _block(
            "Internal Agreement is the check for whether what the paper says it "
            "intends to do matches what it later shows as done."
        )
    ]
    out = extract_statements(_extraction(blocks), cues=CUES, settings=SETTINGS)
    assert not out.intents


# --- guards ---------------------------------------------------------------------


def test_future_work_guard_excludes_aspirational_statement():
    blocks = [
        _block("Future researchers will be able to extend this system to support mobile devices.")
    ]
    out = extract_statements(_extraction(blocks), cues=CUES, settings=SETTINGS)
    assert not out.intents


def test_scope_negation_guard_excludes_out_of_scope_statement():
    blocks = [_block("Mobile support is outside the scope of this study and will not be built.")]
    out = extract_statements(_extraction(blocks), cues=CUES, settings=SETTINGS)
    assert not out.intents


# --- dedup ------------------------------------------------------------------------


def test_restated_objective_dedupes_by_similarity():
    blocks = [
        _block("Objectives"),
        _block("1. To build a real-time attendance dashboard for instructors."),
        _block("CHAPTER 3: RESULTS"),
        _block("This study aimed to build a real-time attendance dashboard for instructors."),
    ]
    out = extract_statements(_extraction(blocks), cues=CUES, settings=SETTINGS)
    assert len(out.intents) == 1
    assert len(out.intents[0].other_anchors) == 1


def test_distinct_objectives_are_not_merged():
    blocks = [
        _block("Objectives"),
        _block("1. To build a real-time attendance dashboard for instructors."),
        _block("2. To generate a printable weekly summary report."),
    ]
    out = extract_statements(_extraction(blocks), cues=CUES, settings=SETTINGS)
    assert len(out.intents) == 2


# --- recall fixture (stand-in for the second real manuscript, F4.1 QA step) ----

# Hand-labeled: 5 real intents, 4 real outcomes, plus 2 deliberate DISTRACTORS
# (future-work + scope-negation) that must NOT be extracted — same shape as
# V4's seeded-manuscript regression suite (planted signal + planted noise).
_SYNTHETIC_MANUSCRIPT_BLOCKS = [
    _block("1.3 PROJECT OBJECTIVES"),
    _block("1.3.2 Specific Objectives"),
    _block("1. To develop an automated login module using existing school credentials."),
    _block("2. To build a real-time dashboard summarizing weekly attendance."),
    _block("3. To generate a printable summary report for department heads."),
    _block("CHAPTER 1: INTRODUCTION"),
    _block(
        "The current process is entirely manual. This research seeks to reduce the "
        "administrative burden on faculty advisers."
    ),
    _block(
        "The proponents will also provide a mobile-friendly view of the same "
        "dashboard for department heads."
    ),
    _block(
        "Support for biometric login is outside the scope of this study and will "
        "not be implemented."
    ),
    _block("CHAPTER 5: SUMMARY, CONCLUSIONS AND RECOMMENDATIONS"),
    _block("Summary of Findings"),
    _block("1. The login module correctly authenticated 98% of test users."),
    _block("2. The dashboard updated within two seconds of a new attendance entry."),
    _block(
        "Results showed a 40% reduction in the time advisers spent compiling "
        "weekly attendance manually."
    ),
    _block(
        "The study found that department heads accessed the printable report an "
        "average of three times per week."
    ),
    _block("Future researchers could extend this system with biometric login support."),
]

_EXPECTED_INTENT_SUBSTRINGS = [
    "automated login module",
    "real-time dashboard",
    "printable summary report",
    "reduce the administrative burden",
    "mobile-friendly view",
]
_EXPECTED_OUTCOME_SUBSTRINGS = [
    "authenticated 98%",
    "updated within two seconds",
    "40% reduction",
    "accessed the printable report",
]


def test_synthetic_manuscript_recall_meets_80_percent_floor():
    """The ticket's own D-011 gate: the bounded Gemini augmentation pass is
    only built if rule-only recall drops below 80% on the labeled fixture.
    Measured here (not assumed) on BOTH statement types, same as V-006's
    "measure first" precedent."""
    out = extract_statements(
        _extraction(_SYNTHETIC_MANUSCRIPT_BLOCKS), cues=CUES, settings=SETTINGS
    )
    intent_texts = [s.text for s in out.intents]
    outcome_texts = [s.text for s in out.outcomes]

    intent_hits = sum(
        1 for exp in _EXPECTED_INTENT_SUBSTRINGS if any(exp in t for t in intent_texts)
    )
    outcome_hits = sum(
        1 for exp in _EXPECTED_OUTCOME_SUBSTRINGS if any(exp in t for t in outcome_texts)
    )
    intent_recall = intent_hits / len(_EXPECTED_INTENT_SUBSTRINGS)
    outcome_recall = outcome_hits / len(_EXPECTED_OUTCOME_SUBSTRINGS)

    assert intent_recall >= 0.80, f"intent recall {intent_recall:.0%} below the 80% floor"
    assert outcome_recall >= 0.80, f"outcome recall {outcome_recall:.0%} below the 80% floor"
    # Precision half of the same measurement: the two planted distractors
    # (future-work, scope-negation) must not appear anywhere in the output.
    all_texts = intent_texts + outcome_texts
    assert not any("biometric login" in t for t in all_texts)


# --- the real 47-page proposal (V0/V2/V3's own demo document) -----------------

DEMO_PDF = Path(__file__).resolve().parents[2] / "VERIDICAL-DOCUMENTATION.pdf"
demo_pdf_only = pytest.mark.skipif(
    not DEMO_PDF.exists(),
    reason="owner's proposal PDF is local-only (D-007); run this suite locally",
)


@demo_pdf_only
def test_owner_proposal_four_objectives_extracted_as_intents():
    """Ticket AC #1, verbatim: 'Owner's proposal: its 4 specific objectives
    extracted as intents.' Hand-verified against the real document's
    '1.3.2 Specific Objectives' list (Rubric Parsing module, Hybrid Checking
    Engine, four integrity checks, Readiness Report + Dashboard)."""
    from app.ingest.pdf import extract_document

    settings = get_settings()
    extraction = extract_document(str(DEMO_PDF), settings)
    out = extract_statements(extraction, settings=settings)

    assert len(out.intents) == 4
    expected_fragments = [
        "Rubric Parsing module",
        "Hybrid Checking Engine",
        "four integrity checks",
        "Readiness Report and Capstone Instructor Dashboard",
    ]
    for fragment in expected_fragments:
        assert any(fragment in s.text for s in out.intents), f"missing: {fragment}"
    # Precision: this is a proposal, not a full thesis — no findings
    # chapter exists, so zero outcomes is the CORRECT result, not a gap
    # (same honest reasoning as V2's milestone evidence).
    assert out.outcomes == []
