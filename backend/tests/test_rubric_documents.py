"""Owned-format reads do not create manuscript records or regenerate criteria."""

import asyncio
import hashlib
import threading
import uuid
import zipfile
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_instructor
from app.config import get_settings
from app.db import get_session
from app.errors import HTTP_STATUS, DocumentPreparationBusyError, VeridicalError
from app.ingest.admission import PreparationAdmission
from app.rubric import documents
from app.rubric.router import router
from tests.test_docx_source_media import _source
from tests.test_ingest_docx import _png_bytes


class OwnedSession:
    def __init__(self, row):
        self.row = row

    async def scalar(self, statement):
        params = statement.compile().params
        return (
            self.row
            if params["id_1"] == self.row.id and params["instructor_id_1"] == self.row.instructor_id
            else None
        )


@pytest.fixture
def owned(tmp_path, monkeypatch):
    settings = get_settings().model_copy(
        update={"data_dir": tmp_path, "document_preparation_slots": 1}
    )
    directory = tmp_path / settings.rubric_upload_subdir
    directory.mkdir()
    path = directory / "41.pdf"
    _source(path)
    row = SimpleNamespace(
        id=41,
        instructor_id=9,
        rubric_family_id=uuid.uuid4(),
        version=3,
        source_file=str(path),
        parse_status="needs_review",
        is_active=False,
    )
    admission = PreparationAdmission(settings)

    @contextmanager
    def scope(_settings):
        with admission.acquire() as lease:
            yield lease

    monkeypatch.setattr(documents, "preparation_scope", scope)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_instructor] = lambda: SimpleNamespace(id=9)
    app.dependency_overrides[get_session] = lambda: OwnedSession(row)
    app.dependency_overrides[get_settings] = lambda: settings
    # The router obtains settings directly, unlike dependency-injected auth.
    monkeypatch.setattr("app.rubric.router.get_settings", lambda: settings)

    @app.exception_handler(VeridicalError)
    async def error_handler(_request, exc):
        return JSONResponse(
            {"error": {"code": exc.code, "message": str(exc)}}, status_code=HTTP_STATUS[exc.code]
        )

    with TestClient(app) as client:
        yield client, row, path, settings, admission
    admission.shutdown()


def test_draft_original_is_source_bound_with_real_ordinals_media_and_no_record_changes(owned):
    client, row, path, settings, _ = owned
    before = vars(row).copy()
    files = {
        str(p.relative_to(settings.data_dir)): p.read_bytes()
        for p in settings.data_dir.rglob("*")
        if p.is_file()
    }
    metadata = client.get("/rubrics/41/document")
    assert metadata.status_code == 200
    assert metadata.headers["cache-control"] == "private, no-store"
    assert metadata.headers["x-content-type-options"] == "nosniff"
    value = metadata.json()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert value == {
        "rubric_id": 41,
        "rubric_family_id": str(row.rubric_family_id),
        "version": 3,
        "available": True,
        "source_format": "docx",
        "source_sha256": digest,
        "unavailable_reason": None,
        "reconstruction": "fresh_local_source",
    }
    query = {"source_sha256": digest}
    response = client.get("/rubrics/41/document/paragraphs", params=query)
    assert response.status_code == 200
    content = response.json()["content"]
    assert [item["paragraph"] for item in content["paragraphs"]] == [0, 2]
    assert [item["paragraph"] for item in content["tables"]] == [3]
    assert [item["paragraph"] for item in content["images"]] == [1, 3]
    assert all(item["figure_reading"] is None for item in content["images"])
    assert content["native_math"]["state"] == "complete"
    for index in (0, 1):
        image = client.get(f"/rubrics/41/document/images/{index}", params=query)
        assert image.status_code == 200 and image.content == _png_bytes()
        assert image.headers["content-type"] == "image/png"
    original = client.get("/rubrics/41/document/original", params=query)
    assert original.status_code == 200 and original.content == path.read_bytes()
    assert "required-format-41-v3.docx" in original.headers["content-disposition"]
    assert client.get("/rubrics/41/document/file", params=query).status_code == 409
    assert str(settings.data_dir) not in response.text
    assert "relationship_id" not in response.text and "part_name" not in response.text
    assert vars(row) == before
    assert {
        str(p.relative_to(settings.data_dir)): p.read_bytes()
        for p in settings.data_dir.rglob("*")
        if p.is_file()
    } == files


@pytest.mark.parametrize(
    "route",
    ["document", "document/paragraphs", "document/file", "document/original", "document/images/0"],
)
def test_foreign_and_unknown_version_refuse_before_files_or_native_admission(
    owned, monkeypatch, route
):
    client, row, _, _, _ = owned
    monkeypatch.setattr(
        documents, "_source_path", lambda *_: pytest.fail("Unauthorized filesystem access")
    )
    monkeypatch.setattr(
        documents, "preparation_scope", lambda *_: pytest.fail("Unauthorized native admission")
    )
    query = {"source_sha256": "a" * 64}
    unknown = client.get(f"/rubrics/42/{route}", params=query)
    row.instructor_id = 10
    foreign = client.get(f"/rubrics/41/{route}", params=query)
    assert unknown.status_code == foreign.status_code == 404
    assert unknown.json() == foreign.json()


