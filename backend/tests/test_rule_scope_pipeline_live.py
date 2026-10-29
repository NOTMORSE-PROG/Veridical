"""F3/F8: complete-claim routing and immutable-prefix resume on real PostgreSQL."""

import copy
import os

import pytest
from sqlalchemy import select

from app.checks.router import RouteDecision, apply_routing
from app.errors import ApiDownError
from app.llm.fake import FakeLLMClient
from app.models.audit import AuditLog
from app.models.enums import CheckKind, CheckRunStatus, CriterionType, ResultOutcome
from app.models.rubric import Criterion
from app.models.run import CheckResult, CheckRun
from app.pipeline.machine import run_check_run
from tests.test_pipeline_machine_live import _clean_tables as _clean_tables
from tests.test_pipeline_machine_live import _seed
from tests.test_pipeline_machine_live import pipeline_scratch_url as pipeline_scratch_url
from tests.test_pipeline_machine_live import session_factory as session_factory

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="owned PostgreSQL required"
)
# Still compound after BUG-498: the word-count rule covers "an abstract of at
# most 250 words" completely, but no rule covers the added tense qualifier.
COMPOUND = "Manuscript contains an abstract of at most 250 words in the past tense"
PAGES = "The PDF manuscript contains at least one page"


class BoundaryClient:
    def __init__(self):
        self.semantic_requests = []

    async def complete(self, prompt_type, prompt, **kwargs):
        if prompt_type in {"semantic_grading", "semantic_support"}:
            self.semantic_requests.append(prompt_type)
            raise ApiDownError("Controlled unavailable adapter; no provider was called.")
        return await FakeLLMClient().complete(prompt_type, prompt, **kwargs)


async def rows(session, run_id):
    return list(
        await session.scalars(
            select(CheckResult)
            .where(CheckResult.check_run_id == run_id, CheckResult.criterion_id.isnot(None))
            .order_by(CheckResult.criterion_id)
        )
    )


def saved(row):
    return (row.id, row.criterion_id, row.kind, row.outcome, row.score, copy.deepcopy(row.detail))


async def test_fresh_compound_requirement_is_not_settled_by_a_heading(
    session_factory, tmp_path, monkeypatch
):
    run_id, ids, settings = await _seed(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        first, second = await session.get(Criterion, ids[0]), await session.get(Criterion, ids[1])
        first.text = COMPOUND
        second.text, second.type = PAGES, CriterionType.structural
        await session.commit()
        client = BoundaryClient()
        run = await session.get(CheckRun, run_id)
        await run_check_run(session, run, settings, client)
        assert run.status == CheckRunStatus.done
        results = await rows(session, run_id)
        assert len(results) == 3
        compound = next(row for row in results if row.criterion_id == ids[0])
        assert compound.kind == CheckKind.semantic and compound.outcome == ResultOutcome.api_down
        assert compound.score is None and compound.detail["basis"] == "not-graded"
        page = next(row for row in results if row.criterion_id == ids[1])
        assert page.kind == CheckKind.structural and page.outcome == ResultOutcome.passed
        assert page.detail["actual_pages"] == 3
        assert len(client.semantic_requests) == 1
        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.check_run_id == run_id, AuditLog.event_type == "criterion_routing"
            )
        )
        assert audit.payload["decisions"][0]["kind"] == "semantic"
        assert audit.payload["decisions"][1]["rule_id"] == "page_limit"
        assert first.text == COMPOUND and second.text == PAGES


