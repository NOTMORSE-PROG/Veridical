"""A finding lists every Library record it matched, in the saved order."""

import pytest

from app.report.service import matched_refs


@pytest.mark.parametrize(
    "detail, expected",
    [
        ({"matched_manuscript_id": 10, "matched_manuscript_ids": [10, 12, 14]}, [10, 12, 14]),
        ({"matched_manuscript_id": 3}, [3]),
        ({"matched_manuscript_ids": [12, 12, -1, 0, True, "7", 9]}, [12, 9]),
        ({"matched_manuscript_id": 10, "matched_manuscript_ids": "10,12"}, [10]),
        ({}, []),
        (None, []),
    ],
)
def test_every_matched_record_is_listed_once_in_order(detail, expected):
    assert matched_refs(detail) == expected
