"""Omitted bibliography outline nodes must not discard later anchored headings."""

import pytest

from app.ingest.patterns import load_patterns
from app.ingest.references import (
    ReferenceSelection,
    _bibliography_continues,
    extract_references,
    non_reference_blocks,
    reference_entry_locations,
    reference_pages_not_assessed,
    select_references,
)
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock


def bibliography(*, later_page=1):
    texts = [
        "Bibliography",
        "[1] Author A. (2024). First source.",
        "[2] Author B. Resource, 2023. URL https://example.org/resource.",
        ".1\nAdditional materials",
        "The accompanying survey is available separately.",
    ]
    blocks = [
        TextBlock(
            page=1 if index < 3 else later_page,
            bbox=(10, index * 30, 400, index * 30 + 20),
            text=text,
            max_font_size=24 if index == 0 else 14 if index == 3 else 10,
            bold_ratio=1 if index in (0, 3) else 0,
        )
        for index, text in enumerate(texts)
    ]
    return ExtractionResult(
        page_count=later_page,
        anchor_kind="page",
        image_only=False,
        text_chars=300,
        blocks=blocks,
        images=[],
        section_tree=SectionTree(
            source="embedded_toc",
            nodes=[
                SectionNode(title="Additional materials", page=later_page, level=2),
            ],
        ),
    )


@pytest.mark.parametrize("page", [1, 2])
def test_later_source_anchored_heading_ends_omitted_bibliography(page):
    result = bibliography(later_page=page)
    refs = extract_references(result, load_patterns())
    assert len(refs) == 2
    assert refs[-1].raw_text == result.blocks[2].text
    assert result.blocks[3] in non_reference_blocks(result, load_patterns())


@pytest.mark.parametrize(
    "mutation", ["wrong_page", "duplicate", "body", "weak", "heuristic", "incomplete"]
)
def test_uncertain_heading_cannot_truncate_reference_inventory(mutation):
    result = bibliography()
    if mutation == "wrong_page":
        result.section_tree.nodes[0].page = 2
    elif mutation == "duplicate":
        result.blocks.append(result.blocks[3].model_copy())
    elif mutation == "body":
        result.blocks[3].text += "\nThis paragraph shares the block."
    elif mutation == "weak":
        result.blocks[3].max_font_size = 10
        result.blocks[3].bold_ratio = 0
    elif mutation == "heuristic":
        result.section_tree.source = "heuristics"
    elif mutation == "incomplete":
        result.blocks[2].text = "[2] Author B. (2023)."
    refs = extract_references(result, load_patterns())
    assert any("Additional materials" in ref.raw_text for ref in refs)


@pytest.mark.parametrize(
    "continuation",
    [
        "[3] Author C. (2025). A later reference.",
        "Author, C. (2025). An author-year reference.",
        "[3] [Online]. Available: https://example.org/three.",
        "[3]\n[Online]. Available: https://example.org/three.",
    ],
)
def test_bibliography_subsection_keeps_following_references(continuation):
    result = bibliography()
    result.blocks[4].text = continuation
    refs = extract_references(result, load_patterns())
    assert any(" ".join(continuation.split()) in ref.raw_text for ref in refs)


def test_restarted_bibliography_is_preserved_but_not_a_unique_reference_inventory():
    result = bibliography()
    result.blocks[4].text = "[1] Author C. (2025). A restarted bibliography."
    selection = select_references(result, load_patterns())
    assert selection.issues == ("ambiguous_duplicate_reference_number",)
    assert any(result.blocks[4].text in part.text for part in selection.span)
    assert extract_references(result, load_patterns()) == []
    assert non_reference_blocks(result, load_patterns()) == []


def test_explicit_docx_style_boundary_uses_paragraph_identity():
    result = bibliography()
    result.anchor_kind = "paragraph"
    result.page_count = 0
    result.section_tree.source = "styles"
    for index, block in enumerate(result.blocks):
        block.page, block.bbox, block.paragraph = None, None, index
    result.section_tree.nodes[0].page = None
    result.section_tree.nodes[0].paragraph = 3
    refs = extract_references(result, load_patterns())
    assert len(refs) == 2
    assert all("Additional materials" not in ref.raw_text for ref in refs)


