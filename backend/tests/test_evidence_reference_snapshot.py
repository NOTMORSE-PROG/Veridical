"""Immutable evidence association and privacy; no native/files/DB/network work."""

from types import SimpleNamespace as Row

import pytest
from pydantic import ValidationError

from app.report.evidence_references import (
    EvidenceReferenceSnapshot,
    EvidenceSourceReference,
    build_evidence_reference_snapshot,
    public_evidence_reference,
)


def criterion(**changes):
    return Row(
        **{
            "check_result_id": 11,
            "criterion_id": 21,
            "anchor": "page 9",
            "evidence": [Row(quote="First complete recorded excerpt.", anchor="page 2")],
            "reasoning": "NARRATIVE ONLY page 999 and pages 7-8",
            "resolution": {"reason": "PRIVATE_OVERRIDE_REASON"},
            **changes,
        }
    )


def flag(**changes):
    return Row(
        **{
            "id": 31,
            "check_run_id": 7,
            "check_result_id": 12,
            "assessment_revision": 3,
            "check_kind": "statistical_forensics",
            "ai_verdict_summary": None,
            "is_passage_level": False,
            "agreement_pair": None,
            "passage_pair": None,
            "evidence_excerpt": "A saved statistical expression.",
            "page_anchor": "page 4",
            "annotation": "PRIVATE_ANNOTATION",
            "override_reason": "PRIVATE_OVERRIDE_REASON",
            "detail": {"file_ref": "PRIVATE_SOURCE_PATH"},
            **changes,
        }
    )


def report(**changes):
    return Row(
        **{
            "check_run_id": 7,
            "manuscript_id": 8,
            "assessment_revision": 3,
            "results": [criterion()],
            "decision_note": "PRIVATE_FIELD_NOT_SELECTED",
            **changes,
        }
    )


def build(current=None, flags=(), **changes):
    return build_evidence_reference_snapshot(
        current or report(),
        flags,
        **{
            "expected_flag_ids": [item.id for item in flags],
            "source_sha256": "a" * 64,
            "audience": "private",
            **changes,
        },
    )


def test_complete_quotes_and_distinct_same_page_entries_are_retained_without_narrative_pages():
    row = criterion(
        evidence=[
            Row(quote="First complete quote.", anchor="page 2"),
            Row(quote="Second complete quote.", anchor="page 2"),
            Row(quote="", anchor="unsupported location"),
            Row(quote="Unplaced quote.", anchor=""),
        ]
    )
    snapshot = build(report(results=[row]))
    assert [ref.quote for ref in snapshot.references] == [item.quote for item in row.evidence]
    assert [ref.anchor for ref in snapshot.references] == [item.anchor for item in row.evidence]
    assert len({ref.reference_id for ref in snapshot.references}) == 4
    assert "page 9" not in snapshot.model_dump_json()
    assert "999" not in snapshot.model_dump_json()
    assert all(ref.source_state == "not_checked" for ref in snapshot.references)


@pytest.mark.parametrize("anchor", [None, "", "page 5", "pages 5 and 9", "whole document"])
def test_no_saved_excerpt_remains_an_explicit_empty_scope(anchor):
    ref = build(report(results=[criterion(evidence=[], anchor=anchor)])).references[0]
    assert ref.role == "criterion_scope" and ref.quote == "" and ref.anchor == anchor


@pytest.mark.parametrize(
    "dimension",
    ["revision", "run", "manuscript", "source", "audience", "owner", "index", "quote", "anchor"],
)
def test_reference_identity_changes_with_each_binding_dimension(dimension):
    original = build().references[0].reference_id
    row = criterion()
    current = report(results=[row])
    kwargs = {}
    if dimension == "revision":
        current.assessment_revision = 4
    if dimension == "run":
        current.check_run_id = 9
    if dimension == "manuscript":
        current.manuscript_id = 9
    if dimension == "source":
        kwargs["source_sha256"] = "b" * 64
    if dimension == "audience":
        kwargs["audience"] = "public"
    if dimension == "owner":
        row.check_result_id = 13
    if dimension == "index":
        row.evidence.insert(0, Row(quote="Another excerpt.", anchor="page 1"))
    if dimension == "quote":
        row.evidence[0].quote += " Entire additional sentence."
    if dimension == "anchor":
        row.evidence[0].anchor = "page 3"
    refs = build(current, **kwargs).references
    assert refs[-1].reference_id != original


