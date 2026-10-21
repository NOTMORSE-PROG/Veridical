"""V-081: separately frozen F5/F6 controls, not representative accuracy.

The original V4 module, default collection and invalid zero-flag assertion
remain untouched. These controls distinguish source-correct metadata from
the historical mismatched reference without treating fake claim support as
semantic evidence or an empty flag list as proof of completed computation.
"""

import asyncio
import hashlib
import importlib.metadata
import json
import os
import shutil
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

import pytest
import statcheck.helpers_parse_stats
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.checks.forensics import checks, pcheck
from app.checks.forensics import service as forensics
from app.checks.source_quality import source_limitation_detail
from app.config import get_settings
from app.db import sqlalchemy_url
from app.ingest.service import ingest_manuscript, load_raw_store
from app.llm.fake import FakeLLMClient
from app.models.audit import AuditLog
from app.models.citation import Citation
from app.models.citation_cache import CitationCache
from app.models.enums import CheckKind, CheckRunStatus, IngestStatus, LLMMode, ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckResult, CheckRun, Flag
from app.pipeline import machine
from tests.test_schema import _admin_execute, _alembic_config, _swap_db
from tests.test_v4_seeded_regression import _clean_control_docx
from tests.v4_control_fixture import (
    FIXTURE_DIR,
    build_control_docx,
    control_inventory,
    load_manifest,
    load_recipe,
)

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs live Postgres and real public metadata services",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_source_and_provenance_remain_identifiable():
    manifest, recipe = load_manifest(), load_recipe()
    assert _sha(FIXTURE_DIR / "recipe.json") == manifest["recipe_sha256"]
    assert _sha(FIXTURE_DIR / "control.docx") == manifest["docx_sha256"]
    original = Path(__file__).with_name("test_v4_seeded_regression.py")
    assert _sha(original) == manifest["original_frozen_test_sha256"]
    with ZipFile(FIXTURE_DIR / "control.docx") as archive:
        assert (
            hashlib.sha256(archive.read("word/document.xml")).hexdigest()
            == manifest["document_xml_sha256"]
        )
    assert control_inventory(FIXTURE_DIR / "control.docx") == manifest["canonical_inventory"]
    assert manifest["canonical_inventory"]["tables"] == [recipe["table_rows"]]
    assert manifest["canonical_inventory"]["paragraphs"][-2:] == recipe["references"]
    oracle = manifest["numerical_oracles"]["single_integer_item"]
    assert len(oracle["responses"]) == oracle["n"] == 10
    assert sum(oracle["responses"]) == oracle["total"] == 33
    assert sum(oracle["responses"]) / oracle["n"] == float(oracle["mean"])


def test_recipe_build_is_deterministic(tmp_path):
    first = build_control_docx(tmp_path / "first.docx")
    second = build_control_docx(tmp_path / "second.docx")
    assert first.read_bytes() == second.read_bytes() == (FIXTURE_DIR / "control.docx").read_bytes()


