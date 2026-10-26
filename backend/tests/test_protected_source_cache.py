"""F1/F2/F9: source caching policy also reaches handled error responses."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_instructor
from app.db import get_session
from app.errors import GoneError, NotFoundError, UnauthenticatedError
from app.main import app
from tests.test_ingest_fixtures import FIXTURE_DIR

_HASH = "a" * 64
_SOURCE_ROUTES = (
    ("/check-runs/7/document", "app.report.router.get_manuscript_viewer"),
    ("/check-runs/7/document/reuse-matches", "app.report.router.list_reuse_passage_matches"),
    ("/check-runs/7/document/paragraphs", "app.report.router.get_manuscript_paragraphs"),
    ("/check-runs/7/document/page-text?page=1", "app.report.router.get_manuscript_page_text"),
    ("/check-runs/7/document/images/0", "app.report.router.get_manuscript_docx_image"),
    ("/check-runs/7/document/file", "app.report.router.get_manuscript_file_path"),
    ("/rubrics/7/document", "app.rubric.router.read_rubric_document"),
    (
        f"/rubrics/7/document/paragraphs?source_sha256={_HASH}",
        "app.rubric.router.read_rubric_document",
    ),
    (f"/rubrics/7/document/file?source_sha256={_HASH}", "app.rubric.router.read_rubric_document"),
    (
        f"/rubrics/7/document/original?source_sha256={_HASH}",
        "app.rubric.router.read_rubric_document",
    ),
    (
        f"/rubrics/7/document/images/0?source_sha256={_HASH}",
        "app.rubric.router.read_rubric_document",
    ),
)


@pytest.fixture
def source_client():
    """Exercise the complete app middleware/router/typed-error boundary in RAM."""
    saved = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_instructor] = lambda: SimpleNamespace(id=9)
    app.dependency_overrides[get_session] = lambda: object()
    try:
        # No context manager: this matrix needs no application lifespan or SQL.
        with_client = TestClient(app)
        try:
            yield with_client
        finally:
            with_client.close()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)


@pytest.mark.parametrize("path,service", _SOURCE_ROUTES)
@pytest.mark.parametrize(
    "exception,status,code", [(NotFoundError, 404, "not_found"), (GoneError, 410, "gone")]
)
def test_handled_source_errors_are_private_no_store(
    source_client, monkeypatch, path, service, exception, status, code
):
    async def unavailable(*args, **kwargs):
        raise exception("Protected source unavailable.")

    monkeypatch.setattr(service, unavailable)
    response = source_client.get(path)
    assert response.status_code == status
    assert response.json() == {"error": {"code": code, "message": "Protected source unavailable."}}
    assert response.headers.get("cache-control") == "private, no-store"
    assert "x-robots-tag" not in response.headers


@pytest.mark.parametrize("path,_service", _SOURCE_ROUTES)
def test_source_authentication_errors_are_private_no_store(source_client, path, _service):
    async def signed_out():
        raise UnauthenticatedError("Not signed in.")

    app.dependency_overrides[get_current_instructor] = signed_out
    response = source_client.get(path)
    assert response.status_code == 401
    assert response.json() == {"error": {"code": "unauthenticated", "message": "Not signed in."}}
    assert response.headers.get("cache-control") == "private, no-store"
    assert "x-robots-tag" not in response.headers


@pytest.mark.parametrize(
    "path",
    [
        "/check-runs/7/document/page-text?page=0",
        "/check-runs/7/document/images/-1",
        "/rubrics/7/document/file?source_sha256=invalid",
        "/rubrics/7/document/images/-1?source_sha256=" + _HASH,
    ],
)
def test_source_validation_errors_are_private_no_store(source_client, path):
    response = source_client.get(path)
    assert response.status_code == 422
    assert response.json()["detail"]
    assert response.headers.get("cache-control") == "private, no-store"
    assert "x-robots-tag" not in response.headers


@pytest.mark.parametrize(
    "path,service",
    [
        ("/check-runs/7/document/file", "app.report.router.get_manuscript_file_path"),
        (
            f"/rubrics/7/document/file?source_sha256={_HASH}",
            "app.rubric.router.read_rubric_document",
        ),
        (
            f"/rubrics/7/document/original?source_sha256={_HASH}",
            "app.rubric.router.read_rubric_document",
        ),
    ],
)
def test_existing_source_file_success_preserves_bytes_and_headers(
    source_client, monkeypatch, path, service
):
    native = FIXTURE_DIR / "native.pdf"

    async def existing(*args, **kwargs):
        return native if "check-runs" in path else (native, "application/pdf", "native.pdf")

    monkeypatch.setattr(service, existing)
    response = source_client.get(path)
    assert response.status_code == 200
    assert response.content == native.read_bytes()
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "x-robots-tag" not in response.headers


@pytest.mark.parametrize(
    "path,service",
    [
        ("/check-runs/7/report", "app.report.router.get_report"),
        ("/rubrics/7", "app.rubric.router.get_rubric"),
    ],
)
def test_unrelated_sibling_error_cache_policy_is_unchanged(
    source_client, monkeypatch, path, service
):
    async def missing(*args, **kwargs):
        raise NotFoundError("Unrelated record missing.")

    monkeypatch.setattr(service, missing)
    response = source_client.get(path)
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Unrelated record missing."}
    }
    assert "cache-control" not in response.headers
    assert "x-robots-tag" not in response.headers


def test_unmatched_source_like_path_does_not_get_document_policy(source_client):
    response = source_client.get("/check-runs/7/document/not-a-source-route")
    assert response.status_code == 404
    assert "cache-control" not in response.headers


@pytest.mark.parametrize(
    "path,service,payload",
    [
        (
            "/check-runs/7/document",
            "app.report.router.get_manuscript_viewer",
            {
                "manuscript_id": 3,
                "original_filename": "source.docx",
                "source_format": "docx",
                "available": True,
                "unavailable_reason": None,
                "purged_at": None,
                "page_count": None,
                "regions": [],
            },
        ),
        (
            "/check-runs/7/document/reuse-matches",
            "app.report.router.list_reuse_passage_matches",
            {
                "passage_archive_size_n": 0,
                "matches": [],
            },
        ),
        (
            "/check-runs/7/document/paragraphs",
            "app.report.router.get_manuscript_paragraphs",
            {
                "paragraphs": [
                    {"paragraph": 0, "text": "Private source text.", "heading_level": None}
                ],
            },
        ),
    ],
)
def test_successful_source_json_is_private_no_store(
    source_client, monkeypatch, path, service, payload
):
    async def available(*args, **kwargs):
        return payload

    monkeypatch.setattr(service, available)
    response = source_client.get(path)
    assert response.status_code == 200
    for key, value in payload.items():
        assert response.json()[key] == value
    assert response.headers["cache-control"] == "private, no-store"
    assert "x-robots-tag" not in response.headers


@pytest.mark.parametrize("path,service", [_SOURCE_ROUTES[5], _SOURCE_ROUTES[8]])
def test_unexpected_private_source_failure_keeps_propagating(
    source_client, monkeypatch, path, service
):
    async def broken(*args, **kwargs):
        raise RuntimeError("Source reader failed unexpectedly.")

    monkeypatch.setattr(service, broken)
    with pytest.raises(RuntimeError, match="Source reader failed unexpectedly"):
        source_client.get(path)