def test_copied_snapshot_is_deeply_immutable_and_does_not_alias_current_dtos():
    current = report()
    snapshot = build(current)
    before = snapshot.model_dump_json()
    current.results[0].evidence[0].quote = "Changed current quote."
    current.results.clear()
    assert snapshot.model_dump_json() == before
    assert isinstance(snapshot.references, tuple)
    with pytest.raises(ValidationError):
        snapshot.references[0].quote = "Changed"
    with pytest.raises(ValidationError):
        snapshot.references = ()


@pytest.mark.parametrize("source", [None, "", "invalid", "A" * 64, "a" * 63])
def test_missing_or_invalid_raw_source_identity_does_not_become_placement_proof(source):
    ref = build(source_sha256=source).references[0]
    assert ref.source_state == "source_identity_unverified"
    assert ref.source_sha256 is None and ref.quote


def test_unknown_revision_is_not_coerced_to_current_zero():
    snapshot = build(report(assessment_revision=None), [flag(assessment_revision=None)])
    assert snapshot.assessment_revision is None
    assert all(ref.source_state == "assessment_revision_unverified" for ref in snapshot.references)
    assert (
        snapshot.references[0].reference_id
        != build(report(assessment_revision=0)).references[0].reference_id
    )


@pytest.mark.parametrize(
    "case",
    [
        "foreign run",
        "other revision",
        "unknown flag revision",
        "duplicate flag",
        "missing flag",
        "extra flag",
        "duplicate expected",
        "boolean revision",
        "duplicate result",
    ],
)
def test_incoherent_aggregate_is_rejected_whole(case):
    current, flags, expected = report(), [flag()], [31]
    if case == "foreign run":
        flags[0].check_run_id = 8
    if case == "other revision":
        flags[0].assessment_revision = 2
    if case == "unknown flag revision":
        flags[0].assessment_revision = None
    if case == "duplicate flag":
        flags.append(flag())
    if case == "missing flag":
        expected.append(32)
    if case == "extra flag":
        expected.clear()
    if case == "duplicate expected":
        expected.append(31)
    if case == "boolean revision":
        current.assessment_revision = True
    if case == "duplicate result":
        current.results.append(criterion())
    with pytest.raises(ValueError):
        build(current, flags, expected_flag_ids=expected)


def test_flag_input_order_does_not_change_identity_or_association():
    first, second = flag(), flag(id=32)
    assert build(flags=[first, second]).model_dump() == build(flags=[second, first]).model_dump()


def test_agreement_uses_two_actual_statements_even_on_the_same_page():
    pair = Row(
        intent_text="Actual recorded intent.",
        intent_anchor="page 2",
        outcome_text="Actual recorded result.",
        outcome_anchor="page 2",
    )
    refs = build(
        report(results=[]), [flag(check_kind="internal_agreement", agreement_pair=pair)]
    ).references
    assert [(ref.role, ref.quote, ref.anchor) for ref in refs] == [
        ("agreement_intent", pair.intent_text, "page 2"),
        ("agreement_outcome", pair.outcome_text, "page 2"),
    ]
    assert refs[0].reference_id != refs[1].reference_id


@pytest.mark.parametrize(
    "kind",
    [
        "reuse_exact_duplicate",
        "reuse_high_similarity",
        "reuse_exact_duplicate_chapter",
        "reuse_high_similarity_chapter",
        "reuse_same_instructor_resubmission",
    ],
)
def test_known_reuse_summary_without_a_pair_never_becomes_a_source_quote(kind):
    ref = build(
        report(results=[]), [flag(check_kind="originality_reuse", ai_verdict_summary=kind)]
    ).references[0]
    assert ref.role == "finding_scope" and ref.quote == ""


