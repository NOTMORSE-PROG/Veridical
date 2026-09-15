"""BUG-219: `llm_execution_mode` derivation is pure (event_type + payload in,
one of "fake"/"real"/"unknown"/None out) and needs no database at all —
unlike every other audit test in this suite (all `_live.py`, all gated on
DATABASE_URL). Keeping this fast and DB-free means the one thing the bug was
about (never silently promoting an unmarked row to "real") is checked on
every run, Docker or not. Deliberately reuses BUG-049's own `fake`/`real`/
`unknown` vocabulary (`app.models.enums.LLMMode`) rather than a second one
for the same fact.
"""

from datetime import UTC, datetime

from app.audit.service import _llm_execution_mode, _summary
from app.models.audit import AuditLog

# `created_at` is DB `server_default=func.now()` (app/models/base.py) — it
# never populates on a bare, unflushed Python object, but AuditLogSummary
# requires it. Any fixed instant works; these tests don't assert on time.
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_fake_llm_true_is_fake_mode():
    assert _llm_execution_mode("llm_call", {"fake_llm": True}) == "fake"


def test_fake_llm_false_is_real_mode():
    assert _llm_execution_mode("llm_call", {"fake_llm": False}) == "real"


def test_fake_llm_missing_is_unknown_not_guessed_real():
    """A row written before this field existed (or by any future code path
    that forgets to set it) must read as honestly unknown, never default
    into the "a real model produced this" claim BUG-219 was filed over."""
    assert _llm_execution_mode("llm_call", {}) == "unknown"
    assert _llm_execution_mode("llm_call", {"model": "gemini-1.5-flash"}) == "unknown"


def test_applies_to_every_llm_prefixed_event_type():
    for event_type in ("llm_call", "llm_cache_hit", "llm_call_failed", "llm_model_exhausted"):
        assert _llm_execution_mode(event_type, {"fake_llm": True}) == "fake"


def test_non_llm_event_types_carry_no_execution_mode_even_if_the_key_is_present():
    """The concept doesn't apply to an instructor action or a routing
    decision — reporting a mode there would be inventing a fact, not
    reading one. Deliberately includes a stray `fake_llm` key to prove the
    event_type gate, not payload absence, is what makes this None."""
    assert _llm_execution_mode("escalation_resolved", {"fake_llm": True}) is None
    assert _llm_execution_mode("criterion_routing", {}) is None
    assert _llm_execution_mode("flag_overridden", {"fake_llm": False}) is None


def test_summary_wires_the_derived_mode_through():
    row = AuditLog(id=1, event_type="llm_call", payload={"fake_llm": True}, created_at=_NOW)
    assert _summary(row, group_label=None).llm_execution_mode == "fake"


def test_a_mixed_batch_of_rows_never_cross_contaminates_each_others_mode():
    """Regression test's own "grouped mixed batch" case: summarizing a
    fake-mode row, a real-mode row, and a pre-fix unmarked row side by side
    (the exact shape one page of the audit list can return) must leave each
    row's own mode independent of the others, in any order."""
    fake_row = AuditLog(id=1, event_type="llm_call", payload={"fake_llm": True}, created_at=_NOW)
    real_row = AuditLog(id=2, event_type="llm_call", payload={"fake_llm": False}, created_at=_NOW)
    unknown_row = AuditLog(id=3, event_type="llm_call", payload={}, created_at=_NOW)
    non_llm_row = AuditLog(id=4, event_type="report_decided", payload={}, created_at=_NOW)

    summaries = [
        _summary(r, group_label=None) for r in (fake_row, real_row, unknown_row, non_llm_row)
    ]

    assert [s.llm_execution_mode for s in summaries] == ["fake", "real", "unknown", None]
