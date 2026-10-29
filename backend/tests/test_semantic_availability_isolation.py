"""F3: service interruption preserves proposals without bypassing acceptance gates."""

import json

import pytest

from app.checks.consistency import _vote_batch
from app.checks.semantic import _grade_batch
from app.config import Settings
from app.errors import ApiDownError, QuotaExhaustedError
from app.models.enums import ResultOutcome
from tests.semantic_support_fixture import supported_response
from tests.test_checks_semantic import FakeSession
from tests.test_semantic_partial_recovery import Scripted, candidate, fixture


class Client(Scripted):
    def __init__(self, responses, *, support_down=False):
        super().__init__(responses)
        self.support_down = support_down
        self.support_tasks = []

    async def complete(self, prompt_type, prompt, *, prompt_version, **context):
        if prompt_type == "semantic_support":
            self.support_tasks.append(json.loads(prompt.split("--- TASK JSON ---\n", 1)[1]))
            if self.support_down:
                raise ApiDownError("Controlled support timeout")
            return supported_response(prompt)
        return await super().complete(prompt_type, prompt, prompt_version=prompt_version, **context)


def response(*, invalid=(), uncertain=(), disagree=()):
    return {
        "verdicts": [
            candidate(
                i,
                quotes=[] if i in invalid else None,
                verdict="uncertain" if i in uncertain else "fail" if i in disagree else "pass",
            )
            for i in range(3)
        ]
    }


async def run(client, runner=_vote_batch):
    batch, criteria = fixture()
    session = FakeSession()
    rows = await runner(session, 1, batch, criteria[:3], client, Settings(), "page")
    assert session.added == rows and len(rows) == 3
    return rows


def assert_withheld(row, phase):
    assert row.outcome == ResultOutcome.api_down
    assert row.score is None
    assert "score" not in row.detail
    assert "verdict" not in row.detail and "level" not in row.detail
    assert row.detail["failure_phase"] == phase


@pytest.mark.parametrize("failure", ["repair", "tie"])
async def test_middle_failure_persists_supported_siblings_before_and_after(failure):
    client = Client(
        [
            response(),
            response(invalid=(1,)) if failure == "repair" else response(disagree=(1,)),
            ApiDownError("Controlled interruption"),
        ]
    )
    rows = await run(client)
    assert [r.outcome for r in rows] == [
        ResultOutcome.passed,
        ResultOutcome.api_down,
        ResultOutcome.passed,
    ]
    assert_withheld(rows[1], "pass_2:criterion_repair" if failure == "repair" else "tie_break")
    assert rows[1].detail["grading_pass_proposals"][0]["evidence"] == [
        {"quote": "Unique saved source statement 1.", "anchor": "page 7"}
    ]
    assert rows[1].detail["votes"] == (
        ["pass", None] if failure == "repair" else ["pass", "fail", None]
    )
    assert len(client.calls) == 3 and len(client.support_tasks) == 1
    assert len(client.support_tasks[0]["items"]) == 2
    assert {q for item in client.support_tasks[0]["items"] for q in item["candidate_quotes"]} == {
        "Unique saved source statement 0.",
        "Unique saved source statement 2.",
    }


async def test_second_pass_outage_retains_first_pass_without_fabricating_agreement():
    client = Client([response(), ApiDownError("Controlled second pass timeout")])
    rows = await run(client)
    for i, row in enumerate(rows):
        assert_withheld(row, "pass_2:batch")
        assert row.detail["votes"] == ["pass", None]
        proposal = row.detail["grading_pass_proposals"][0]
        assert proposal["proposed_verdict"] == "pass"
        assert proposal["evidence"] == [
            {"quote": f"Unique saved source statement {i}.", "anchor": "page 7"}
        ]
        assert proposal["failure_phase"] is None
    assert len(client.calls) == 2 and client.support_tasks == []


async def test_first_pass_outage_retains_existing_owned_pipeline_floor():
    client = Client([ApiDownError("Controlled first pass timeout")])
    batch, criteria = fixture()
    session = FakeSession()
    with pytest.raises(ApiDownError):
        await _vote_batch(session, 1, batch, criteria[:3], client, Settings(), "page")
    assert session.added == []
    assert len(client.calls) == 1 and client.support_tasks == []


async def test_all_repairs_unavailable_explicitly_skips_second_pass():
    client = Client([response(invalid=(0, 1, 2)), ApiDownError("Controlled repair timeout")])
    rows = await run(client)
    for row in rows:
        first, second = row.detail["grading_pass_proposals"]
        assert second["request_attempted"] is False
        assert second["failure_phase"] == "pass_2:not_attempted"
    assert len(client.calls) == 2 and client.support_tasks == []


@pytest.mark.parametrize("runner", [_grade_batch, _vote_batch])
async def test_shared_support_failure_downgrades_only_submitted_candidates(runner):
    client = Client(
        [response(uncertain=(1,))] * (2 if runner == _vote_batch else 1), support_down=True
    )
    rows = await run(client, runner)
    assert [r.outcome for r in rows] == [
        ResultOutcome.api_down,
        ResultOutcome.escalated,
        ResultOutcome.api_down,
    ]
    for row in (rows[0], rows[2]):
        assert_withheld(row, "semantic_support")
        assert row.detail["proposed_verdict"] == "pass"
        assert row.detail["evidence"]
        assert row.detail["support_withheld"] is True
    assert "failure_phase" not in rows[1].detail
    assert len(client.support_tasks) == 1 and len(client.support_tasks[0]["items"]) == 2


@pytest.mark.parametrize("runner", [_grade_batch, _vote_batch])
async def test_later_invalid_items_do_not_multiply_failed_recovery_requests(runner):
    replies = [response(invalid=(0, 1)), ApiDownError("Controlled repair timeout")]
    if runner == _vote_batch:
        replies.append(response())
    client = Client(replies)
    rows = await run(client, runner)
    assert [r.outcome for r in rows] == [
        ResultOutcome.api_down,
        ResultOutcome.api_down,
        ResultOutcome.passed,
    ]
    assert len(client.calls) == (3 if runner == _vote_batch else 2)
    assert len(client.support_tasks) == 1
    if runner == _vote_batch:
        assert rows[0].detail["grading_pass_proposals"][0]["request_attempted"] is True
        assert rows[1].detail["grading_pass_proposals"][0]["request_attempted"] is False
    else:
        assert rows[0].detail["request_attempted"] is True
        assert rows[1].detail["request_attempted"] is False


@pytest.mark.parametrize("phase", ["second_pass", "tie_break"])
async def test_quota_still_aborts_before_any_current_cohort_persistence(phase):
    client = Client(
        [response()]
        + ([response(disagree=(1,))] if phase == "tie_break" else [])
        + [QuotaExhaustedError("Controlled quota")]
    )
    batch, criteria = fixture()
    session = FakeSession()
    with pytest.raises(QuotaExhaustedError):
        await _vote_batch(session, 1, batch, criteria[:3], client, Settings(), "page")
    assert session.added == [] and client.support_tasks == []