@pytest.fixture()
def scratch_url():
    """A new migrated DB per case guarantees that metadata starts cold."""
    base = os.environ["DATABASE_URL"]
    name = f"veridical_v4control_{uuid.uuid4().hex}"
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{name}"'))
    url = _swap_db(base, name)
    try:
        command.upgrade(_alembic_config(url), "head")
        yield url
    finally:
        asyncio.run(_admin_execute(base, f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture()
async def session_factory(scratch_url):
    engine = create_async_engine(sqlalchemy_url(scratch_url), hide_parameters=True)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _row(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _expected_statcheck_t(recipe, manifest):
    """Keep the scientific df frozen while correcting a harness field name.

    The pre-output recipe mislabeled the sole t degree of freedom as df1.
    statcheck's documented parser places it in df2 and leaves df1 empty.
    Preserve that frozen recipe and require this exact mapping, never an
    either-slot fallback. The independent mathematical oracle also binds df.
    """
    expected = recipe["expected"]["inferential"]
    oracle = manifest["numerical_oracles"]["student_t_two_sided"]
    assert expected["test_type"] == "t"
    assert expected["df1"] == oracle["df"] and expected["df2"] is None
    return {**expected, "df1": None, "df2": oracle["df"]}


class AuditedFake(FakeLLMClient):
    def __init__(self, session_factory):
        super().__init__(session_factory=session_factory)
        self.prompt_types = []

    async def complete(self, prompt_type, prompt, **kwargs):
        self.prompt_types.append(prompt_type)
        return await super().complete(prompt_type, prompt, **kwargs)


async def _exercise(session_factory, scratch_url, tmp_path, monkeypatch, *, historical=False):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", scratch_url)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "1")
    get_settings.cache_clear()
    settings = get_settings()
    source = (
        Path(_clean_control_docx(tmp_path))
        if historical
        else Path(shutil.copyfile(FIXTURE_DIR / "control.docx", tmp_path / "control.docx"))
    )
    evidence = {
        "scope": load_manifest()["scope"],
        "source_sha256_before_ingest": _sha(source),
        "recipe_sha256": _sha(FIXTURE_DIR / "recipe.json"),
        "manifest_sha256": _sha(FIXTURE_DIR / "provenance.json"),
        "historical_input": historical,
        "http": [],
        "p_evaluations": [],
        "grim_evaluations": [],
        "extracted_stats": [],
        "statcheck_contract": {
            "version": "t-single-df-in-df2-v1",
            "installed_version": importlib.metadata.version("statcheck"),
            "parser_sha256": _sha(Path(statcheck.helpers_parse_stats.__file__)),
            "frozen_recipe_df_field_misnamed": "df1",
            "dependency_t_df_field": "df2",
        },
    }
    original_client = machine.build_http_client
    original_p, original_grim = pcheck.recompute_p, checks.grim_check
    original_extract = forensics.extract_all_stats

    async def observe_response(response):
        body = await response.aread()
        body_path = tmp_path / f"provider-{len(evidence['http'])}.json"
        body_path.write_bytes(body)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        message = payload.get("message", {}) if isinstance(payload, dict) else {}
        evidence["http"].append(
            {
                "url": str(response.request.url),
                "status": response.status_code,
                "retrieved_at": datetime.now(UTC).isoformat(),
                "response_sha256": hashlib.sha256(body).hexdigest(),
                "response_file": body_path.name,
                "registered_doi": message.get("DOI") if isinstance(message, dict) else None,
            }
        )

    def observed_client(config):
        client = original_client(config)
        client.event_hooks["response"].append(observe_response)
        return client

    def observed_p(stat, **kwargs):
        result = original_p(stat, **kwargs)
        evidence["p_evaluations"].append(
            {
                "stat": asdict(stat),
                "arguments": kwargs,
                "result": asdict(result) if result else None,
            }
        )
        return result

    def observed_grim(n, mean, **kwargs):
        result = original_grim(n, mean, **kwargs)
        evidence["grim_evaluations"].append(
            {"n": n, "mean": mean, "arguments": kwargs, "result": result}
        )
        return result

    def observed_extract(extraction):
        stats = original_extract(extraction)
        evidence["extracted_stats"].extend(asdict(stat) for stat in stats)
        return stats

    monkeypatch.setattr(machine, "build_http_client", observed_client)
    monkeypatch.setattr(pcheck, "recompute_p", observed_p)
    monkeypatch.setattr(checks, "grim_check", observed_grim)
    monkeypatch.setattr(forensics, "extract_all_stats", observed_extract)
    llm = AuditedFake(session_factory)
    run_id = None
    try:
        async with session_factory() as session:
            evidence["cache_rows_before"] = await session.scalar(
                select(func.count()).select_from(CitationCache)
            )
            assert evidence["cache_rows_before"] == 0
            owner = Instructor(email="v4-control@test.local", display_name="V4 control")
            session.add(owner)
            await session.commit()
            manuscript = Manuscript(
                instructor_id=owner.id,
                group_label="Synthetic control",
                original_filename=source.name,
                file_ref=str(source),
            )
            session.add(manuscript)
            await session.commit()
            await ingest_manuscript(session, manuscript, source, settings)
            evidence["manuscript"] = _row(manuscript)
            extraction = load_raw_store(settings, manuscript.id)
            evidence["extraction"] = extraction.model_dump(mode="json")
            evidence["source_limitation"] = source_limitation_detail(extraction)
            citations = list(
                (
                    await session.scalars(
                        select(Citation)
                        .where(Citation.manuscript_id == manuscript.id)
                        .order_by(Citation.order_index)
                    )
                ).all()
            )
            evidence["citations"] = [_row(citation) for citation in citations]
            assert manuscript.ingest_status == IngestStatus.done
            rubric = Rubric(instructor_id=owner.id, title="Empty mechanism control", source_file="")
            session.add(rubric)
            await session.commit()
            run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id, llm_mode=LLMMode.fake)
            session.add(run)
            await session.commit()
            run_id = run.id
        async with session_factory() as session:
            run = await session.get(CheckRun, run_id)
            await machine.run_check_run(session, run, settings, llm)
    except BaseException as exc:
        evidence["exception_type"] = type(exc).__name__
        raise
    finally:
        async with session_factory() as session:
            if run_id is not None:
                evidence["run"] = _row(await session.get(CheckRun, run_id))
                evidence["results"] = [
                    _row(row)
                    for row in await session.scalars(
                        select(CheckResult).where(CheckResult.check_run_id == run_id)
                    )
                ]
                evidence["flags"] = [
                    _row(row)
                    for row in await session.scalars(
                        select(Flag).join(CheckResult).where(CheckResult.check_run_id == run_id)
                    )
                ]
                evidence["audit"] = [
                    _row(row)
                    for row in await session.scalars(
                        select(AuditLog).where(AuditLog.check_run_id == run_id)
                    )
                ]
            evidence["cache"] = [_row(row) for row in await session.scalars(select(CitationCache))]
        evidence["fake_prompt_types"] = llm.prompt_types
        evidence["source_sha256_after_pipeline"] = _sha(source)
        path = tmp_path / "evidence.json"
        path.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"V4_CONTROL_EVIDENCE={path}")
    return evidence


def _completed_fake_run(evidence):
    assert evidence["run"]["status"] == CheckRunStatus.done
    assert evidence["run"]["llm_mode"] == LLMMode.fake
    assert evidence["cache_rows_before"] == 0
    assert evidence["source_sha256_before_ingest"] == evidence["source_sha256_after_pipeline"]
    calls = [row for row in evidence["audit"] if row["event_type"] == "llm_call"]
    assert len(calls) == len(evidence["fake_prompt_types"])
    assert all(row["payload"].get("fake_llm") is True for row in calls)
    assert all(row["payload"].get("model") == "fake" for row in calls)


@live
async def test_source_validated_control_runs_actual_metadata_and_numerical_checks(
    session_factory, scratch_url, tmp_path, monkeypatch
):
    evidence = await _exercise(session_factory, scratch_url, tmp_path, monkeypatch)
    _completed_fake_run(evidence)
    recipe, manifest = load_recipe(), load_manifest()
    assert evidence["source_sha256_before_ingest"] == manifest["docx_sha256"]
    references = recipe["expected"]["references"]
    assert len(evidence["citations"]) == len(references) == 2
    for index, (citation, expected) in enumerate(
        zip(evidence["citations"], references, strict=True)
    ):
        assert citation["order_index"] == index
        assert citation["raw_text"] == recipe["references"][index]
        assert citation["doi"] == expected["doi"]
        assert citation["title"] == expected["title"]
        assert citation["year"] == expected["year"]
        assert citation["authors"][0].split(",", 1)[0] == expected["first_author"]
        assert citation["parse_status"] == "parsed"
    extraction = evidence["extraction"]
    assert evidence["source_limitation"] is None
    assert not extraction["image_only"]
    assert extraction["images"] == extraction["equations"] == []
    assert len(extraction["tables"]) == 1
    assert extraction["tables"][0]["source"] == "native"
    assert extraction["tables"][0]["rows"] == recipe["table_rows"]

    results = {row["kind"]: row for row in evidence["results"]}
    metadata = results[CheckKind.citation_integrity]["detail"]
    assert metadata["n_references"] == metadata["n_linked"] == 2
    assert metadata["n_orphans"] == metadata["n_uncited"] == metadata["n_linkage_unassessed"] == 0
    for key, expected in recipe["expected"]["metadata_counters"].items():
        assert metadata[key] == expected
    ledger = metadata["reference_metadata_coverage"]
    assert len(ledger) == 2
    for index, (record, expected) in enumerate(zip(ledger, references, strict=True)):
        assert record["reference_order_index"] == index
        assert record["status"] == "existence_confirmed"
        assert record["metadata_complete"] is True and record["lookup_attempted"] is True
        assert record["provider"] == "crossref" and record["retraction_status"] == "checked"
        comparison = record["metadata_comparison"]
        assert comparison["title_status"] == "agrees"
        assert comparison["differing_fields"] == [] and comparison["unassessed_reason"] is None
        assert comparison["policy_version"] and comparison["policy_sha256"]
        for side in ("cited", "registered"):
            value = comparison[side]
            assert value["doi"] == expected["doi"] and value["title"] == expected["title"]
            assert value["year"] == expected["year"]
            assert value["first_author"].split(",", 1)[0] == expected["first_author"]
        assert any(
            response["status"] == 200 and response["registered_doi"] == expected["doi"]
            for response in evidence["http"]
        ), "The cold-cache claim needs an actual successful response for this DOI"
        cached = next(
            row
            for row in evidence["cache"]
            if row["key_kind"] == "doi" and row["key_value"] == expected["doi"]
        )
        assert cached["provider"] == "crossref"
        assert cached["result"]["found"] is True
        assert cached["result"]["retracted"] is cached["result"]["is_correction"] is False

    forensic = results[CheckKind.statistical_forensics]
    assert forensic["outcome"] == ResultOutcome.passed
    for key, expected in recipe["expected"]["forensics_counts"].items():
        assert forensic["detail"][key] == expected
    assert forensic["detail"].get("coverage_reason") is None
    assert forensic["detail"]["n_flags"] == 0
    stats = evidence["extracted_stats"]
    inferential = [stat for stat in stats if stat["kind"] == "inferential"]
    descriptive = [stat for stat in stats if stat["kind"] == "descriptive"]
    assert len(inferential) == 1 and len(descriptive) == 2
    expected_p = _expected_statcheck_t(recipe, manifest)
    for key in (
        "test_type",
        "df1",
        "df2",
        "test_value",
        "test_value_precision",
        "p_value",
        "p_value_precision",
    ):
        assert inferential[0][key] == expected_p[key]
    assert inferential[0]["p_comparison"] == inferential[0]["test_comparison"] == "="
    source_blocks = [
        block for block in extraction["blocks"] if block["text"] == recipe["inferential_text"]
    ]
    assert len(source_blocks) == 1
    assert source_blocks[0]["page"] is None and source_blocks[0]["paragraph"] is not None
    assert inferential[0]["paragraph"] == source_blocks[0]["paragraph"]
    assert inferential[0]["anchor"] == f"\u00b6{source_blocks[0]['paragraph']}"
    by_name = {stat["stat_name"]: stat for stat in descriptive}
    assert by_name["n"]["value"] == 10
    assert by_name["mean"]["value"] == 3.30 and by_name["mean"]["raw_text"] == "3.30"
    assert {stat["table_index"] for stat in descriptive} == {0}
    assert len({(stat["anchor"], stat["group_label"]) for stat in descriptive}) == 1
    assert all(not stat["low_confidence"] and stat["anchor"] for stat in descriptive)
    table = extraction["tables"][0]
    assert table["page"] is None and table["paragraph"] is not None
    assert all(
        stat["source"] == "table"
        and stat["paragraph"] == table["paragraph"]
        and stat["anchor"] == f"\u00b6{table['paragraph']}"
        for stat in descriptive
    )
    assert len(evidence["p_evaluations"]) == len(evidence["grim_evaluations"]) == 1
    assert evidence["p_evaluations"][0]["stat"] == inferential[0]
    assert evidence["p_evaluations"][0]["arguments"] == {"one_tailed_in_text": False}
    evaluated = evidence["p_evaluations"][0]["result"]
    assert evaluated is not None, "No flags must not conceal an unevaluated p-value"
    assert evaluated["computed_p"] == pytest.approx(
        expected_p["computed_p"], abs=expected_p["computed_p_abs_tolerance"], rel=0
    )
    assert evaluated["error"] is evaluated["decision_error"] is False
    assert evidence["grim_evaluations"][0] == {
        "n": 10,
        "mean": 3.30,
        "arguments": {"prec": 2},
        "result": True,
    }
    selected_ids = {
        results[kind]["id"]
        for kind in (CheckKind.citation_integrity, CheckKind.statistical_forensics)
    }
    assert not [row for row in evidence["flags"] if row["check_result_id"] in selected_ids]
    # Aggregate F5 and canned claim-support counters remain in evidence.json.
    # They do not certify semantic support or a complete readiness assessment.


@live
async def test_historical_wrong_reference_is_a_positive_metadata_mismatch_control(
    session_factory, scratch_url, tmp_path, monkeypatch
):
    evidence = await _exercise(session_factory, scratch_url, tmp_path, monkeypatch, historical=True)
    _completed_fake_run(evidence)
    reference = evidence["citations"][0]
    assert reference["order_index"] == 0 and reference["doi"] == "10.1038/nature12373"
    assert reference["authors"][0].startswith("Reyes") and reference["year"] == 2026
    matches = [
        flag
        for flag in evidence["flags"]
        if flag["detail"]
        and flag["detail"].get("kind") == "possible_metadata_mismatch"
        and flag["evidence_excerpt"] == reference["raw_text"]
    ]
    assert len(matches) == 1
    citation_result = next(
        row for row in evidence["results"] if row["kind"] == CheckKind.citation_integrity
    )
    assert matches[0]["check_result_id"] == citation_result["id"]
    assert citation_result["check_run_id"] == evidence["run"]["id"]
    ledger = [
        row
        for row in citation_result["detail"]["reference_metadata_coverage"]
        if row["reference_order_index"] == reference["order_index"]
    ]
    assert len(ledger) == 1 and ledger[0]["status"] == "metadata_mismatch"
    comparison = matches[0]["detail"]["metadata_comparison"]
    assert comparison == ledger[0]["metadata_comparison"]
    assert set(comparison["differing_fields"]) == {"title", "year", "first author"}
    assert comparison["cited"]["doi"] == comparison["registered"]["doi"] == reference["doi"]
    assert comparison["cited"]["year"] == 2026 and comparison["registered"]["year"] == 2013
    assert comparison["registered"]["first_author"] == "Kucsko"
    assert comparison["registered"]["title"] == load_recipe()["expected"]["references"][0]["title"]
    assert comparison["cited"]["title"] == reference["title"]
    assert comparison["cited"]["first_author"] == reference["authors"][0]
    blocks = [
        block
        for block in evidence["extraction"]["blocks"]
        if block["text"] == reference["raw_text"]
    ]
    assert len(blocks) == 1
    assert blocks[0]["page"] is None and blocks[0]["paragraph"] is not None
    assert matches[0]["page_anchor"] == f"\u00b6{blocks[0]['paragraph']}"
    assert any(
        response["status"] == 200 and response["registered_doi"] == reference["doi"]
        for response in evidence["http"]
    )
