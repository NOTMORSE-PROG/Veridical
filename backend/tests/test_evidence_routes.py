"""Complete ASGI route/header boundary with stubbed service; no DB/native/PDF."""

from types import SimpleNamespace as Row

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_instructor
from app.db import get_session
from app.errors import ConflictError, GoneError, NotFoundError, UnauthenticatedError
from app.main import app
from app.report.evidence_placement import EvidencePlacementOut
from app.report.evidence_service import EvidenceManifestOut, EvidencePageOut

IDENTITY = "a" * 64
ROUTES = (
    ("/check-runs/7/evidence", "app.report.router.get_evidence_manifest", False, False),
    (
        f"/check-runs/7/evidence/{IDENTITY}/page?snapshot_id={IDENTITY}",
        "app.report.router.get_evidence_page",
        False,
        True,
    ),
    ("/shared/local-token/evidence", "app.share.router.get_evidence_manifest", True, False),
    (
        f"/shared/local-token/evidence/{IDENTITY}/page?snapshot_id={IDENTITY}",
        "app.share.router.get_evidence_page",
        True,
        True,
    ),
)


@pytest.fixture
def client(monkeypatch):
    saved = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = lambda: object()
    app.dependency_overrides[get_current_instructor] = lambda: Row(id=9)
    monkeypatch.setattr("app.report.router.enforce_action_rate_limit", lambda *args: None)
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)


def assert_policy(response, public):
    assert response.headers["cache-control"] == ("no-store" if public else "private, no-store")
    if public:
        assert response.headers["x-robots-tag"] == "noindex, nofollow"


@pytest.mark.parametrize("path,target,public,page", ROUTES)
def test_reading_routes_keep_allowlisted_payload_and_authority(
    client, monkeypatch, path, target, public, page
):
    received = []

    async def success(*args, **kwargs):
        received.append((args, kwargs))
        if page:
            return EvidencePageOut(
                snapshot_id=IDENTITY,
                placement=EvidencePlacementOut(reference_id=IDENTITY, state="page_only", page=2),
                image_data_url=None,
            )
        return EvidenceManifestOut(
            snapshot_id=IDENTITY, assessment_revision=3, llm_mode="real", references=()
        )

    monkeypatch.setattr(target, success)
    response = client.get(path)
    assert response.status_code == 200 and response.json()["snapshot_id"] == IDENTITY
    assert_policy(response, public)
    authority = received[0][1]
    assert authority == (
        {"token": "local-token"} if public else {"check_run_id": 7, "instructor_id": 9}
    )


@pytest.mark.parametrize("path,target,public,page", ROUTES)
@pytest.mark.parametrize(
    "error,status", [(NotFoundError, 404), (GoneError, 410), (ConflictError, 409)]
)
def test_handled_route_failures_keep_source_cache_policy(
    client, monkeypatch, path, target, public, page, error, status
):
    async def denied(*args, **kwargs):
        raise error("Evidence unavailable.")

    monkeypatch.setattr(target, denied)
    response = client.get(path)
    assert response.status_code == status
    assert response.json()["error"]["message"] == "Evidence unavailable."
    assert_policy(response, public)


@pytest.mark.parametrize("path,target,public,page", ROUTES[:2])
def test_signed_out_private_reading_never_calls_service(
    client, monkeypatch, path, target, public, page
):
    async def signed_out():
        raise UnauthenticatedError("Not signed in.")

    async def forbidden(*args, **kwargs):
        raise AssertionError("No source service without instructor")

    app.dependency_overrides[get_current_instructor] = signed_out
    monkeypatch.setattr(target, forbidden)
    response = client.get(path)
    assert response.status_code == 401
    assert_policy(response, False)


@pytest.mark.parametrize("path,target,public,page", ROUTES[2:])
def test_public_unexpected_failure_is_generic_and_uncacheable(
    client, monkeypatch, path, target, public, page
):
    async def failed(*args, **kwargs):
        raise RuntimeError("PRIVATE_NATIVE_PATH")

    monkeypatch.setattr(target, failed)
    response = client.get(path)
    assert response.status_code == 500 and response.text == "Internal Server Error"
    assert_policy(response, True)


@pytest.mark.parametrize("prefix,public", [("/check-runs/7", False), ("/shared/local-token", True)])
def test_non_reference_location_and_malformed_manifest_are_validation_errors(
    client, prefix, public
):
    for ending in (
        "/evidence/page-2/page?snapshot_id=" + IDENTITY,
        "/evidence/" + IDENTITY + "/page?snapshot_id=invalid",
    ):
        response = client.get(prefix + ending)
        assert response.status_code == 422
        assert_policy(response, public)
