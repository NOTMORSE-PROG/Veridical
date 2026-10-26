"""Complete ASGI route/header boundary with stubbed service; no DB/native/PDF."""

from types import SimpleNamespace as Row

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_instructor
from app.db import get_session
from app.errors import ConflictError, GoneError, NotFoundError, UnauthenticatedError
from app.main import app
from app.report.evidence_placement import EvidencePlacementOut
from app.report.evidence_service import EvidenceLocationOut, EvidenceManifestOut, EvidencePageOut
from app.report.evidence_source import BoundEvidenceSource

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


READER_ROUTES = (
    f"/check-runs/7/evidence/{IDENTITY}/location?snapshot_id={IDENTITY}",
    f"/check-runs/7/evidence/source-file?source_identity={IDENTITY}",
    f"/check-runs/7/evidence/source-document?source_identity={IDENTITY}",
    f"/check-runs/7/evidence/source-images/0?source_identity={IDENTITY}",
)


@pytest.mark.parametrize("path", READER_ROUTES)
@pytest.mark.parametrize("failure", [None, "foreign", "stale", "signed_out"])
def test_private_bound_reader_route_authority_and_cache(client, monkeypatch, path, failure):
    received = []

    async def result(*args):
        received.append(args)
        assert args[1:3] == (7, 9)
        if failure == "foreign":
            raise NotFoundError("Unavailable source.")
        if failure == "stale":
            raise ConflictError("Source changed.")
        if "source-images" in path:
            assert args[3:5] == (IDENTITY, 0)
            return b"verified image bytes", "image/png"
        if "/location?" in path:
            assert args[3:5] == (IDENTITY, IDENTITY)
            return EvidenceLocationOut(
                snapshot_id=IDENTITY,
                source_identity=IDENTITY,
                source_format="pdf",
                placement=EvidencePlacementOut(reference_id=IDENTITY, state="page_only", page=2),
                caption="Recorded source page; passage unmarked.",
            )
        assert args[3] == IDENTITY
        from app.report.schemas import DocumentParagraphsOut

        return BoundEvidenceSource(
            7,
            8,
            "docx" if "source-document" in path else "pdf",
            IDENTITY,
            "b" * 64,
            pdf_bytes=b"exact pinned response bytes" if "source-file" in path else None,
            document=DocumentParagraphsOut(paragraphs=[], tables=[], images=[])
            if "source-document" in path
            else None,
        )

    async def signed_out():
        raise UnauthenticatedError("Sign in.")

    monkeypatch.setattr("app.report.router.get_private_evidence_location", result)
    monkeypatch.setattr("app.report.router.get_private_evidence_reader_source", result)
    monkeypatch.setattr("app.report.router.get_private_evidence_source_image", result)
    if failure == "signed_out":
        app.dependency_overrides[get_current_instructor] = signed_out
    response = client.get(path)
    assert (
        response.status_code
        == {None: 200, "foreign": 404, "stale": 409, "signed_out": 401}[failure]
    )
    assert_policy(response, False)
    if failure == "signed_out":
        assert received == []
    elif failure is None and "source-file" in path:
        assert response.content == b"exact pinned response bytes"
        assert response.headers["x-evidence-source-identity"] == IDENTITY
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["x-content-type-options"] == "nosniff"
    elif failure is None and "source-images" in path:
        assert response.content == b"verified image bytes"
        assert response.headers["x-evidence-source-identity"] == IDENTITY
        assert response.headers["content-type"] == "image/png"
    elif failure is None:
        assert "source_sha256" not in response.text and "pdf_bytes" not in response.text


@pytest.mark.parametrize("path", READER_ROUTES)
def test_bound_reader_malformed_identity_never_reads_source(client, monkeypatch, path):
    async def forbidden(*args):
        pytest.fail("No source read for malformed identity")

    monkeypatch.setattr("app.report.router.get_private_evidence_location", forbidden)
    monkeypatch.setattr("app.report.router.get_private_evidence_reader_source", forbidden)
    monkeypatch.setattr("app.report.router.get_private_evidence_source_image", forbidden)
    response = client.get(path.replace(IDENTITY, "invalid"))
    assert response.status_code == 422
    assert_policy(response, False)


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
                caption="Page shown without a passage mark.",
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
