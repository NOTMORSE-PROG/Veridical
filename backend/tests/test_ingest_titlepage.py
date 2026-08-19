"""V-063: `extract_title_page`'s own tests. Fixture tests over real
front-matter shapes (the ticket's own QA step), including a PDF built to
match the owner's real manuscript title page, run through the REAL PDF
extraction pipeline end-to-end -- not just hand-crafted ExtractionResult
objects.
"""

from pathlib import Path

from app.ingest.patterns import load_patterns
from app.ingest.pdf import extract_document
from app.ingest.schemas import ExtractionResult, TextBlock
from app.ingest.titlepage import extract_title_page
from tests.test_ingest_pdf import PdfBuilder

PATTERNS = load_patterns()


def _block(text: str, page: int = 1) -> TextBlock:
    return TextBlock(page=page, text=text, max_font_size=12.0, bold_ratio=0.0)


def _result(blocks: list[TextBlock]) -> ExtractionResult:
    from app.ingest.schemas import SectionTree

    return ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        section_tree=SectionTree(source="none"),
        blocks=blocks,
        images=[],
    )


def test_owners_real_title_page_shape_extracts_every_field():
    """The exact text from the ticket's own DECIDED section
    (`VERIDICAL-DOCUMENTATION.pdf` p.1)."""
    result = _result(
        [
            _block(
                "VERIDICAL: An AI-Assisted Quality and Integrity Assurance Platform"
                " for Academic Research"
            ),
            _block("A Capstone Project Presented to the Faculty of the"),
            _block("Technological Institute of the Philippines"),
            _block("College of Information Technology Education"),
            _block("In Partial Fulfillment of the Requirements for the Degree of"),
            _block("Bachelor of Science in Information Technology"),
            _block("By:"),
            _block("Condino, Mark Andrei A"),
            _block("Concepcion, Marc Laurence M."),
            _block("Munoz, John Marvin Oric A."),
            _block("Adviser:"),
            _block("Mr. Jhon Angelo M. San Andres"),
            _block("June 2026"),
        ]
    )
    proposal = extract_title_page(result, PATTERNS)

    assert not proposal.extraction_failed
    assert proposal.short_name is not None
    assert proposal.short_name.value == "VERIDICAL"
    assert proposal.title is not None
    assert proposal.title.value.startswith("VERIDICAL: An AI-Assisted")
    assert [m.value for m in proposal.members] == [
        "Condino, Mark Andrei A",
        "Concepcion, Marc Laurence M.",
        "Munoz, John Marvin Oric A.",
    ]
    assert proposal.program is not None
    assert proposal.program.value == "IT"
    assert proposal.adviser is not None
    assert proposal.adviser.value == "Mr. Jhon Angelo M. San Andres"
    # AC3: every field carries its own anchor for the instructor to check.
    assert proposal.short_name.anchor == "p. 1"
    assert proposal.members[0].anchor == "p. 1"


def test_single_line_adviser_never_gets_fabricated_into_the_members_list():
    """ux-critic (V-063 review), reproduced live: a title page with
    "Adviser: Prof. Ana Lopez" on ONE line (at least as common a
    convention as the two-line "Adviser:" / name-on-its-own-line form)
    used to match neither the dedicated adviser field NOR the member-
    loop's own stop condition -- the adviser line, and every block after
    it (including a trailing date), silently swept into `members` as
    fabricated entries carrying the SAME evidence anchor as real members.
    AC5's "never fabricates" must hold for both title-page conventions,
    not just the one the ticket's own worked example happened to use."""
    result = _result(
        [
            _block("AI-Powered Capstone Readiness Advisor: VERIDICAL"),
            _block("Bachelor of Science in Information Technology"),
            _block("By:"),
            _block("Dela Cruz, Juan"),
            _block("Santos, Maria"),
            _block("Adviser: Prof. Ana Lopez"),
            _block("August 2026"),
        ]
    )
    proposal = extract_title_page(result, PATTERNS)

    assert [m.value for m in proposal.members] == ["Dela Cruz, Juan", "Santos, Maria"]
    assert proposal.adviser is not None
    assert proposal.adviser.value == "Prof. Ana Lopez"
    assert proposal.adviser.anchor == "p. 1"


