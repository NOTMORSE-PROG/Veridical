"""Optional F7 record identity does not collapse scopes or classify other checks."""

import pytest

from app.report.export import _is_optional_comparison_lead, _review_record_identities
from app.report.schemas import FlagSummaryOut


def _flag(record_id, *, kind="originality_reuse", affects=False):
    return FlagSummaryOut(
        id=record_id,
        check_kind=kind,
        affects_readiness=affects,
        severity="high",
        criterion_text=None,
        evidence_excerpt="Saved passage",
        page_anchor="p. 1",
        overridden=False,
    )


def test_all_records_keep_stable_identity_across_payload_order_and_mixed_checks():
    leads = [_flag(record_id) for record_id in range(39, 70)]
    other = _flag(8, kind="internal_agreement")
    identities = _review_record_identities([*reversed(leads), other])
    assert len(identities) == 32
    assert identities[41] == "Comparison lead 3 of 31. Record #41"
    assert identities[69] == "Comparison lead 31 of 31. Record #69"
    assert identities[8] == "Finding 1"
    assert identities == _review_record_identities([other, *leads])


@pytest.mark.parametrize(
    "kind,affects,expected",
    [
        ("originality_reuse", False, True),
        ("originality_reuse", True, False),
        ("internal_agreement", False, False),
        ("citation_integrity", False, False),
    ],
)
def test_exact_optional_predicate(kind, affects, expected):
    assert _is_optional_comparison_lead(_flag(41, kind=kind, affects=affects)) is expected