@pytest.mark.parametrize("label", ["[3]", "3.", "3)"])
def test_detached_numbered_label_retains_unparsed_bibliography_subsection(label):
    result = bibliography()
    result.blocks[4].text = label
    result.blocks.append(
        result.blocks[4].model_copy(
            update={
                "text": "[Online]. Available: https://example.org/three.",
            }
        )
    )
    refs = extract_references(result, load_patterns())
    assert any("Additional materials" in ref.raw_text for ref in refs)
    assert any("https://example.org/three." in ref.raw_text for ref in refs)


BODY_TEXT = "The clinic booking system was evaluated with twenty staff members."


def body_and_restarted_bibliography():
    result = bibliography()
    result.blocks[4].text = "[1] Author C. (2025). A restarted bibliography."
    result.blocks.insert(
        0,
        TextBlock(page=1, bbox=(10, -60, 400, -40), text=BODY_TEXT, max_font_size=10, bold_ratio=0),
    )
    return result


def test_text_only_callers_keep_the_body_beside_an_ambiguous_bibliography():
    result = body_and_restarted_bibliography()
    patterns = load_patterns()
    assert select_references(result, patterns).issues == ("ambiguous_duplicate_reference_number",)
    # The default stays strict for callers that need an exact bibliography.
    assert non_reference_blocks(result, patterns) == []
    kept = [b.text for b in non_reference_blocks(result, patterns, ambiguous_entries_ok=True)]
    assert BODY_TEXT in kept
    assert not any("Author" in text for text in kept)


@pytest.mark.parametrize("issue", ["ambiguous_reference_heading", "invalid_source_line_inventory"])
def test_an_unknown_bibliography_location_still_leaves_no_body(issue):
    result = body_and_restarted_bibliography()
    patterns = load_patterns()
    located = select_references(result, patterns)
    unknown = ReferenceSelection(located.groups, located.span, (issue,))
    assert (
        non_reference_blocks(result, patterns, selection=unknown, ambiguous_entries_ok=True) == []
    )


def _numbered_bibliography(lines):
    blocks = [
        TextBlock(
            page=1,
            bbox=(10, index * 30, 400, index * 30 + 20),
            text=text,
            max_font_size=24 if index == 0 else 10,
            bold_ratio=1 if index == 0 else 0,
        )
        for index, text in enumerate(["References", *lines])
    ]
    return ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(block.text) for block in blocks),
        blocks=blocks,
        images=[],
        section_tree=SectionTree(source="none", nodes=[]),
    )


def test_a_year_starting_a_wrapped_line_is_not_a_list_number():
    result = _numbered_bibliography(
        [
            "Reyes, J. Booking queues in rural clinics. In Proceedings of the Health IT Forum,",
            "2023. URL https://example.org/reyes.",
            "Santos, A. Measuring appointment no-shows. Journal of Examples, 4(2), 10-20,",
            "2023. URL https://example.org/santos.",
        ]
    )
    selection = select_references(result, load_patterns())
    assert "ambiguous_duplicate_reference_number" not in selection.issues


def test_labels_read_apart_from_their_entries_refuse_the_reference_list():
    result = _numbered_bibliography(
        [
            "[1]",
            "[2]",
            "Reyes, J. (2024). Booking queues in rural clinics. Journal of Examples.",
            "Santos, A. (2023). Measuring appointment no-shows. Journal of Examples.",
        ]
    )
    selection = select_references(result, load_patterns())
    assert "detached_reference_labels" in selection.issues
    assert extract_references(result, load_patterns()) == []


def _two_column_bibliography(*, second_label_y=90):
    def block(text, x, top, bottom, *, size=10, bold=0):
        width = 18 if text.startswith("[") else 300
        return TextBlock(
            page=1, bbox=(x, top, x + width, bottom), text=text, max_font_size=size, bold_ratio=bold
        )

    blocks = [
        block("References", 10, 0, 20, size=24, bold=1),
        block("[1]", 78, 30, 42),
        block("[2]", 78, second_label_y, second_label_y + 12),
        block("Reyes, J. Booking queues in rural clinics. Journal of Examples,", 108, 30, 42),
        block("4(2), 10-20. https://example.org/records/", 108, 44, 56),
        block("7534015.", 108, 58, 70),
        block("Santos, A. Measuring appointment no-shows. Health IT Notes, 2023.", 108, 90, 102),
    ]
    return ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        blocks=blocks,
        images=[],
        section_tree=SectionTree(source="none", nodes=[]),
    )


