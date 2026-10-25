"""V-081 containment and arithmetic regressions; not an accuracy benchmark."""

from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.checks.agreement.extract import Statement
from app.checks.agreement.pair import Candidate, PairingError, _judge_batch
from app.checks.citations import verify
from app.checks.citations.support import ClaimSupportPairResult, ClaimSupportResult
from app.checks.forensics.extract import ReportedStat
from app.checks.forensics.grim import grim_check
from app.checks.forensics.pcheck import recompute_p
from app.config import Settings
from app.external.schemas import VerificationResult
from app.findings.policy import exclusion_reason, reuse_excerpt, reuse_reason, reuse_reasoning
from app.ingest.patterns import load_patterns
from app.ingest.references import parse_reference
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock
from app.models.citation import Citation
from app.models.enums import (
    CheckKind,
    CitationParseStatus,
    FlagSeverity,
    ReadinessStatus,
    ResultOutcome,
)
from app.models.run import CheckResult, Flag
from app.report.scoring import ScorableFlag, ScorableResult, score_check_run
from app.report.service import _load_scorable_flags


@pytest.mark.parametrize(
    "kind", ["reuse_exact_duplicate_passage", "reuse_passage_exact_duplicate", "unknown"]
)
def test_reuse_presentation_preserves_recorded_passages_including_legacy_kinds(kind):
    excerpt = "An actual source passage, unchanged."
    detail = {"kind": kind, "reason": excerpt}
    assert reuse_excerpt(detail, excerpt) == excerpt
    assert reuse_reasoning(detail, excerpt) is None


def test_reuse_lead_preserves_own_context_without_foreign_identity_or_verdict():
    detail = {
        "kind": "reuse_high_similarity_chapter",
        "matched_manuscript_id": 42,
        "own_chapter_title": "Related work",
        "matched_group_label": "Other authors",
        "matched_chapter_title": "Private title",
    }
    reason = reuse_reason(detail)
    assert "Related work" in reason and "#42" in reason
    assert "Other authors" not in reason and "Private title" not in reason
    assert "does not change readiness" in reason


def test_metadata_budget_preserves_entire_raw_source(monkeypatch):
    monkeypatch.setenv("REFERENCE_METADATA_MAX_CHARS", "1024")
    raw = "Author, A. (2024). " + "lowercase. prose " * 32000
    result = parse_reference(raw, 0)
    assert result.raw_text == raw
    assert result.parse_status == CitationParseStatus.parse_failed
    assert result.title is None


@pytest.mark.parametrize(
    "n,mean,expected", [(80, 4.83, True), (40, 1.28, True), (10, 3.33, False), (100, 3.24, True)]
)
def test_exact_half_grim_controls(n, mean, expected):
    assert grim_check(n, mean, prec=2) is expected


@pytest.mark.parametrize(
    "kind,df,value,p", [("t", 68, -1.67, 0.100), ("Z", None, -1.76, 0.078), ("r", 48, -0.32, 0.023)]
)
def test_negative_two_sided_statistics_preserve_correct_p(kind, df, value, p):
    stat = ReportedStat(
        kind="inferential",
        anchor="p. 1",
        page=1,
        paragraph=None,
        source="text",
        raw_text=f"{kind} = {value}, p = {p}",
        test_type=kind,
        df2=df,
        test_value=value,
        p_value=p,
        test_value_precision=2,
        p_value_precision=3,
    )
    result = recompute_p(stat, one_tailed_in_text=False)
    assert result is not None and not result.error
    if kind == "t":
        assert recompute_p(replace(stat, p_value=0.010), one_tailed_in_text=False).error


@pytest.mark.parametrize("kind", ["internal_agreement", "originality_reuse"])
@pytest.mark.parametrize("problem", [None, "unknown_legacy_kind", "reuse_exact_duplicate"])
def test_unvalidated_leads_cannot_deduct_in_pure_scorer(kind, problem):
    result = score_check_run(
        [ScorableResult(1, 1, ResultOutcome.passed, 100)],
        [ScorableFlag(FlagSeverity.high, False, check_kind=kind, problem_kind=problem)],
    )
    assert result.status == ReadinessStatus.ready
    assert result.flag_deduction == result.unresolved_high_flag_count == 0