async def test_clear_page_requirement_has_no_semantic_grading_requests(
    session_factory, tmp_path, monkeypatch
):
    run_id, ids, settings = await _seed(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        for index, criterion_id in enumerate(ids):
            criterion = await session.get(Criterion, criterion_id)
            criterion.text = PAGES if index == 0 else "Bring printed copies to the defense."
            criterion.type = (
                CriterionType.structural if index == 0 else CriterionType.not_assessable
            )
        await session.commit()
        client = BoundaryClient()
        run = await session.get(CheckRun, run_id)
        await run_check_run(session, run, settings, client)
        results = await rows(session, run_id)
        assert run.status == CheckRunStatus.done and len(results) == 3
        assert results[0].kind == CheckKind.structural and results[0].score == 100
        assert all(row.outcome == ResultOutcome.not_applicable for row in results[1:])
        assert client.semantic_requests == []


async def test_cross_kind_saved_prefix_is_preserved_without_second_assessment(
    session_factory, tmp_path, monkeypatch
):
    run_id, ids, settings = await _seed(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        first, second = await session.get(Criterion, ids[0]), await session.get(Criterion, ids[1])
        first.text, second.text, second.type = COMPOUND, PAGES, CriterionType.structural
        await session.commit()
        await apply_routing(
            session,
            run_id,
            [
                RouteDecision(
                    ids[0], CheckKind.structural, "required_section_present", False, None
                ),
                RouteDecision(ids[1], CheckKind.semantic, None, True, "Recorded earlier routing"),
                RouteDecision(ids[2], CheckKind.semantic, None, False, None),
            ],
        )
        session.add_all(
            [
                CheckResult(
                    check_run_id=run_id,
                    criterion_id=ids[0],
                    kind=CheckKind.structural,
                    outcome=ResultOutcome.passed,
                    score=100,
                    detail={
                        "rule_id": "required_section_present",
                        "target": "abstract",
                        "matched_title": "ABSTRACT",
                    },
                ),
                CheckResult(
                    check_run_id=run_id,
                    criterion_id=ids[1],
                    kind=CheckKind.semantic,
                    outcome=ResultOutcome.passed,
                    score=50,
                    detail={"basis": "llm", "reasoning": "Synthetic saved prefix"},
                ),
            ]
        )
        run = await session.get(CheckRun, run_id)
        run.status = CheckRunStatus.structural
        await session.commit()
        before = [saved(row) for row in await rows(session, run_id)]
        client = BoundaryClient()
        await run_check_run(session, run, settings, client)
        result = await rows(session, run_id)
        assert run.status == CheckRunStatus.done and len(result) == 3
        assert [saved(row) for row in result[:2]] == before
        assert result[2].outcome == ResultOutcome.api_down and result[2].score is None
        assert len(client.semantic_requests) == 1


async def test_old_unexecuted_semantic_plan_cannot_disappear_after_page_recognition_improves(
    session_factory, tmp_path, monkeypatch
):
    run_id, ids, settings = await _seed(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        for index, criterion_id in enumerate(ids):
            criterion = await session.get(Criterion, criterion_id)
            criterion.text = PAGES if index == 0 else "Bring printed copies to the defense."
            criterion.type = (
                CriterionType.structural if index == 0 else CriterionType.not_assessable
            )
        await session.commit()
        await apply_routing(
            session,
            run_id,
            [
                RouteDecision(
                    ids[0], CheckKind.semantic, None, True, "Recorded earlier page routing"
                ),
                *[
                    RouteDecision(
                        cid, CheckKind.semantic, None, False, "Outside manuscript", unroutable=True
                    )
                    for cid in ids[1:]
                ],
            ],
        )
        run = await session.get(CheckRun, run_id)
        run.status = CheckRunStatus.semantic
        await session.commit()
        client = BoundaryClient()
        await run_check_run(session, run, settings, client)
        result = await rows(session, run_id)
        assert run.status == CheckRunStatus.done and len(result) == 3
        page = next(row for row in result if row.criterion_id == ids[0])
        assert page.kind == CheckKind.semantic and page.outcome == ResultOutcome.api_down
        assert page.score is None and len(client.semantic_requests) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "malformed",
        "conflicting",
        "missing_terminal",
        "wrong_terminal_kind",
        "wrong_terminal_outcome",
        "wrong_terminal_score",
    ],
)
async def test_unavailable_saved_plan_fails_without_grading_or_changing_prefix(
    session_factory,
    tmp_path,
    monkeypatch,
    fault,
):
    run_id, ids, settings = await _seed(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        prefix = CheckResult(
            check_run_id=run_id,
            criterion_id=ids[0],
            kind=CheckKind.structural,
            outcome=ResultOutcome.passed,
            score=100,
            detail={
                "rule_id": "required_section_present",
                "target": "abstract",
                "matched_title": "ABSTRACT",
            },
        )
        session.add(prefix)
        if fault == "wrong_terminal_kind":
            prefix.outcome, prefix.score = ResultOutcome.not_applicable, None
        elif fault == "wrong_terminal_outcome":
            prefix.kind = CheckKind.semantic
        elif fault == "wrong_terminal_score":
            prefix.kind, prefix.outcome = CheckKind.semantic, ResultOutcome.not_applicable
        run = await session.get(CheckRun, run_id)
        run.status = CheckRunStatus.semantic
        if fault != "missing":
            decisions = [
                {
                    "criterion_id": cid,
                    "kind": "semantic",
                    "rule_id": None,
                    "degraded": False,
                    "note": None,
                    "unroutable": (fault == "missing_terminal" and cid == ids[2])
                    or (fault.startswith("wrong_terminal") and cid == ids[0]),
                }
                for cid in ids
            ]
            payload = {"decisions": decisions} if fault != "malformed" else {"decisions": []}
            session.add(
                AuditLog(check_run_id=run_id, event_type="criterion_routing", payload=payload)
            )
            if fault == "conflicting":
                altered = copy.deepcopy(payload)
                altered["decisions"][0]["note"] = "Conflicting plan"
                session.add(
                    AuditLog(check_run_id=run_id, event_type="criterion_routing", payload=altered)
                )
        await session.commit()
        before = saved(prefix)
        client = BoundaryClient()
        await run_check_run(session, run, settings, client)
        result = await rows(session, run_id)
        assert run.status == CheckRunStatus.failed
        assert run.stage_status["failed"]["code"] == "evaluation_plan_unavailable"
        assert len(result) == 1 and saved(result[0]) == before
        assert client.semantic_requests == []