@pytest.mark.parametrize(
    "kind,problem,passage",
    [
        ("internal_agreement", "agreement_unmatched_intent", False),
        ("internal_agreement", "agreement_cannot_determine", False),
        ("originality_reuse", "reuse_high_similarity_passage", True),
        ("originality_reuse", "historical_kind_not_recorded", False),
    ],
)
def test_unpaired_single_statement_or_own_passage_is_not_erased(kind, problem, passage):
    saved = flag(check_kind=kind, ai_verdict_summary=problem, is_passage_level=passage)
    ref = build(report(results=[]), [saved]).references[0]
    assert ref.role == "finding_excerpt" and ref.quote == saved.evidence_excerpt
    assert ref.anchor == saved.page_anchor and ref.source_state == "not_checked"


def test_library_match_is_bounded_text_without_foreign_original_page_binding():
    pair = Row(
        own_excerpt="Own stored passage.",
        matched_excerpt="Bounded Library passage.",
        matched_ref=42,
        matched_group_label="FOREIGN_IDENTITY",
        file_ref="FOREIGN_SOURCE_PATH",
    )
    refs = build(
        report(results=[]),
        [flag(check_kind="originality_reuse", passage_pair=pair)],
        audience="public",
    ).references
    own, matched = refs
    assert own.source_scope == "own_manuscript" and own.source_sha256 == "a" * 64
    assert matched.quote == pair.matched_excerpt and matched.anchor is None
    assert matched.source_scope == "bounded_comparison_excerpt"
    assert matched.source_state == "bounded_excerpt_only" and matched.source_sha256 is None
    assert "FOREIGN" not in refs[1].model_dump_json()


def test_public_projection_is_an_independent_allowlist_not_internal_serialization():
    refs = build(flags=[flag()], audience="public").references
    outputs = [public_evidence_reference(ref) for ref in refs]
    encoded = " ".join(item.model_dump_json() for item in outputs)
    for forbidden in (
        "PRIVATE_",
        "source_sha256",
        "owner_id",
        "manuscript_id",
        "check_result_id",
        "render_policy",
        "assessment_revision",
    ):
        assert forbidden not in encoded
    assert outputs[0].criterion_id == 21 and outputs[0].flag_id is None
    assert outputs[1].flag_id == 31
    with pytest.raises(ValueError):
        public_evidence_reference(build().references[0])


def test_tampered_reference_or_snapshot_cannot_keep_a_previous_identity():
    snapshot = build()
    values = snapshot.references[0].model_dump()
    values["quote"] = "Different quote"
    with pytest.raises(ValidationError):
        EvidenceSourceReference.model_validate(values)
    values = snapshot.model_dump()
    values["check_run_id"] += 1
    with pytest.raises(ValidationError):
        EvidenceReferenceSnapshot.model_validate(values)


@pytest.mark.parametrize("digest", ["b" * 64, None])
def test_valid_individual_references_cannot_mix_own_source_bindings(digest):
    from app.report.evidence_references import _reference_id

    snapshot = build(flags=[flag()])
    changed = snapshot.references[1].model_dump(exclude={"reference_id"})
    changed["source_sha256"] = digest
    if digest is None:
        changed["source_state"] = "source_identity_unverified"
    other = EvidenceSourceReference(reference_id=_reference_id(changed), **changed)
    values = snapshot.model_dump()
    values["references"] = (snapshot.references[0], other)
    with pytest.raises(ValidationError):
        EvidenceReferenceSnapshot.model_validate(values)


def test_unchecked_pydantic_copy_cannot_cross_public_or_snapshot_boundaries():
    snapshot = build()
    original = snapshot.references[0]
    forged = original.model_copy(
        update={
            "audience": "public",
            "render_policy": "public_annotation_free_page_v1",
            "quote": "Forged",
        }
    )
    with pytest.raises(ValidationError):
        public_evidence_reference(forged)
    values = snapshot.model_dump()
    values["references"] = (original.model_copy(update={"quote": "Forged"}),)
    with pytest.raises(ValidationError):
        EvidenceReferenceSnapshot.model_validate(values)


@pytest.mark.parametrize("kind", [None, [], {}, 1, True, "unknown_recorded_kind"])
def test_shared_summary_policy_does_not_crash_on_unrecognized_recorded_json(kind):
    from app.findings.policy import is_reuse_summary

    assert is_reuse_summary(kind) is False