def test_partial_assessment_is_not_a_complete_verdict():
    result = score_check_run(
        [ScorableResult(1, 1, ResultOutcome.passed, 100)],
        [],
        assessment_incomplete=True,
    )
    assert result.status == ReadinessStatus.needs_review
    assert "unassessed" in result.reason


@pytest.mark.parametrize("indexes", [[0, 0], [], [1]])
async def test_pair_indexes_must_be_complete_unique_and_in_range(indexes):
    intent = Statement("intent", "The system will track orders.", "p. 1", 1, None, "modal")
    outcome = Statement("outcome", "The system tracks orders.", "p. 2", 2, None, "modal")
    llm = AsyncMock()
    llm.complete.return_value = {
        "verdicts": [
            {"index": i, "verdict": "consistent", "reasoning": "The statements agree."}
            for i in indexes
        ]
    }
    with pytest.raises(PairingError):
        await _judge_batch(llm, [Candidate(intent, outcome, 0.9)], check_run_id=1)


class MemorySession:
    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)

    async def scalar(self, statement):
        # These isolated module controls have no owned manuscript/account.
        # Confirmation lookup must fail closed; live DB tests cover ownership.
        return None

    async def flush(self):
        for row in self.rows:
            if getattr(row, "id", None) is None:
                row.id = len(self.rows)

    async def commit(self):
        pass


def source(body, raw_refs):
    blocks = [TextBlock(text=body, page=1, max_font_size=11, bold_ratio=0)]
    blocks.append(TextBlock(text="References", page=2, max_font_size=14, bold_ratio=1))
    blocks += [TextBlock(text=raw, page=2, max_font_size=11, bold_ratio=0) for raw in raw_refs]
    return ExtractionResult(
        page_count=2,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        blocks=blocks,
        images=[],
        section_tree=SectionTree(
            source="heuristics",
            nodes=[
                SectionNode(title="References", level=1, page=2),
            ],
        ),
    )


def citation(raw, index=0):
    # Whole-stage controls use the same source-derived fields as ingestion.
    return Citation(manuscript_id=1, **asdict(parse_reference(raw, index)))


