"""Real owned source/assessment reads using unchanged committed PDF/DOCX inputs."""

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import get_current_instructor
from app.config import Settings
from app.db import get_session
from app.errors import ConflictError, GoneError, NotFoundError
from app.ingest.docx import extract_document
from app.ingest.service import raw_store_path
from app.main import app
from app.models.manuscript import Manuscript
from app.models.run import CheckResult
from app.report import evidence_service as service
from app.report.service import resolve_escalation_for_run
from tests.test_assessment_revision_live import seed
from tests.test_optional_grade_comments_live import factory as factory
from tests.test_optional_grade_comments_live import scratch_url as scratch_url

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="owned Postgres required")
FIXTURES = Path(__file__).parent / "fixtures/ingest"


async def source_run(factory, path, quote, anchor, settings):
    owner, run, ids, _ = await seed(factory)
    data = path.read_bytes()
    async with factory() as session:
        row = await session.get(CheckResult, ids[0])
        criterion = row.criterion_id
        row.detail = {**row.detail, "evidence": [{"quote": quote, "anchor": anchor}]}
        parent = await session.get(service.CheckRun, run)
        manuscript = await session.get(Manuscript, parent.manuscript_id)
        manuscript.file_ref = str(path)
        manuscript.content_hash = hashlib.sha256(data).hexdigest()
        manuscript_id = manuscript.id
        await session.commit()
    return owner, run, ids[0], criterion, manuscript_id, data


@pytest.mark.asyncio
async def test_real_pdf_reader_keeps_content_identity_after_grade_but_rejects_old_evidence(
    factory, tmp_path
):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    path = FIXTURES / "native.pdf"
    owner, run, result_id, criterion, manuscript_id, data = await source_run(
        factory,
        path,
        "A STUDY OF THINGS",
        "page 1",
        settings,
    )
    async with factory() as session:
        first = await service.get_evidence_manifest(
            session, settings, check_run_id=run, instructor_id=owner
        )
    reference = next(ref for ref in first.references if ref.criterion_id == criterion)
    async with factory() as session:
        location = await service.get_private_evidence_location(
            session, run, owner, reference.reference_id, first.snapshot_id, settings
        )
    assert location.placement.state == "text_layer_match" and location.placement.page == 1
    assert location.placement.boxes and location.source_identity
    async with factory() as session:
        original = await service.get_private_evidence_reader_source(
            session, run, owner, location.source_identity, settings
        )
    assert original.pdf_bytes == data
    async with factory() as session:
        await resolve_escalation_for_run(
            session, run, result_id, owner, "mark_pass", "", expected_revision=0
        )
    async with factory() as session:
        changed = await service.get_evidence_manifest(
            session, settings, check_run_id=run, instructor_id=owner
        )
    assert changed.snapshot_id != first.snapshot_id and changed.assessment_revision == 1
    async with factory() as session:
        with pytest.raises(ConflictError):
            await service.get_private_evidence_location(
                session, run, owner, reference.reference_id, first.snapshot_id, settings
            )
    async with factory() as session:
        same = await service.get_private_evidence_reader_source(
            session, run, owner, location.source_identity, settings
        )
    assert same.pdf_bytes == data and same.source_identity == original.source_identity
    async with factory() as session:
        with pytest.raises(NotFoundError):
            await service.get_private_evidence_reader_source(
                session, run, owner + 999, location.source_identity, settings
            )
    async with factory() as session:
        manuscript = await session.get(Manuscript, manuscript_id)
        from datetime import UTC, datetime

        manuscript.purged_at = datetime.now(UTC)
        await session.commit()
    async with factory() as session:
        with pytest.raises(GoneError):
            await service.get_private_evidence_reader_source(
                session, run, owner, location.source_identity, settings
            )
    assert path.read_bytes() == data


@pytest.mark.asyncio
async def test_real_docx_image_route_binds_exact_projection_and_rejects_replacement(
    factory, tmp_path, monkeypatch
):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    path = FIXTURES / "native.docx"
    extraction = extract_document(str(path), settings)
    image = next(image for image in extraction.images if image.docx_source)
    owner, run, _, criterion, manuscript_id, data = await source_run(
        factory,
        path,
        "",
        f"paragraph {image.paragraph}",
        settings,
    )
    raw = raw_store_path(settings, manuscript_id)
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(extraction.model_dump_json(), encoding="utf-8")
    async with factory() as session:
        manifest = await service.get_evidence_manifest(
            session, settings, check_run_id=run, instructor_id=owner
        )
    reference = next(ref for ref in manifest.references if ref.criterion_id == criterion)
    async with factory() as session:
        location = await service.get_private_evidence_location(
            session, run, owner, reference.reference_id, manifest.snapshot_id, settings
        )
    assert location.placement.state == "paragraph_only" and location.source_identity
    actor = owner

    async def session_dependency():
        async with factory() as session:
            yield session

    saved = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_current_instructor] = lambda: SimpleNamespace(id=actor)
    monkeypatch.setattr("app.report.router.get_settings", lambda: settings)
    route = f"/check-runs/{run}/evidence/source-images/0?source_identity={location.source_identity}"
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://local.test"
        ) as client:
            response = await client.get(route)
            assert response.status_code == 200
            assert response.headers["x-evidence-source-identity"] == location.source_identity
            assert response.headers["cache-control"] == "private, no-store"
            assert hashlib.sha256(response.content).hexdigest() == image.docx_source.sha256
            actor = owner + 999
            denied = await client.get(route)
            assert (
                denied.status_code == 404 and denied.headers["cache-control"] == "private, no-store"
            )
            actor = owner
            extraction.blocks[0].text += " Changed projected text."
            raw.write_text(extraction.model_dump_json(), encoding="utf-8")
            stale = await client.get(route)
            assert (
                stale.status_code == 409 and stale.headers["cache-control"] == "private, no-store"
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)
    assert path.read_bytes() == data