def test_labels_printed_beside_their_entries_are_paired_by_line():
    refs = extract_references(_two_column_bibliography(), load_patterns())
    assert [" ".join(ref.raw_text.split()) for ref in refs] == [
        "[1] Reyes, J. Booking queues in rural clinics. Journal of Examples, "
        "4(2), 10-20. https://example.org/records/ 7534015.",
        "[2] Santos, A. Measuring appointment no-shows. Health IT Notes, 2023.",
    ]


def test_a_label_with_no_entry_on_its_line_refuses_the_pairing():
    result = _two_column_bibliography(second_label_y=150)
    assert "detached_reference_labels" in select_references(result, load_patterns()).issues
    assert extract_references(result, load_patterns()) == []


APPENDIX_B_REFERENCES = [
    "Cruz, M. (2021). Mobile queueing for barangay health centers. Journal of Examples, 7(1), 1-9.",
    "Lim, K. (2020). Appointment reminders and no-show rates. Journal of Examples, 6(3), 22-30.",
]
CHAPTER_TEXT = "The clinic booking system was evaluated with twenty staff members over six weeks. "


def unclear_end_manuscript():
    """The outline lists the appendices but not the hand-bolded REFERENCES,
    one entry cannot be parsed, and Appendix B prints more entries."""
    spec = [
        (1, "CHAPTER 1", 16, 1),
        (1, CHAPTER_TEXT * 12, 11, 0),
        (2, "REFERENCES", 16, 1),
        (2, "Department of Health. Clinic appointment guidelines. Manila: DOH Press", 11, 0),
        (
            2,
            "Santos, A. (2022). Measuring appointment no-shows. Journal of Examples, 5(1), 3-8.",
            11,
            0,
        ),
        (3, "APPENDIX A", 16, 1),
        (3, "The client letter of intent is reproduced on the next page for the panel.", 11, 0),
        (4, "APPENDIX B", 16, 1),
        (4, APPENDIX_B_REFERENCES[0], 11, 0),
        (4, APPENDIX_B_REFERENCES[1], 11, 0),
    ]
    blocks = [
        TextBlock(
            page=page,
            bbox=(72, 100 + index * 30, 540, 120 + index * 30),
            text=text,
            max_font_size=size,
            bold_ratio=bold,
        )
        for index, (page, text, size, bold) in enumerate(spec)
    ]
    nodes = [
        SectionNode(title="CHAPTER 1", page=1, level=1),
        SectionNode(title="APPENDIX A", page=3, level=1),
        SectionNode(title="APPENDIX B", page=4, level=1),
    ]
    return ExtractionResult(
        page_count=4,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        blocks=blocks,
        images=[],
        section_tree=SectionTree(source="embedded_toc", nodes=nodes),
    )


def test_an_unclear_end_leaves_out_everything_from_the_reference_list_onward():
    result = unclear_end_manuscript()
    patterns = load_patterns()
    selection = select_references(result, patterns)
    assert "ambiguous_reference_end" in selection.issues
    kept = "\n".join(
        b.text for b in non_reference_blocks(result, patterns, ambiguous_entries_ok=True)
    )
    assert CHAPTER_TEXT.strip() in kept
    assert not any(reference[:30] in kept for reference in APPENDIX_B_REFERENCES)
    assert "letter of intent" not in kept
    assert reference_pages_not_assessed(result, selection) == [2, 3, 4]


def test_a_clear_reference_list_leaves_no_pages_unassessed():
    result = body_and_restarted_bibliography()
    selection = select_references(result, load_patterns())
    assert reference_pages_not_assessed(result, selection) == []