@pytest.mark.parametrize("failure", ["html", "null", "list", 400, 422, 401, 403, 410, 429, 503])
async def test_failed_book_lookup_preserves_healthy_neighbor_and_no_absence_cache(
    monkeypatch, failure
):
    refs = [
        citation("Alvarez, A. (2023). First Book. ISBN: 9780132350884."),
        citation("Reyes, J. (2024). Second Book. ISBN: 9780201616224.", 1),
    ]
    assert refs[0].isbn == "9780132350884" and refs[1].isbn == "9780201616224"
    requests = []

    class Governor:
        acquisitions = 0

        async def acquire(self):
            self.acquisitions += 1

    governor = Governor()

    def response(request):
        requests.append(request.url.path)
        if refs[0].isbn in request.url.path:
            if isinstance(failure, int):
                return httpx.Response(failure)
            body = {"html": b"<html>upstream error</html>", "null": b"null", "list": b"[]"}[failure]
            return httpx.Response(200, content=body)
        return httpx.Response(200, json={"title": "Second Book", "key": "/books/CONTROLLED"})

    stored = AsyncMock()
    monkeypatch.setattr(verify.cache, "get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr(verify.cache, "store_result", stored)
    monkeypatch.setattr(verify.cache, "is_instructor_confirmed", AsyncMock(return_value=False))
    monkeypatch.setattr(verify.openlibrary_client, "_get_governor", lambda _settings: governor)
    monkeypatch.setattr(verify, "write_audit_event", AsyncMock())
    session = MemorySession()
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        result = await verify.run_citation_integrity_check(
            session,
            client,
            1,
            refs,
            source("Prior work (Alvarez, 2023; Reyes, 2024).", [ref.raw_text for ref in refs]),
            load_patterns(),
            Settings(_env_file=None, external_max_retries=1, external_retry_base_seconds=0),
        )
    statuses = {
        row["reference_order_index"]: row["status"]
        for row in result.detail["reference_metadata_coverage"]
    }
    assert statuses == {0: "api_down", 1: "existence_confirmed"}
    assert result.detail["n_metadata_api_down"] == 1
    assert result.detail["n_metadata_not_found"] == 0
    stored.assert_awaited_once()
    assert stored.await_args.kwargs["key_value"] == refs[1].isbn
    assert stored.await_args.kwargs["result"].found is True
    unavailable = [
        row
        for row in session.rows
        if isinstance(row, Flag) and row.detail.get("kind") == "unverifiable_api_down"
    ]
    assert len(unavailable) == 1
    assert unavailable[0].detail["affects_readiness"] is False
    assert exclusion_reason("citation_integrity", unavailable[0].detail) == "unassessed_evidence"
    expected_bad_attempts = 2 if failure in ("html", "null", "list", 429, 503) else 1
    assert len(requests) == governor.acquisitions == expected_bad_attempts + 1
    assert requests[-1].endswith(f"/{refs[1].isbn}.json")


async def test_current_flag_projection_excludes_exact_legacy_outage_without_hiding_real_neighbor():
    result = CheckResult(
        id=1,
        check_run_id=1,
        criterion_id=None,
        kind=CheckKind.citation_integrity,
        outcome=ResultOutcome.api_down,
        detail={},
    )
    details = [
        {"kind": "unverifiable_api_down"},
        {"kind": "unverifiable_api_down", "affects_readiness": False},
        {"kind": "unverifiable_not_found"},
    ]
    flags = [
        Flag(
            id=index,
            check_result_id=1,
            severity=FlagSeverity.low,
            overridden=False,
            evidence_excerpt=f"Reference {index}",
            page_anchor="p. 2",
            detail=detail,
        )
        for index, detail in enumerate(details, 1)
    ]
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(all=lambda: [(flag, result) for flag in flags])
        )
    )
    projected = await _load_scorable_flags(session, 1)
    assert [flag.has_verdict for flag in projected] == [False, False, True]
    scored = score_check_run([ScorableResult(1, 1, ResultOutcome.passed, 100)], projected[:2])
    assert scored.flag_deduction == 0
    assert scored.status == ReadinessStatus.ready
    assert details[0] == {
        "kind": "unverifiable_api_down"
    }  # Read projection does not backfill facts.


@pytest.mark.parametrize(
    "kind", ["unverifiable_not_found", "retracted_source", "corrected_source", "unknown"]
)
def test_citation_unavailability_exclusion_does_not_silence_real_or_unknown_findings(kind):
    assert exclusion_reason("citation_integrity", {"kind": kind}) is None
    assert exclusion_reason("statistical_forensics", {"kind": "unverifiable_api_down"}) is None


async def check(monkeypatch, body, refs, *, llm=None, abstract="A source abstract."):
    session = MemorySession()
    provider = AsyncMock(
        return_value=verify.CitationVerdict(
            verify.VerdictKind.existence_confirmed,
            result=VerificationResult(
                provider="crossref", found=True, title="A Study", abstract=abstract
            ),
        )
    )

    async def assessed(_llm, pairs, **_kwargs):
        return ClaimSupportResult(
            [],
            0,
            0,
            0,
            pair_results=[
                ClaimSupportPairResult(index, "assessed", model_verdict="supported")
                for index in range(len(pairs))
            ],
        )

    support = AsyncMock(side_effect=assessed)
    monkeypatch.setattr(verify, "verify_citation", provider)
    monkeypatch.setattr(verify, "run_claim_support_check", support)
    monkeypatch.setattr(verify, "write_audit_event", AsyncMock())
    result = await verify.run_citation_integrity_check(
        session,
        None,
        1,
        refs,
        source(body, [r.raw_text for r in refs]),
        load_patterns(),
        Settings(),
        llm,
    )
    return result, session.rows, provider, support