@pytest.mark.parametrize("reference", [None, "missing"])
def test_missing_local_original_is_not_an_empty_reading_view_or_changed_draft(owned, reference):
    client, row, path, _, _ = owned
    if reference is None:
        row.source_file = None
    else:
        path.unlink()
    before = vars(row).copy()
    response = client.get("/rubrics/41/document")
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["unavailable_reason"] == "not_available_locally"
    assert response.json()["source_sha256"] is None
    assert vars(row) == before


@pytest.mark.parametrize("leaf", ["42.docx", "41.docx:payload", "../41.docx", "sub/41.docx"])
def test_recorded_path_cannot_select_other_or_outside_files(owned, leaf):
    client, row, path, _, _ = owned
    row.source_file = str(path.parent / leaf)
    response = client.get("/rubrics/41/document")
    assert response.status_code == 410
    assert str(path.parent) not in response.text


def test_supported_extensionless_original_and_changed_source_binding(owned):
    client, row, path, _, _ = owned
    target = path.with_suffix("")
    path.rename(target)
    row.source_file = str(target)
    value = client.get("/rubrics/41/document").json()
    assert value["source_format"] == "docx"
    query = {"source_sha256": value["source_sha256"]}
    _source(target)
    with zipfile.ZipFile(target, "a") as archive:
        archive.writestr("changed.xml", "<changed/>")
    for suffix in ("paragraphs", "original", "images/0"):
        assert client.get(f"/rubrics/41/document/{suffix}", params=query).status_code == 409


def test_impossible_image_ordinal_refuses_before_hashing_or_native_extraction(owned, monkeypatch):
    client, _, _, settings, _ = owned
    monkeypatch.setattr(
        documents, "_bound_source", lambda *_: pytest.fail("Read source for invalid ordinal")
    )
    monkeypatch.setattr(
        documents, "_extract_docx", lambda *_: pytest.fail("Parsed invalid ordinal")
    )
    response = client.get(
        f"/rubrics/41/document/images/{settings.docx_image_max_count}",
        params={"source_sha256": "a" * 64},
    )
    assert response.status_code == 410


def test_corrupt_docx_and_source_limits_refuse_without_uncaught_parser_error(owned):
    client, _, path, settings, _ = owned
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", "<malformed")
    value = client.get("/rubrics/41/document").json()
    assert (
        client.get(
            "/rubrics/41/document/paragraphs", params={"source_sha256": value["source_sha256"]}
        ).status_code
        == 422
    )
    settings.docx_zip_directory_max_bytes = 1
    limited = client.get("/rubrics/41/document")
    assert limited.status_code == 422
    assert limited.json()["error"]["code"] == "file_malformed"


@pytest.mark.parametrize("missing", ["relationship_target", "document_body"])
def test_malformed_required_ooxml_is_a_safe_display_refusal(owned, missing):
    from lxml import etree

    client, row, path, _, _ = owned
    before = vars(row).copy()
    with zipfile.ZipFile(path) as package:
        parts = {name: package.read(name) for name in package.namelist()}
    if missing == "relationship_target":
        relationships = etree.fromstring(parts["_rels/.rels"])
        for relationship in relationships:
            if relationship.get("Type", "").endswith("/officeDocument"):
                relationship.attrib.pop("Target")
        parts["_rels/.rels"] = etree.tostring(relationships)
    else:
        document = etree.fromstring(parts["word/document.xml"])
        for child in list(document):
            if etree.QName(child).localname == "body":
                document.remove(child)
        parts["word/document.xml"] = etree.tostring(document)
    with zipfile.ZipFile(path, "w") as package:
        for name, value in parts.items():
            package.writestr(name, value)
    metadata = client.get("/rubrics/41/document").json()
    response = client.get(
        "/rubrics/41/document/paragraphs", params={"source_sha256": metadata["source_sha256"]}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "file_malformed"
    assert str(path) not in response.text
    assert vars(row) == before


def test_timeout_keeps_native_admission_owned_until_the_reader_finishes(owned):
    _, row, _, settings, admission = owned
    settings.ingest_extraction_timeout_seconds = 0.01
    entered, release, done = threading.Event(), threading.Event(), threading.Event()

    def held_reader(_source, _settings):
        entered.set()
        try:
            release.wait(2)
            return None
        finally:
            done.set()

    async def exercise():
        from app.errors import SourceReadUnavailableError

        with pytest.raises(SourceReadUnavailableError, match="did not finish"):
            await documents.read_rubric_document(OwnedSession(row), 41, 9, settings, held_reader)
        assert entered.is_set() and admission.active_owners == 1
        with pytest.raises(DocumentPreparationBusyError):
            await documents.read_rubric_document(
                OwnedSession(row), 41, 9, settings, documents.document_metadata
            )

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        assert done.wait(2)
