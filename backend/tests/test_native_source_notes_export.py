"""F8: repeated inventories share a print note; distinct check limits remain."""

import fitz
import pytest

from app.report.export import build_report_pdf
from app.report.schemas import IntegrityCheckStatusOut
from tests.test_rule_facts_export import _data, _text

KINDS = ["internal_agreement", "citation_integrity", "statistical_forensics", "originality_reuse"]


def status(kind="internal_agreement", **patch):
    return IntegrityCheckStatusOut.model_validate(
        {
            "check_kind": kind,
            "outcome": "unverifiable",
            "n_skipped_quota": 0,
            "n_skipped_api_down": 0,
            "n_skipped_parse_failure": 0,
            "coverage_reason": "source_reading_order_unresolved",
            "native_math_inventory_state": "complete",
            "n_native_equations_unassessed": 1,
            "n_native_equations_unlocated": 0,
            "native_math_excluded_paragraphs": [7],
            "native_math_coverage_items": [
                {"paragraph": 7, "kind": "paragraph", "n_occurrences": 1}
            ],
            **patch,
        }
    )


def test_identical_native_inventory_is_printed_once_with_all_check_causes():
    data = _data()
    data.report.integrity_check_status = [status(kind) for kind in KINDS]
    before = data.model_dump_json()
    text = _text(build_report_pdf(data))
    assert text.count("1 native Word equation occurrence remains unassessed") == 1
    assert text.count("Native source coverage: note 1") == 4
    assert (
        "Applies to: Internal agreement, Citation integrity, "
        "Statistical forensics, Originality / reuse" in text
    )
    assert text.count("Extracted paragraph 8: Native Word equations unassessed") == 1
    assert data.model_dump_json() == before


@pytest.mark.parametrize("state", ["not_recorded", "unavailable"])
def test_unknown_native_inventories_stay_separate_and_keep_their_state(state):
    from app.report.source_inventory import group_native_source_notes

    source = [
        status(
            kind,
            native_math_inventory_state=state,
            n_native_equations_unassessed=None,
            n_native_equations_unlocated=None,
            native_math_excluded_paragraphs=[],
            native_math_coverage_items=[],
        )
        for kind in KINDS
    ]
    before = [value.model_dump() for value in source]
    notes, references = group_native_source_notes(source)
    assert references == [1, 2, 3, 4] and len(notes) == 4
    assert all(note.state == state and note.count is None for note in notes)
    assert [value.model_dump() for value in source] == before


@pytest.mark.parametrize(
    "patch",
    [
        {"n_native_equations_unassessed": 2},
        {"n_native_equations_unlocated": 1},
        {"native_math_excluded_paragraphs": [7, 8]},
        {"native_math_coverage_items": [{"paragraph": 8, "kind": "paragraph", "n_occurrences": 1}]},
        {
            "native_math_coverage_items": [
                {"paragraph": 7, "kind": "table_cell", "n_occurrences": 1}
            ]
        },
        {"native_math_coverage_items": [{"paragraph": 7, "kind": "paragraph", "n_occurrences": 2}]},
        {
            "native_math_coverage_items": [
                {"paragraph": None, "kind": "paragraph", "n_occurrences": 1}
            ]
        },
        {
            "native_math_coverage_items": [
                {"paragraph": 7, "kind": "paragraph", "n_occurrences": 1}
            ]
            * 2
        },
    ],
)
def test_every_typed_inventory_field_participates_in_identity(patch):
    from app.report.source_inventory import group_native_source_notes

    notes, references = group_native_source_notes([status(), status("citation_integrity", **patch)])
    assert len(notes) == 2 and references == [1, 2]


def test_duplicate_items_and_their_order_are_not_collapsed_or_sorted():
    from app.report.source_inventory import group_native_source_notes

    items = [
        {"paragraph": 8, "kind": "paragraph", "n_occurrences": 1},
        {"paragraph": 7, "kind": "table_cell", "n_occurrences": 1},
    ]
    original = status(
        native_math_coverage_items=items + items[:1],
        n_native_equations_unassessed=3,
        native_math_excluded_paragraphs=[8, 7],
    )
    other = original.model_copy(update={"check_kind": "citation_integrity"}, deep=True)
    notes, references = group_native_source_notes([original, other])
    assert references == [1, 1]
    assert notes[0].items == (("paragraph", 8, 1), ("table_cell", 7, 1), ("paragraph", 8, 1))
    reversed_source = original.model_copy(
        update={"native_math_coverage_items": list(reversed(original.native_math_coverage_items))},
        deep=True,
    )
    # Use a genuinely different ordered tuple, not a palindromic control.
    reversed_source.native_math_coverage_items = (
        original.native_math_coverage_items[1:] + original.native_math_coverage_items[:1]
    )
    assert group_native_source_notes([original, reversed_source])[1] == [1, 2]


