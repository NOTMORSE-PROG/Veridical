"""Operator logs name where a background job failed without repeating the
exception message, which can quote manuscript text."""

import pytest

from app.errors import failure_summary
from app.storage.access import lock_source

SECRET = "Chapter 3 says the clinic lost 40 bookings"


class FailingSession:
    async def refresh(self, value, **kwargs):
        raise RuntimeError(SECRET)


@pytest.mark.asyncio
async def test_the_summary_names_the_class_and_our_own_frame_only():
    with pytest.raises(RuntimeError) as caught:
        await lock_source(FailingSession(), object())
    summary = failure_summary(caught.value)
    assert summary.startswith("RuntimeError at app/storage/access.py:")
    assert summary.endswith("(lock_source)")
    assert SECRET not in summary


def test_a_failure_outside_our_code_gives_the_class_alone():
    try:
        raise ValueError(SECRET)
    except ValueError as exc:
        assert failure_summary(exc) == "ValueError"
