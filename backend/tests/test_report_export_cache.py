"""F8.6/F9: generated private report response policy, with existing PDF bytes."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.ratelimit as ratelimit
import app.report.router as report_router
from app.auth.dependencies import get_current_instructor
from app.db import get_session
from app.errors import ConflictError, NotFoundError, RateLimitedError, UnauthenticatedError
from app.main import app
from tests.test_ingest_fixtures import FIXTURE_DIR


@pytest.fixture
def export_client(monkeypatch):
    saved = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_instructor] = lambda: SimpleNamespace(id=9)
    app.dependency_overrides[get_session] = lambda: object()
    ratelimit._limiters.clear()
    state = {"assembly": [], "builder": []}
    data = object()
    original = (FIXTURE_DIR / "native.pdf").read_bytes()

    async def assemble(session, check_run_id, instructor_id):
        state["assembly"].append((check_run_id, instructor_id))
        return data

    def existing_bytes(actual):
        assert actual is data
        state["builder"].append(actual)
        return original

    monkeypatch.setattr(report_router, "get_report_export_data", assemble)
    monkeypatch.setattr(report_router, "build_report_pdf", existing_bytes)
    client = TestClient(app)
    try:
        yield client, state, original
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)
        ratelimit._limiters.clear()


def assert_private_export_policy(response):
    assert response.headers.get("cache-control") == "private, no-store"
    assert "x-robots-tag" not in response.headers


def test_owned_export_keeps_renderer_bytes_media_and_attachment(export_client):
    client, state, original = export_client
    response = client.get("/check-runs/7/report/export.pdf")
    assert response.status_code == 200
    assert response.content == original
    assert response.headers["content-type"] == "application/pdf"
    assert (
        response.headers["content-disposition"] == 'attachment; filename="veridical-report-7.pdf"'
    )
    assert state["assembly"] == [(7, 9)]
    assert len(state["builder"]) == 1
    assert_private_export_policy(response)


@pytest.mark.parametrize(
    "error,status,code",
    [(NotFoundError, 404, "not_found"), (ConflictError, 409, "conflict")],
)
def test_export_handled_errors_keep_envelope_and_private_policy(
    export_client, monkeypatch, error, status, code
):
    client, state, _ = export_client

    async def unavailable(*args, **kwargs):
        raise error("Report unavailable.")

    monkeypatch.setattr(report_router, "get_report_export_data", unavailable)
    response = client.get("/check-runs/7/report/export.pdf")
    assert response.status_code == status
    assert response.json() == {"error": {"code": code, "message": "Report unavailable."}}
    assert state["builder"] == []
    assert_private_export_policy(response)


def test_export_rate_limit_remains_before_assembly_and_builder(export_client, monkeypatch):
    client, state, _ = export_client
    seen = []

    def limited(settings, scope, instructor_id):
        seen.append((scope, instructor_id))
        raise RateLimitedError("Too many requests, please wait a bit before trying again.")

    monkeypatch.setattr(report_router, "enforce_action_rate_limit", limited)
    response = client.get("/check-runs/7/report/export.pdf")
    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": "rate_limited",
            "message": "Too many requests, please wait a bit before trying again.",
        }
    }
    assert seen == [("report_export", 9)]
    assert state["assembly"] == state["builder"] == []
    assert_private_export_policy(response)


def test_export_authentication_failure_keeps_envelope_and_stops_work(export_client):
    client, state, _ = export_client

    async def signed_out():
        raise UnauthenticatedError("Not signed in.")

    app.dependency_overrides[get_current_instructor] = signed_out
    response = client.get("/check-runs/7/report/export.pdf")
    assert response.status_code == 401
    assert response.json() == {"error": {"code": "unauthenticated", "message": "Not signed in."}}
    assert state["assembly"] == state["builder"] == []
    assert_private_export_policy(response)


def test_export_validation_rejects_before_assembly_and_builder(export_client):
    client, state, _ = export_client
    response = client.get("/check-runs/not-an-integer/report/export.pdf")
    assert response.status_code == 422
    assert response.json()["detail"]
    assert state["assembly"] == state["builder"] == []
    assert_private_export_policy(response)


def test_unexpected_export_failure_keeps_propagating(export_client, monkeypatch):
    client, state, _ = export_client

    def broken(_data):
        raise RuntimeError("Unexpected renderer failure.")

    monkeypatch.setattr(report_router, "build_report_pdf", broken)
    with pytest.raises(RuntimeError, match="Unexpected renderer failure"):
        client.get("/check-runs/7/report/export.pdf")
    assert state["assembly"] == [(7, 9)]


def test_unrelated_report_read_keeps_existing_cache_policy(export_client, monkeypatch):
    client, _, _ = export_client

    async def missing(*args, **kwargs):
        raise NotFoundError("No check run with id 7.")

    monkeypatch.setattr(report_router, "get_report", missing)
    response = client.get("/check-runs/7/report")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "cache-control" not in response.headers


def test_unmatched_export_like_path_does_not_get_private_policy(export_client):
    client, _, _ = export_client
    response = client.get("/check-runs/7/report/export.pdf/other")
    assert response.status_code == 404
    assert "cache-control" not in response.headers