async def test_present_unparsed_reference_does_not_become_an_orphan(monkeypatch):
    result, rows, provider, _ = await check(
        monkeypatch,
        "Prior work (Reyes, 2023).",
        [citation("Reyes J. Study. Journal, 2023.")],
    )
    assert not any(isinstance(row, Flag) for row in rows)
    provider.assert_not_called()
    assert result.detail["n_metadata_skipped_insufficient"] == 1
    assert result.outcome == ResultOutcome.escalated


async def test_identifier_lookup_does_not_claim_incomplete_metadata_verified(monkeypatch):
    ref = citation("Reyes, J. (n.d.). A Study. Journal. doi:10.1234/known")
    result, _, provider, support = await check(
        monkeypatch,
        "Prior work (Reyes, n.d.).",
        [ref],
        llm=object(),
    )
    provider.assert_awaited_once()
    support.assert_not_called()
    record = result.detail["reference_metadata_coverage"][0]
    assert record["lookup_attempted"] and record["lookup_kind"] == "doi"
    assert record["metadata_complete"] is False
    assert result.detail["n_metadata_skipped_insufficient"] == 1
    assert result.detail["n_claim_support_unassessed"] == 1
    assert result.detail["n_claim_support_checked"] == 0
    assert result.outcome == ResultOutcome.escalated


async def test_missing_isbn_title_never_searches_the_raw_reference_as_a_title(monkeypatch):
    ref = citation("Unstructured book metadata with an ISBN")
    ref.title = None
    ref.isbn = "9780132350884"
    isbn_lookup = AsyncMock(return_value=VerificationResult(provider="openlibrary", found=False))
    title_lookup = AsyncMock()
    monkeypatch.setattr(verify.openlibrary_client, "lookup_isbn", isbn_lookup)
    monkeypatch.setattr(verify.gbooks_client, "search_title", title_lookup)

    async def keyed(session, kind, value, *, settings, fetch, instructor_id, reference_text):
        assert instructor_id is None and reference_text == ref.raw_text
        return await fetch()

    monkeypatch.setattr(verify, "_verify_keyed", keyed)
    result = await verify.verify_citation(None, None, ref, settings=Settings())
    assert result.found is False
    isbn_lookup.assert_awaited_once()
    title_lookup.assert_not_called()


async def test_absent_llm_never_counts_an_eligible_claim_as_checked(monkeypatch):
    result, _, _, support = await check(
        monkeypatch, "Prior work (Reyes, 2023).", [citation("Reyes, J. (2023). A Study. Journal.")]
    )
    support.assert_not_called()
    assert result.detail["n_claim_support_checked"] == 0
    assert result.detail["n_claim_support_unassessed"] == 1
    assert result.outcome == ResultOutcome.escalated


async def test_single_strong_numeric_source_reaches_claim_support(monkeypatch):
    result, _, _, support = await check(
        monkeypatch,
        "According to [1], results improved.",
        [citation("[1] Reyes, J. (2023). A Study. Journal.")],
        llm=object(),
    )
    support.assert_awaited_once()
    assert result.detail["n_claim_support_checked"] == 1


async def test_joint_numeric_claim_keeps_evidence_without_inventing_attribution(monkeypatch):
    result, _, _, support = await check(
        monkeypatch,
        "Prior work [1,2] found improvements.",
        [
            citation("[1] Reyes, J. (2023). A Study. Journal."),
            citation("[2] Reyes, J. (2023). Another Study. Journal.", 1),
        ],
        llm=object(),
    )
    support.assert_not_called()
    assert result.detail["numeric_link_evidence"]
    assert result.detail["n_claim_support_unassessed"] == 2
    assert result.outcome == ResultOutcome.escalated