def _paired(*rows, page=1):
    """Blocks of (text, x0, top) on one page under a References heading."""
    blocks = [
        TextBlock(
            page=page, bbox=(10, 0, 300, 20), text="References", max_font_size=24, bold_ratio=1
        )
    ]
    for text, x0, top in rows:
        width = 18 if text.startswith("[") else 400
        blocks.append(
            TextBlock(
                page=page,
                bbox=(x0, top, x0 + width, top + 12),
                text=text,
                max_font_size=10,
                bold_ratio=0,
            )
        )
    return ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        blocks=blocks,
        images=[],
        section_tree=SectionTree(source="none", nodes=[]),
    )


REYES_ENTRY = "Reyes, J. Booking queues in rural clinics. Journal of Examples, 2024."
SANTOS_ENTRY = "Santos, A. Measuring appointment no-shows. Health IT Notes, 2023."
CRUZ_ENTRY = "Cruz, M. Mobile queueing for barangay health centers. Notes, 2021."


@pytest.mark.parametrize(
    "rows",
    [
        # A repeated label.
        [
            ("[1]", 78, 30),
            ("[2]", 78, 60),
            ("[2]", 78, 90),
            (REYES_ENTRY, 108, 30),
            (SANTOS_ENTRY, 108, 60),
            (CRUZ_ENTRY, 108, 90),
        ],
        # Labels in two columns.
        [
            ("[1]", 78, 30),
            ("[2]", 78, 60),
            ("[3]", 320, 30),
            ("[4]", 320, 60),
            (REYES_ENTRY[:30], 108, 30),
            (SANTOS_ENTRY[:30], 108, 60),
            (CRUZ_ENTRY[:30], 350, 30),
            (REYES_ENTRY[30:], 350, 60),
        ],
        # Labels in two columns beside a single text column.
        [
            ("[1]", 20, 30),
            ("[2]", 20, 60),
            ("[3]", 78, 90),
            (REYES_ENTRY, 108, 30),
            (SANTOS_ENTRY, 108, 60),
            (CRUZ_ENTRY, 108, 90),
        ],
        # One label on the line of two text blocks.
        [
            ("[1]", 78, 30),
            ("[2]", 78, 60),
            (REYES_ENTRY[:30], 108, 28),
            (SANTOS_ENTRY[:30], 108, 32),
            (CRUZ_ENTRY, 108, 60),
        ],
        # Text before the first label.
        [
            ("[1]", 78, 30),
            ("[2]", 78, 60),
            (CRUZ_ENTRY, 108, 10),
            (REYES_ENTRY, 108, 30),
            (SANTOS_ENTRY, 108, 60),
        ],
    ],
)
def test_untrustworthy_label_layouts_refuse_the_list(rows):
    result = _paired(*rows)
    assert "detached_reference_labels" in select_references(result, load_patterns()).issues
    assert extract_references(result, load_patterns()) == []


def test_a_paired_entry_keeps_its_printed_number():
    result = _paired(
        ("[1]", 78, 30), ("[2]", 78, 60), (REYES_ENTRY, 108, 30), (SANTOS_ENTRY, 108, 60)
    )
    locations = reference_entry_locations(result, load_patterns())
    assert [location.printed_number for location in locations] == [1, 2]


def test_an_identifier_tail_after_an_end_heading_is_not_more_bibliography():
    tail = [TextBlock(page=1, text="7534015.", max_font_size=10, bold_ratio=0)]
    assert not _bibliography_continues(tail)
    label = [TextBlock(page=1, text="12.", max_font_size=10, bold_ratio=0)]
    assert _bibliography_continues(label)


def test_word_paragraphs_that_hold_only_a_label_refuse_the_list():
    result = bibliography()
    result.anchor_kind = "paragraph"
    result.page_count = 0
    result.section_tree.source = "styles"
    for index, block in enumerate(result.blocks):
        block.page, block.bbox, block.paragraph = None, None, index
    result.section_tree.nodes[0].page = None
    result.section_tree.nodes[0].paragraph = 3
    result.blocks[1].text, result.blocks[2].text = "[1]", "[2]"
    assert "detached_reference_labels" in select_references(result, load_patterns()).issues
