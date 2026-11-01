"""A pair's verdict must not depend on which other pairs share its model call:
batches are built in an order fixed by each pair's own text, and a pair the
batched pass would raise as a finding is judged again alone, and that answer
counts."""

import asyncio

from app.checks.agreement import pair
from app.checks.agreement.cues import load_cues
from app.checks.agreement.extract import Statement
from app.config import Settings

CUES = load_cues()


def _candidate(n):
    intent = Statement("intent", f"To build module {n} for the clinic.", "p. 1", 1, None, "to")
    outcome = Statement("outcome", f"Module {n} was tested with staff.", "p. 9", 9, None, "was")
    return pair.Candidate(intent=intent, outcome=outcome, similarity=0.9)


def _verdict(index, verdict):
    return pair.PairVerdict(index=index, verdict=verdict, reasoning="Because of the stated result.")


class Judge:
    """Answers each pair by its module number: batched and lone answers can differ."""

    def __init__(self, batched, alone=None):
        self.batched, self.alone, self.calls = batched, alone or {}, []

    async def __call__(self, llm, pairs, *, check_run_id, reading=0):
        numbers = [int(c.intent.text.split()[3]) for c in pairs]
        self.calls.append(numbers)
        table = self.alone if len(pairs) == 1 and self.alone else self.batched
        return {
            i: _verdict(i, table.get(n, self.batched.get(n, "consistent")))
            for i, n in enumerate(numbers)
            if table.get(n, self.batched.get(n)) != "unanswered"
        }


def _judge(monkeypatch, candidates, judge, **settings):
    monkeypatch.setattr(pair, "_judge_batch", judge)
    config = Settings(_env_file=None, agreement_pairing_max_pairs_per_call=3, **settings)
    judged, _ = asyncio.run(
        pair.judge_pairs(None, candidates, check_run_id=None, settings=config, cues=CUES)
    )
    return judged


def test_the_same_pairs_share_a_call_whatever_order_they_arrive_in(monkeypatch):
    candidates = [_candidate(n) for n in range(1, 8)]
    first, second = Judge({}), Judge({})
    _judge(monkeypatch, candidates, first)
    _judge(monkeypatch, list(reversed(candidates)), second)
    assert sorted(map(sorted, first.calls)) == sorted(map(sorted, second.calls))


def test_results_keep_the_order_of_the_candidates(monkeypatch):
    candidates = [_candidate(n) for n in range(1, 8)]
    judged = _judge(monkeypatch, candidates, Judge({}))
    assert [j.candidate for j in judged] == candidates


def test_a_pair_the_batch_would_flag_is_judged_alone_and_that_answer_counts(monkeypatch):
    candidates = [_candidate(n) for n in range(1, 5)]
    judge = Judge({2: "partial"}, alone={2: "consistent"})
    judged = _judge(monkeypatch, candidates, judge)
    assert [2] in judge.calls
    second = next(j for j in judged if j.candidate is candidates[1])
    assert second.final == "consistent" and second.batched_verdict == "partial"


def test_a_lone_call_with_no_answer_does_not_let_the_batched_flag_stand(monkeypatch):
    candidates = [_candidate(n) for n in range(1, 5)]
    judge = Judge({3: "partial"}, alone={3: "unanswered"})
    judged = _judge(monkeypatch, candidates, judge)
    third = next(j for j in judged if j.candidate is candidates[2])
    assert third.final == "cannot_determine" and third.downgrade == "lone_reading_not_answered"


def test_a_pair_already_alone_in_its_call_is_not_asked_twice(monkeypatch):
    judge = Judge({1: "partial"})
    judged = _judge(monkeypatch, [_candidate(1)], judge)
    assert judge.calls == [[1]] and judged[0].final == "partial"


def test_both_rules_can_be_turned_off(monkeypatch):
    candidates = [_candidate(n) for n in range(1, 5)]
    judge = Judge({2: "partial"}, alone={2: "consistent"})
    judged = _judge(
        monkeypatch,
        candidates,
        judge,
        agreement_stable_batch_order=False,
        agreement_flagged_pairs_judged_alone=False,
    )
    assert judge.calls[0] == [1, 2, 3] and [2] not in judge.calls
    assert next(j for j in judged if j.candidate is candidates[1]).final == "partial"
