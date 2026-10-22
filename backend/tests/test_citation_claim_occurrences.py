"""F5.4: every captured claim/reference occurrence has explicit coverage."""

import re
import socket
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.checks.citations import verify
from app.checks.citations.support import (
    ClaimSupportInput,
    ClaimSupportPairResult,
    ClaimSupportResult,
    run_claim_support_check,
)
from app.config import Settings
from app.errors import ApiDownError, QuotaExhaustedError
from app.external.schemas import VerificationResult
from app.ingest.patterns import load_patterns
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock
from app.models.enums import ResultOutcome
from app.models.run import Flag
from tests.test_stabilization_integrity_controls import MemorySession, citation

FIRST = "The study reports reduced execution time (Reyes, 2023)."
SECOND = "The study proves that all systems are perfectly secure (Reyes, 2023)."
RAW = "Reyes, J. (2023). A Study. Journal of Examples."
ABSTRACT = "We measured reduced execution time in controlled tests. Security was not studied."


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def guarded(original):
        def connect(sock, address):
            # Windows asyncio creates its own loopback socket pair during setup.
            if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
                return original(sock, address)
            raise AssertionError("Occurrence controls must remain keyless and offline")

        return connect

    monkeypatch.setattr(socket.socket, "connect", guarded(original_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guarded(original_connect_ex))


class ScriptedClaims:
    def __init__(self):
        self.prompts = []

    async def complete(self, prompt_type, prompt, **_context):
        assert prompt_type == "claim_support"
        self.prompts.append(prompt)
        return {
            "verdicts": [
                {
                    "index": int(index),
                    "verdict": "possibly_unsupported"
                    if "perfectly secure" in text
                    else "supported",
                    "reasoning": "Scripted test judgment; no live model assessment.",
                    "abstract_excerpt": "Security was not studied."
                    if "perfectly secure" in text
                    else None,
                }
                for index, text in re.findall(r'^\[(\d+)\] CLAIM: "(.*)"$', prompt, re.MULTILINE)
            ]
        }


def source(claims, *, paragraph=False, references=(RAW,)):
    body = [
        TextBlock(
            text=claim,
            page=None if paragraph else index + 1,
            paragraph=index if paragraph else None,
            max_font_size=11,
            bold_ratio=0,
        )
        for index, claim in enumerate(claims)
    ]
    reference_location = {"paragraph": len(body)} if paragraph else {"page": len(body) + 1}
    blocks = [
        *body,
        TextBlock(text="References", max_font_size=14, bold_ratio=1, **reference_location),
        *[
            TextBlock(text=raw, max_font_size=11, bold_ratio=0, **reference_location)
            for raw in references
        ],
    ]
    return ExtractionResult(
        page_count=0 if paragraph else len(body) + 1,
        anchor_kind="paragraph" if paragraph else "page",
        image_only=False,
        text_chars=sum(len(block.text) for block in blocks),
        blocks=blocks,
        images=[],
        section_tree=SectionTree(
            source="heuristics",
            nodes=[SectionNode(title="References", level=1, **reference_location)],
        ),
    )


async def run(
    monkeypatch, claims, *, settings=None, paragraph=False, abstract=ABSTRACT, llm=True, refs=None
):
    settings = settings or Settings(_env_file=None)
    refs = refs or [citation(RAW)]
    extraction = source(claims, paragraph=paragraph, references=[ref.raw_text for ref in refs])
    before = extraction.model_dump_json()
    provider = AsyncMock(
        return_value=verify.CitationVerdict(
            verify.VerdictKind.existence_confirmed,
            VerificationResult(found=True, provider="crossref", title="A Study", abstract=abstract),
        )
    )
    monkeypatch.setattr(verify, "verify_citation", provider)
    monkeypatch.setattr(verify, "write_audit_event", AsyncMock())
    session, model = MemorySession(), ScriptedClaims() if llm else None
    result = await verify.run_citation_integrity_check(
        session, None, 1, refs, extraction, load_patterns(), settings, model
    )
    assert extraction.model_dump_json() == before
    assert provider.await_count == len(refs)
    ledger = result.detail["claim_support_occurrences"]
    assert [item["occurrence_index"] for item in ledger] == list(range(len(ledger)))
    assert len(ledger) == result.detail["n_claim_support_candidate_pairs_total"]
    assessed = sum(item["status"] == "assessed" for item in ledger)
    assert assessed == result.detail["n_claim_support_checked"]
    assert len(ledger) == assessed + result.detail["n_claim_support_pairs_unassessed"]
    assert sum(item["eligible"] for item in ledger) == result.detail["n_claim_support_pairs_total"]
    assert all(item["unassessed_reason"] for item in ledger if item["status"] == "unassessed")
    return result, [row for row in session.rows if isinstance(row, Flag)], model


@pytest.mark.parametrize("paragraph", [False, True])
async def test_later_claim_for_one_reference_is_assessed_at_its_actual_source(
    monkeypatch, paragraph
):
    result, flags, model = await run(monkeypatch, [FIRST, SECOND], paragraph=paragraph)
    assert result.detail["n_linked"] == 2
    assert result.detail["n_claim_support_pairs_total"] == 2
    assert result.detail["n_claim_support_checked"] == 2
    assert FIRST in model.prompts[0] and SECOND in model.prompts[0]
    assert len(flags) == 1 and flags[0].evidence_excerpt == SECOND
    assert flags[0].page_anchor == ("¶1" if paragraph else "p. 2")
    ledger = result.detail["claim_support_occurrences"]
    assert [item["model_verdict"] for item in ledger] == ["supported", "possibly_unsupported"]
    assert [item["eligible_pair_index"] for item in ledger] == [0, 1]
    assert [item["source_anchor"] for item in ledger] == (
        ["¶0", "¶1"] if paragraph else ["p. 1", "p. 2"]
    )
    assert all(
        item["source_offset"] is None and item["source_block_index"] is None for item in ledger
    )
    assert flags[0].detail["reference_order_index"] == 0
    assert flags[0].detail["claim_support_pair_index"] == 1


async def test_repeated_supported_claim_keeps_two_occurrences(monkeypatch):
    result, flags, _ = await run(monkeypatch, [FIRST, FIRST])
    assert result.detail["n_claim_support_pairs_total"] == 2
    assert result.detail["n_claim_support_checked"] == 2
    assert result.outcome == ResultOutcome.passed
    assert flags == []
    assert [item["source_anchor"] for item in result.detail["claim_support_occurrences"]] == [
        "p. 1",
        "p. 2",
    ]


async def test_limit_discloses_omitted_second_claim_without_inventing_a_finding(monkeypatch):
    result, flags, model = await run(
        monkeypatch,
        [FIRST, SECOND],
        settings=Settings(_env_file=None, claim_support_max_pairs_per_run=1),
    )
    assert result.detail["n_claim_support_pairs_total"] == 2
    assert result.detail["n_claim_support_checked"] == 1
    assert result.detail["n_claim_support_skipped_limit"] == 1
    assert result.detail["n_claim_support_unassessed"] == 1  # distinct sources, not occurrences
    assert result.detail["coverage_reason"] == "claim_support_unavailable"
    assert result.outcome == ResultOutcome.escalated
    assert flags == []
    assert len(model.prompts) == 1 and SECOND not in model.prompts[0]
    ledger = result.detail["claim_support_occurrences"]
    assert ledger[0]["status"] == "assessed" and ledger[1]["unassessed_reason"] == "limit"
    thresholds = verify.write_audit_event.await_args.kwargs["payload"]["thresholds"]
    assert thresholds["claim_support_max_pairs_per_run"] == 1
    assert thresholds["claim_support_max_pairs_per_call"] == 20


@pytest.mark.parametrize("missing", ["abstract", "llm"])
async def test_missing_input_counts_all_occurrences_but_only_one_source(monkeypatch, missing):
    result, flags, model = await run(
        monkeypatch,
        [FIRST, SECOND],
        abstract=None if missing == "abstract" else ABSTRACT,
        llm=missing != "llm",
    )
    assert result.detail["n_claim_support_checked"] == 0
    assert result.detail["n_claim_support_pairs_unassessed"] == 2
    assert result.detail["n_claim_support_unassessed"] == 1
    assert result.outcome == ResultOutcome.escalated and flags == []
    assert model is None or model.prompts == []


async def test_cropped_author_year_claim_is_withheld_without_hiding_uncropped_repeat(monkeypatch):
    long_claim = "The study reports " + "reduced execution time " * 12 + "(Reyes, 2023)."
    result, flags, _ = await run(
        monkeypatch,
        [long_claim, FIRST],
        settings=Settings(_env_file=None, citation_claim_excerpt_max_chars=80),
    )
    assert result.detail["n_claim_support_candidate_pairs_total"] == 2
    assert result.detail["n_claim_support_pairs_total"] == 1
    assert result.detail["n_claim_support_checked"] == 1
    assert result.detail["claim_support_occurrences"][0]["unassessed_reason"] == "cropped_claim"
    assert result.detail["claim_support_occurrences"][1]["status"] == "assessed"
    assert result.outcome == ResultOutcome.escalated and flags == []


async def test_repeated_strong_numeric_source_preserves_occurrences_and_offsets(monkeypatch):
    claims = [
        "According to [1], execution time decreased.",
        "According to [1], all systems are perfectly secure.",
    ]
    result, flags, _ = await run(monkeypatch, claims, refs=[citation("[1] " + RAW)])
    ledger = result.detail["claim_support_occurrences"]
    assert len(ledger) == result.detail["n_claim_support_checked"] == 2
    assert [item["source_block_index"] for item in ledger] == [0, 1]
    assert [item["source_offset"] for item in ledger] == [claim.index("[1]") for claim in claims]
    assert len(flags) == 1 and flags[0].page_anchor == "p. 2"


async def test_joint_numeric_mark_accounts_for_each_reference_without_claim_attribution(
    monkeypatch,
):
    refs = [citation("[1] " + RAW), citation("[2] Reyes, J. (2023). Another Study. Journal.", 1)]
    result, flags, model = await run(
        monkeypatch, ["Prior work [1,2] found improvements."], refs=refs
    )
    assert result.detail["n_claim_support_candidate_pairs_total"] == 2
    assert (
        result.detail["n_claim_support_pairs_total"]
        == result.detail["n_claim_support_checked"]
        == 0
    )
    assert (
        result.detail["n_claim_support_pairs_unassessed"]
        == result.detail["n_claim_support_unassessed"]
        == 2
    )
    assert all(
        item["unassessed_reason"] == "ambiguous_linkage"
        for item in result.detail["claim_support_occurrences"]
    )
    assert model.prompts == [] and flags == [] and result.outcome == ResultOutcome.escalated


@pytest.mark.parametrize("value", [0, -1])
def test_total_pair_limit_must_be_positive(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, claim_support_max_pairs_per_run=value)


class Batches:
    def __init__(self, middle):
        self.calls = 0
        self.middle = middle

    async def complete(self, _kind, _prompt, **_context):
        self.calls += 1
        if self.calls == 2:
            if isinstance(self.middle, Exception):
                raise self.middle
            return self.middle
        count = 1 if self.calls == 3 else 2
        return {
            "verdicts": [
                {"index": index, "verdict": "supported", "reasoning": "Scripted."}
                for index in range(count)
            ]
        }


@pytest.mark.parametrize(
    "middle,reason,checked,skipped,calls",
    [
        (QuotaExhaustedError("Controlled"), "quota", 2, 3, 2),
        (ApiDownError("Controlled"), "api_down", 3, 2, 3),
        ({"bad": "shape"}, "parse_failure", 3, 2, 3),
        (
            {"verdicts": [{"index": 0, "verdict": "supported", "reasoning": "Scripted."}]},
            "parse_failure",
            3,
            2,
            3,
        ),
    ],
)
async def test_runner_batch_mapping_and_distinct_limit_failure_counts(
    middle, reason, checked, skipped, calls
):
    pairs = [
        ClaimSupportInput(citation(RAW), FIRST, ABSTRACT, f"p. {index + 1}") for index in range(7)
    ]
    before = list(pairs)
    model = Batches(middle)
    result = await run_claim_support_check(
        model,
        pairs,
        check_run_id=1,
        settings=Settings(
            _env_file=None, claim_support_max_pairs_per_run=5, claim_support_max_pairs_per_call=2
        ),
    )
    assert model.calls == calls and pairs == before
    records = result.pair_results
    assert [item.pair_index for item in records] == list(range(7))
    assert sum(item.status == "assessed" for item in records) == checked
    assert sum(item.unassessed_reason == reason for item in records) == skipped
    assert result.n_skipped_limit == 2
    assert result.n_skipped_total == skipped + 2
    assert len(records) == checked + result.n_skipped_total
    assert records[2].batch_index == 1 and records[2].batch_local_index == 0
    assert records[3].batch_index == 1 and records[3].batch_local_index == 1
    assert all(item.batch_index is None for item in records[5:])


@pytest.mark.parametrize("count", [1, 2, 3])
async def test_pair_cap_boundary_has_no_off_by_one_or_implicit_sampling(monkeypatch, count):
    result, _, model = await run(
        monkeypatch,
        [FIRST] * count,
        settings=Settings(_env_file=None, claim_support_max_pairs_per_run=2),
    )
    assert result.detail["n_claim_support_checked"] == min(count, 2)
    assert result.detail["n_claim_support_skipped_limit"] == max(0, count - 2)
    assert len(model.prompts) == 1
    assert result.outcome == (ResultOutcome.escalated if count > 2 else ResultOutcome.passed)


async def test_missing_or_duplicate_runner_record_cannot_claim_complete_coverage(monkeypatch):
    async def malformed(_llm, _pairs, **_kwargs):
        record = ClaimSupportPairResult(0, "assessed", model_verdict="supported")
        return ClaimSupportResult([], 0, 0, 0, pair_results=[record, replace(record)])

    monkeypatch.setattr(verify, "run_claim_support_check", malformed)
    result, flags, _ = await run(monkeypatch, [FIRST, FIRST])
    assert result.detail["n_claim_support_checked"] == 0
    assert result.detail["n_claim_support_skipped_parse_failure"] == 2
    assert result.outcome == ResultOutcome.unverifiable and flags == []


@pytest.mark.parametrize(
    "indexes", [[0, 1, 1], [0, 1, 2], [0, 1, -1], [0, True], [0, "1"], [0, 1.0]]
)
async def test_invalid_model_indexes_cannot_overwrite_or_claim_complete_coverage(
    monkeypatch, indexes
):
    async def invalid_response(self, prompt_type, prompt, **_context):
        self.prompts.append(prompt)
        return {
            "verdicts": [
                {
                    "index": index,
                    "verdict": "possibly_unsupported" if position == 1 else "supported",
                    "reasoning": "Conflicting or invalid scripted mapping.",
                    "abstract_excerpt": "Security was not studied." if position == 1 else None,
                }
                for position, index in enumerate(indexes)
            ]
        }

    monkeypatch.setattr(ScriptedClaims, "complete", invalid_response)
    result, flags, model = await run(monkeypatch, [FIRST, SECOND])
    assert result.outcome == ResultOutcome.unverifiable
    assert flags == [] and len(model.prompts) == 1
    assert result.detail["n_claim_support_checked"] == 0
    assert result.detail["n_claim_support_skipped_parse_failure"] == 2
    assert all(
        item["status"] == "unassessed" and item["unassessed_reason"] == "parse_failure"
        for item in result.detail["claim_support_occurrences"]
    )


@pytest.mark.parametrize("returned_index", [0, 1])
async def test_missing_model_index_withholds_the_entire_batch(monkeypatch, returned_index):
    async def missing_response(self, prompt_type, prompt, **_context):
        self.prompts.append(prompt)
        return {
            "verdicts": [
                {"index": returned_index, "verdict": "supported", "reasoning": "Scripted."}
            ]
        }

    monkeypatch.setattr(ScriptedClaims, "complete", missing_response)
    result, flags, model = await run(monkeypatch, [FIRST, SECOND])
    assert result.outcome == ResultOutcome.unverifiable and flags == []
    assert len(model.prompts) == 1
    assert result.detail["n_claim_support_checked"] == 0
    assert result.detail["n_claim_support_skipped_parse_failure"] == 2
    assert all(
        item["model_verdict"] is None and item["unassessed_reason"] == "parse_failure"
        for item in result.detail["claim_support_occurrences"]
    )


async def test_permuted_complete_model_indexes_retain_the_correct_claim(monkeypatch):
    original_complete = ScriptedClaims.complete

    async def permuted_response(self, prompt_type, prompt, **context):
        response = await original_complete(self, prompt_type, prompt, **context)
        response["verdicts"].reverse()
        return response

    monkeypatch.setattr(ScriptedClaims, "complete", permuted_response)
    result, flags, _model = await run(monkeypatch, [FIRST, SECOND])
    assert result.detail["n_claim_support_checked"] == 2
    assert result.detail["n_claim_support_skipped_parse_failure"] == 0
    assert len(flags) == 1 and flags[0].evidence_excerpt == SECOND
    assert flags[0].page_anchor == "p. 2"
    assert flags[0].detail["claim_support_pair_index"] == 1
