"""Saved indexing facts cannot turn invalid or legacy metadata into completeness."""

import pytest
from pydantic import ValidationError

from app.library.passage_indexing import (
    PassageIndexingSnapshot,
    indexing_scope_sentences,
    project_indexing_summary,
    project_passage_indexing,
)


def _snapshot(**changes):
    return dict(
        version="v1",
        source_sha256="a" * 64,
        model_id="archived-model",
        limit=3,
        inventory_state="measured",
        passages_found=7,
        passages_indexed=3,
        **changes,
    )


def test_bound_source_and_archived_model_are_required():
    value = _snapshot()
    assert project_passage_indexing(value, "a" * 64, "archived-model").state == "partial"
    for source, model in [
        (None, "archived-model"),
        ("b" * 64, "archived-model"),
        ("a" * 64, "other-model"),
        ("a" * 64, None),
    ]:
        assert project_passage_indexing(value, source, model).state == "not_recorded"
    assert project_passage_indexing(None, "a" * 64, "archived-model").state == "not_recorded"


@pytest.mark.parametrize(
    "change",
    [
        {"passages_found": True},
        {"passages_indexed": 8},
        {"passages_found": None},
        {"version": "v0"},
    ],
)
def test_invalid_snapshot_never_projects_complete(change):
    value = _snapshot() | change
    with pytest.raises(ValidationError):
        PassageIndexingSnapshot.model_validate(value)
    assert project_passage_indexing(value, "a" * 64, "archived-model").state == "not_recorded"


def test_measured_zero_and_unavailable_remain_distinct_and_safe():
    zero = _snapshot() | {"passages_found": 0, "passages_indexed": 0}
    unavailable = zero | {"inventory_state": "unavailable", "passages_found": None}
    assert project_passage_indexing(zero, "a" * 64, "archived-model").model_dump() == {
        "state": "complete",
        "passages_found": 0,
        "passages_indexed": 0,
    }
    out = project_passage_indexing(unavailable, "a" * 64, "archived-model").model_dump()
    assert out == {"state": "unavailable", "passages_found": None, "passages_indexed": None}
    assert "source_sha256" not in out and "model_id" not in out


def test_scope_summary_rejects_missing_and_coerced_flags():
    assert project_indexing_summary(None) is None
    assert project_indexing_summary({"version": "v1", "partial": False}) is None
    assert (
        project_indexing_summary(
            dict(version="v1", partial=1, unavailable=False, not_recorded=False)
        )
        is None
    )
    summary = project_indexing_summary(
        dict(
            version="v1", partial=True, unavailable=False, not_recorded=True, private_title="secret"
        )
    )
    assert summary is not None
    assert "private_title" not in summary.model_dump()
    text = " ".join(indexing_scope_sentences(summary))
    assert "when this comparison began" in text and "does not establish originality" in text
