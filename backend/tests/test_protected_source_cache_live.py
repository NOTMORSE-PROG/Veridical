"""F1/F2/F9: real ownership and stored-source errors retain cache protection."""

import asyncio
import hashlib
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.security import hash_password
from app.config import get_settings
from app.db import sqlalchemy_url
from app.models.enums import CheckRunStatus, IngestStatus, LibraryEntrySource, LibraryEntryStatus
from app.models.instructor import Instructor
from app.models.library import LibraryEntry
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun
from tests.test_bug_138_durable_storage import (  # noqa: F401 -- shared fixtures
    api_scratch_url,
    client,
)
from tests.test_ingest_fixtures import FIXTURE_DIR

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="integration: requires PostgreSQL"
)


@pytest.fixture
def cache_records(client, api_scratch_url):  # noqa: F811 -- imported pytest fixtures
    tc, storage = client
    settings = get_settings()
    content = (FIXTURE_DIR / "native.pdf").read_bytes()
    source_hash = hashlib.sha256(content).hexdigest()

    async def prepare():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                owner = await session.scalar(
                    select(Instructor).where(Instructor.email == "prof@tip.edu.ph")
                )
                foreign = await session.scalar(
                    select(Instructor).where(Instructor.email == "cache-boundary@example.test")
                )
                if foreign is None:
                    session.add(
                        Instructor(
                            email="cache-boundary@example.test",
                            display_name="Other instructor",
                            password_hash=hash_password("other-password"),
                        )
                    )
                rubric = Rubric(
                    instructor_id=owner.id,
                    title="Source-cache test format",
                    source_file="pending",
                    is_active=True,
                )
                session.add(rubric)
                await session.flush()
                directory = settings.data_dir / settings.rubric_upload_subdir
                directory.mkdir(parents=True, exist_ok=True)
                rubric_path = directory / f"{rubric.id}.pdf"
                rubric_path.write_bytes(content)
                rubric.source_file = str(rubric_path)
                runs = {}
                mids = {}
                for label in ("owned", "purged", "missing"):
                    path = settings.data_dir / "uploads" / f"cache-{label}.pdf"
                    if label == "owned":
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(content)
                    manuscript = Manuscript(
                        instructor_id=owner.id,
                        file_ref=str(path),
                        original_filename="native.pdf",
                        group_label="Synthetic cache control",
                        ingest_status=IngestStatus.done,
                        purged_at=datetime.now(UTC) if label == "purged" else None,
                    )
                    session.add(manuscript)
                    await session.flush()
                    run = CheckRun(
                        manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
                    )
                    session.add(run)
                    session.add(
                        LibraryEntry(
                            manuscript_id=manuscript.id,
                            source=LibraryEntrySource.legacy_check_upload,
                            status=LibraryEntryStatus.active,
                        )
                    )
                    await session.flush()
                    runs[label] = run.id
                    mids[label] = manuscript.id
                await session.commit()
                return {
                    "runs": runs,
                    "manuscripts": mids,
                    "rubric_id": rubric.id,
                    "rubric_path": rubric_path,
                }
        finally:
            await engine.dispose()

    records = asyncio.run(prepare())
    records.update(content=content, source_hash=source_hash, client=tc, storage=storage)
    return records


def _protected(response, status):
    assert response.status_code == status, response.text
    assert response.headers.get("cache-control") == "private, no-store"
    assert "x-robots-tag" not in response.headers
    return response


def test_owned_bytes_range_and_private_error_ownership_with_real_sql(cache_records, monkeypatch):
    r = cache_records
    tc = r["client"]
    rid = r["runs"]["owned"]
    mid = r["manuscripts"]["owned"]
    rubric = r["rubric_id"]
    source_hash = r["source_hash"]
    source_paths = [
        f"/check-runs/{rid}/document/file",
        f"/library/{mid}/document/file",
        f"/rubrics/{rubric}/document/file?source_sha256={source_hash}",
        f"/rubrics/{rubric}/document/original?source_sha256={source_hash}",
    ]
    for path in source_paths:
        assert _protected(tc.get(path), 200).content == r["content"]
    partial = _protected(tc.get(source_paths[0], headers={"Range": "bytes=0-7"}), 206)
    assert partial.content == r["content"][:8]
    assert partial.headers["content-range"] == f"bytes 0-7/{len(r['content'])}"
    _protected(tc.get(f"/rubrics/{rubric}/document/file?source_sha256={'0' * 64}"), 409)
    assert tc.post("/auth/logout").status_code == 200
    for path in source_paths:
        response = _protected(tc.get(path), 401)
        assert response.json() == {
            "error": {"code": "unauthenticated", "message": "Not signed in."}
        }
    assert (
        tc.post(
            "/auth/login",
            json={"email": "cache-boundary@example.test", "password": "other-password"},
        ).status_code
        == 200
    )
    # D029 permits authenticated reads of active Library originals across owners.
    assert _protected(tc.get(source_paths[1]), 200).content == r["content"]
    readers = []

    def forbidden(*args, **kwargs):
        readers.append(True)
        raise AssertionError("Unauthorized source reached storage or reader")

    with monkeypatch.context() as scope:
        scope.setattr("app.report.source_files.get_storage", forbidden)
        scope.setattr("app.rubric.router.document_file", forbidden)
        for real_path, absent_path in [
            (source_paths[0], f"/check-runs/{rid + 1000000}/document/file"),
            (
                source_paths[2],
                f"/rubrics/{rubric + 1000000}/document/file?source_sha256={source_hash}",
            ),
        ]:
            foreign = _protected(tc.get(real_path), 404)
            absent = _protected(tc.get(absent_path), 404)
            if real_path == source_paths[0]:
                # The existing envelope echoes the public request ID on both misses.
                for response, requested in ((foreign, rid), (absent, rid + 1000000)):
                    assert response.json() == {
                        "error": {
                            "code": "not_found",
                            "message": f"No check run with id {requested}.",
                        }
                    }
            else:
                assert foreign.content == absent.content
            assert foreign.headers["cache-control"] == absent.headers["cache-control"]
    assert readers == []
    assert tc.post("/auth/logout").status_code == 200


def test_recorded_purge_and_genuinely_missing_source_keep_structured_errors(cache_records):
    r = cache_records
    tc = r["client"]
    messages = {}
    for state in ("purged", "missing"):
        for path in [
            f"/check-runs/{r['runs'][state]}/document/file",
            f"/library/{r['manuscripts'][state]}/document/file",
        ]:
            response = _protected(tc.get(path), 410)
            assert response.json()["error"]["code"] == "gone"
            messages.setdefault(state, []).append(response.json()["error"]["message"])
    assert messages["purged"][0] != messages["missing"][0]
    r["rubric_path"].unlink()
    response = _protected(
        tc.get(f"/rubrics/{r['rubric_id']}/document/original?source_sha256={r['source_hash']}"), 410
    )
    assert response.json()["error"]["code"] == "gone"
    assert tc.post("/auth/logout").status_code == 200