def test_the_owners_own_natural_experiment_same_short_name_different_subtitle():
    """The ticket's own proof for why short-name (not full-title) is the
    match key: the owner's real rubric form and real manuscript disagree
    on subtitle AND separator character, but agree on short name."""
    rubric_form_title = _result(
        [_block("VERIDICAL - An AI-Powered Statistical Falsification Detection Platform")]
    )
    manuscript_title = _result(
        [
            _block(
                "VERIDICAL: An AI-Assisted Quality and Integrity Assurance Platform"
                " for Academic Research"
            )
        ]
    )
    a = extract_title_page(rubric_form_title, PATTERNS)
    b = extract_title_page(manuscript_title, PATTERNS)
    assert a.short_name is not None and b.short_name is not None
    assert a.short_name.value == b.short_name.value == "VERIDICAL"


def test_title_with_no_separator_uses_the_whole_title_as_short_name():
    result = _result([_block("A STUDY OF THINGS")])
    proposal = extract_title_page(result, PATTERNS)
    assert proposal.short_name is not None
    assert proposal.short_name.value == "A STUDY OF THINGS"


def test_earliest_separator_wins_not_whichever_appears_first_in_the_pattern_file():
    # ":" appears in the data file before " - ", but here " - " occurs
    # EARLIER in the actual text -- the earliest occurrence must win.
    result = _result([_block("Team Name - A Study: Of Things")])
    proposal = extract_title_page(result, PATTERNS)
    assert proposal.short_name is not None
    assert proposal.short_name.value == "Team Name"


def test_filipino_naming_surname_first_middle_initial_no_trailing_period_on_one():
    """Ticket edge case, verbatim: `Condino, Mark Andrei A` has no
    trailing period while its siblings do -- must not be dropped or
    mangled, matched on the whole line, never reformatted."""
    result = _result(
        [
            _block("Team Title"),
            _block("By:"),
            _block("Condino, Mark Andrei A"),
            _block("Adviser:"),
            _block("Someone Else"),
        ]
    )
    proposal = extract_title_page(result, PATTERNS)
    assert [m.value for m in proposal.members] == ["Condino, Mark Andrei A"]


def test_no_by_block_proposes_with_no_members_and_says_so_never_invents():
    result = _result([_block("Team Title"), _block("Some other front matter line")])
    proposal = extract_title_page(result, PATTERNS)
    assert not proposal.extraction_failed
    assert proposal.short_name is not None
    assert proposal.members == []
    assert proposal.adviser is None


def test_completely_empty_first_page_is_an_honest_extraction_failure():
    """AC5: an image-only scan (no extractable text at all) must produce
    the explicit failure state, never a fabricated "Ungrouped"-style
    default and never a silent empty proposal that looks successful."""
    result = _result([])
    proposal = extract_title_page(result, PATTERNS)
    assert proposal.extraction_failed
    assert proposal.title is None
    assert proposal.short_name is None


def test_degree_line_not_present_leaves_program_unset_never_guessed():
    result = _result([_block("Team Title"), _block("By:"), _block("Someone")])
    proposal = extract_title_page(result, PATTERNS)
    assert proposal.program is None


def test_unmapped_degree_text_leaves_program_unset_rather_than_crashing():
    result = _result([_block("Team Title"), _block("Bachelor of Science in Nursing")])
    proposal = extract_title_page(result, PATTERNS)
    assert proposal.program is None


def test_real_pdf_pipeline_end_to_end_matches_the_owners_shape(tmp_path: Path):
    """Not a hand-crafted ExtractionResult -- a real PDF built and run
    through the actual `pdf.extract_document` pipeline, proving the
    deterministic parser works on REAL extracted blocks (real bbox/font
    metadata, real page-furniture filtering), not just idealized input."""
    b = PdfBuilder()
    b.new_page()
    b.line("VERIDICAL: An AI-Assisted Quality Platform", size=16, bold=True)
    b.line("A Capstone Project Presented to the Faculty of the")
    b.line("Technological Institute of the Philippines")
    b.line("In Partial Fulfillment of the Requirements for the Degree of")
    b.line("Bachelor of Science in Information Technology")
    b.line("By:")
    b.line("Condino, Mark Andrei A")
    b.line("Concepcion, Marc Laurence M.")
    b.line("Adviser:")
    b.line("Mr. Jhon Angelo M. San Andres")
    path = b.save(tmp_path / "titlepage.pdf")

    from app.config import get_settings

    result = extract_document(str(path), get_settings())
    proposal = extract_title_page(result, PATTERNS)

    assert not proposal.extraction_failed
    assert proposal.short_name is not None
    assert proposal.short_name.value == "VERIDICAL"
    assert proposal.program is not None
    assert proposal.program.value == "IT"
    assert [m.value for m in proposal.members] == [
        "Condino, Mark Andrei A",
        "Concepcion, Marc Laurence M.",
    ]
    assert proposal.adviser is not None
    assert proposal.adviser.value == "Mr. Jhon Angelo M. San Andres"