def test_absent_inventory_and_cause_only_status_do_not_manufacture_native_limits():
    from app.report.source_inventory import group_native_source_notes

    source = status(
        native_math_inventory_state=None,
        n_native_equations_unassessed=None,
        n_native_equations_unlocated=None,
        native_math_excluded_paragraphs=[],
        native_math_coverage_items=[],
        coverage_reason="ai_not_configured",
    )
    assert group_native_source_notes([source]) == ([], [None])


def test_different_and_unknown_inventory_notes_preserve_counts_without_summing():
    data = _data()
    first = status()
    different = status(
        "citation_integrity",
        n_native_equations_unassessed=2,
        native_math_coverage_items=[{"paragraph": 7, "kind": "table_cell", "n_occurrences": 2}],
    )
    unknown = status(
        "statistical_forensics",
        native_math_inventory_state="not_recorded",
        n_native_equations_unassessed=None,
        n_native_equations_unlocated=None,
        native_math_excluded_paragraphs=[],
        native_math_coverage_items=[],
    )
    unavailable = unknown.model_copy(
        update={"check_kind": "originality_reuse", "native_math_inventory_state": "unavailable"}
    )
    data.report.integrity_check_status = [first, different, unknown, unavailable]
    before = data.model_dump_json()
    payload = build_report_pdf(data)
    text = _text(payload)
    assert "Native source coverage: note 4" in text
    assert "1 native Word equation occurrence remains unassessed" in text
    assert "2 native Word equation occurrences remain unassessed" in text
    assert "3 native Word equation" not in text
    assert "inventory was not recorded" in text and "inventory could not be verified" in text
    assert text.count("Equation presence and count are unknown") == 2
    with fitz.open(stream=payload, filetype="pdf") as document:
        for page in document:
            page_text = page.get_text()
            if "Native Word source coverage - note 3\n" in page_text:
                assert "inventory was not recorded" in page_text
            if "Native Word source coverage - note 4\n" in page_text:
                assert "inventory could not be verified" in page_text
    assert data.model_dump_json() == before


def test_long_inventory_splits_with_the_note_identity_and_preserves_all_source_items():
    data = _data()
    source = status(
        n_native_equations_unassessed=100,
        native_math_excluded_paragraphs=list(range(100)),
        native_math_coverage_items=[
            {"paragraph": i, "kind": "paragraph", "n_occurrences": 1} for i in range(100)
        ],
    )
    data.report.integrity_check_status = [
        source,
        source.model_copy(update={"check_kind": "citation_integrity"}),
    ]
    before = data.model_dump_json()
    payload = build_report_pdf(data)
    with fitz.open(stream=payload, filetype="pdf") as document:
        texts = [page.get_text() for page in document]
    whole = " ".join(" ".join(value.split()) for value in texts)
    for i in range(100):
        assert whole.count(f"Extracted paragraph {i + 1}: Native Word equations unassessed") == 1
    for text in texts:
        if "Extracted paragraph" in text:
            assert "Native Word source coverage" in text and "note 1" in text
    assert data.model_dump_json() == before


def positive_criterion_coverage():
    from app.report.source_coverage import project_source_coverage

    return project_source_coverage(
        {
            "native_math_coverage_version": "v1",
            "native_math_inventory_state": "complete",
            "native_math_inventory_scope": "main_document_body",
            "n_native_equations_unassessed": 1,
            "n_native_equations_unlocated": 0,
            "native_math_excluded_paragraphs": [7],
            "native_math_coverage_items": [
                {"paragraph": 7, "kind": "paragraph", "n_occurrences": 1}
            ],
        }
    )


