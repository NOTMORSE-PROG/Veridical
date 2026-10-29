"""BUG-532: a PDF with more image pieces than the server can prepare fails
honestly instead of taking the free host past its memory limit."""

from uuid import uuid4

import pytest

from app.config import Settings
from app.errors import FileTooLargeError
from app.ingest import compute_client, compute_native
from app.ingest.compute_contract import SIGNATURE_HEADER, canonical_json, decode_job, signature
from app.ingest.compute_native import execute_native_job
from tests.test_large_upload_transport import (  # noqa: F401
    _attempt,
    _process,
    live,
    scratch_url,
    wired,
)
from tests.test_native_compute import ObjectStore, make_job


def _pieces_pdf(count):
    """A real PDF made of many tiny image pieces, like a tiled scan."""
    import pymupdf

    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 40), "Chapter 1 Introduction", fontsize=16)
        for index in range(count):
            pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 4, 4), False)
            pix.clear_with(20 + index % 200)
            x, y = 40 + (index % 40) * 12, 80 + (index // 40) * 12
            page.insert_image(pymupdf.Rect(x, y, x + 8, y + 8), pixmap=pix)
        return doc.tobytes()


def _source(tmp_path, count):
    source = tmp_path / "uploads" / "1.pdf"
    source.parent.mkdir()
    source.write_bytes(_pieces_pdf(count))
    return source


def _settings(tmp_path, **values):
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        native_compute_url="https://native.example/api/native_extract",
        native_compute_secret="private-test-credential-" * 3,
        **values,
    )


def test_the_helper_refuses_before_storing_anything(tmp_path):
    source = _source(tmp_path, 30)
    settings = _settings(tmp_path, preparation_max_image_fragments=10)
    store = ObjectStore()
    store.objects["uploads/1.pdf"] = source.read_bytes()
    with pytest.raises(FileTooLargeError, match="30 small image pieces"):
        execute_native_job(make_job(settings, source), settings, store)
    assert list(store.objects) == ["uploads/1.pdf"]


def test_the_api_relays_the_helper_refusal_honestly(tmp_path, monkeypatch):
    source = _source(tmp_path, 30)
    settings = _settings(tmp_path, preparation_max_image_fragments=10)
    store = ObjectStore()
    store.objects["uploads/1.pdf"] = source.read_bytes()

    def helper(_url, *, content, headers, **_):
        import httpx

        job = decode_job(content, headers[SIGNATURE_HEADER], settings)
        try:
            receipt = execute_native_job(job, settings, store)
        except FileTooLargeError as exc:
            receipt = {
                "version": job.version,
                "job_id": job.job_id,
                "source_sha256": job.source_sha256,
                "policy_sha256": job.policy_sha256,
                "error_code": exc.code,
                "error_message": str(exc),
            }
        return httpx.Response(
            200,
            json={
                "receipt": receipt,
                "signature": signature(canonical_json(receipt), settings.native_compute_secret),
            },
        )

    monkeypatch.setattr(compute_client.httpx, "post", helper)
    monkeypatch.setattr(compute_client, "get_storage", lambda _: store)
    with pytest.raises(FileTooLargeError, match="30 small image pieces"):
        compute_client.extract_remote(str(source), settings, owner_id=1)


def test_real_theses_stay_far_below_the_default(tmp_path):
    source = _source(tmp_path, 120)
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.preparation_max_image_fragments == 5000
    from app.ingest import fragment_guard, pdf

    fragment_guard.refuse_fragment_heavy(pdf.extract_document(str(source), settings), settings)


def _upload(tc, data):
    attempt_id = tc.post(
        "/manuscripts/uploads",
        json={"attempt_id": str(uuid4()), "filename": "tiled-scan.pdf", "size": len(data)},
        headers={"X-Veridical-Upload-Transports": "stream"},
    ).json()["attempt_id"]
    response = tc.put(f"/manuscripts/uploads/{attempt_id}/body", content=data)
    assert response.status_code == 200 and response.json()["status"] == "queued", response.text
    return attempt_id


@live
@pytest.mark.parametrize("helper_checks", [True, False])
def test_a_fragment_heavy_upload_fails_honestly_and_the_server_stays_up(  # noqa: F811 -- imported pytest fixtures
    wired,  # noqa: F811 -- imported pytest fixture
    monkeypatch,
    helper_checks,
):
    monkeypatch.setenv("PREPARATION_MAX_IMAGE_FRAGMENTS", "10")
    if not helper_checks:
        # A helper deployed before its own copy of the check: ingest refuses.
        monkeypatch.setattr(compute_native, "refuse_fragment_heavy", lambda *_: None)
    tc = wired.client("relay")
    attempt_id = _upload(tc, _pieces_pdf(30))
    tc.__exit__(None, None, None)
    assert _process(wired) is True
    attempt, manuscript = _attempt(wired, attempt_id)
    assert attempt.status == "failed"
    assert attempt.error.startswith("This PDF contains 30 small image pieces")
    assert manuscript.ingest_status.value == "failed"
    assert manuscript.ingest_failure_reason.value == "file_too_large"
    assert wired.client("relay").get("/health").status_code == 200