@pytest.mark.parametrize("manual", [False, True])
def test_criterion_input_limit_precedes_automated_basis_and_keeps_manual_attribution(manual):
    from app.report.export import _criterion_flowable, _register_fonts, _styles
    from app.report.schemas import ResolutionOut

    row = _data().report.results[0]
    row.kind = row.type = "semantic"
    row.source_coverage = positive_criterion_coverage()
    row.reasoning = "SAVED_AUTOMATED_EXPLANATION"
    if manual:
        row.resolution = ResolutionOut(
            type="mark_fail", reason="CURRENT_INSTRUCTOR_REASON", ai_majority_verdict=None
        )
    before = row.model_dump_json()
    parts = _criterion_flowable(row, _styles(_register_fonts()))
    texts = [part.getPlainText() for part in parts]
    whole = " ".join(texts)
    assert "omitted 1 native Word equation occurrence" in whole
    assert "Text and table content affected" in whole
    assert "other package parts are not established" in whole
    if manual:
        assert texts.index("Earlier automated assessment input") > next(
            i for i, value in enumerate(texts) if "CURRENT_INSTRUCTOR_REASON" in value
        )
        assert "Saved automated assessment input" not in whole
    else:
        assert texts.index("Saved automated assessment input") < texts.index(
            "SAVED_AUTOMATED_EXPLANATION"
        )
    assert row.model_dump_json() == before


@pytest.mark.parametrize("state", ["not_recorded", "unavailable", "not_applicable"])
def test_criterion_unknown_input_is_distinct_from_inapplicability(state):
    from app.report.export import _criterion_source_flowables, _register_fonts, _styles
    from app.report.source_coverage import project_source_coverage

    row = _data().report.results[0]
    row.kind = row.type = "semantic"
    row.source_coverage = project_source_coverage(
        {
            "native_math_coverage_version": "v1",
            "native_math_inventory_state": state,
            "n_native_equations_unassessed": 0 if state == "not_applicable" else None,
        }
    )
    whole = " ".join(
        part.getPlainText() for part in _criterion_source_flowables(row, _styles(_register_fonts()))
    )
    if state == "not_applicable":
        assert not whole
    else:
        assert "Equation presence and count are unknown" in whole
        assert ("was not recorded" if state == "not_recorded" else "could not be verified") in whole
        assert "0 native" not in whole


def test_unversioned_inapplicable_claim_stays_unavailable_in_print():
    from app.report.export import _criterion_source_flowables, _register_fonts, _styles
    from app.report.source_coverage import project_source_coverage

    row = _data().report.results[0]
    row.kind = row.type = "semantic"
    row.source_coverage = project_source_coverage(
        {
            "native_math_inventory_state": "not_applicable",
            "n_native_equations_unassessed": 0,
        }
    )
    whole = " ".join(
        part.getPlainText() for part in _criterion_source_flowables(row, _styles(_register_fonts()))
    )
    assert "inventory could not be verified" in whole
    assert "Equation presence and count are unknown" in whole


def test_old_automatic_result_is_qualified_without_changing_manual_grade_or_saved_values():
    from app.report.export import _result_display
    from app.report.schemas import ResolutionOut

    row = _data().report.results[0]
    row.outcome, row.score, row.rule_scope_unconfirmed = "passed", 100, True
    before = row.model_dump_json()
    assert _result_display(row) == ("Previously recorded: Pass", "neutral")
    assert row.model_dump_json() == before
    row.resolution = ResolutionOut(
        type="mark_pass", reason="Verified the full requirement", ai_majority_verdict=None
    )
    assert _result_display(row) == ("Pass", "success")


@pytest.mark.parametrize("state", [None, "unavailable", "needs_review"])
def test_empty_visual_readings_are_omitted_but_nonempty_unassessed_readings_remain(state):
    from app.ingest.schemas import FigureReading

    data = _data()
    data.report.figure_readings = (
        []
        if state is None
        else [
            FigureReading(
                image_index=0,
                status=state,
                reason="SAVED_IMAGE_LIMIT",
                source_binding={"locator": {"paragraph": 7}},
            )
        ]
    )
    before = data.model_dump_json()
    text = _text(build_report_pdf(data))
    if state is None:
        assert "Saved visual readings" not in text
        assert "0 of 0" not in text
    else:
        assert "Saved visual readings" in text
        assert "SAVED_IMAGE_LIMIT" in text and state in text
        assert "0 of 1 image occurrences. 1 remain unassessed" in text
    assert data.model_dump_json() == before
