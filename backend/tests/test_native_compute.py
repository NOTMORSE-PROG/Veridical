"""Native helper source binding and actual parser/crop roundtrip, without providers."""

import hashlib
import json
import time
from email.utils import formatdate
from uuid import uuid4

import httpx
import pymupdf
import pytest

from app.config import Settings
from app.errors import ApiDownError
from app.ingest import compute_client
from app.ingest.compute_contract import (
    SIGNATURE_HEADER,
    NativeJob,
    canonical_json,
    decode_job,
    digest,
    policy_for,
    signature,
)
from app.ingest.compute_native import execute_native_job


class ObjectStore:
    def __init__(self):
        self.objects = {}

    def download_to(self, key, target, *, chunk_bytes):
        data = self.objects[key]
        for offset in range(0, len(data), chunk_bytes):
            target.write(data[offset : offset + chunk_bytes])

    def put_file(self, path, key):
        self.objects[key] = path.read_bytes()

    def close(self):
        pass


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "uploads" / "1.pdf"
    path.parent.mkdir()
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 40), "Results and analysis", fontsize=18)
        page.insert_text((40, 65), "An authored native-compute software acceptance control.")
        pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 100, 100), False)
        pix.clear_with(150)
        page.insert_image(pymupdf.Rect(50, 100, 250, 300), pixmap=pix)
        doc.save(path)
    return path


def make_job(settings, source):
    policy = policy_for(settings)
    return NativeJob(
        job_id=str(uuid4()),
        owner_id=1,
        source_key="uploads/1.pdf",
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        source_bytes=source.stat().st_size,
        expires=int(time.time()) + 120,
        policy=policy,
        policy_sha256=digest(canonical_json(policy)),
    )


def test_actual_native_extraction_and_png_roundtrip_stays_source_bound(source, monkeypatch):
    settings = Settings(
        _env_file=None,
        data_dir=source.parents[1],
        native_compute_url="https://native.example/api/native_extract",
        native_compute_secret="private-test-credential-" * 3,
    )
    store = ObjectStore()
    store.objects["uploads/1.pdf"] = source.read_bytes()

    def invoke(_url, *, content, headers, **_):
        job = decode_job(content, headers[SIGNATURE_HEADER], settings)
        receipt = execute_native_job(job, settings, store)
        return httpx.Response(
            200,
            json={
                "receipt": receipt,
                "signature": signature(canonical_json(receipt), settings.native_compute_secret),
            },
        )

    monkeypatch.setattr(compute_client.httpx, "post", invoke)
    monkeypatch.setattr(compute_client, "get_storage", lambda _: store)
    result = compute_client.extract_remote(str(source), settings, owner_id=1)
    assert result.page_count == 1 and result.text_chars > 20 and len(result.images) == 1
    png, binding = compute_client.remote_raster(source, result.images[0], settings)
    assert png.startswith(b"\x89PNG")
    assert binding["document_sha256"] == digest(source.read_bytes())
    assert binding["image_sha256"] == digest(png)
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="source has changed"):
        compute_client.remote_raster(source, result.images[0], settings)


@pytest.mark.parametrize(
    "mutation", ["signature", "expired", "policy", "source_hash", "source_length"]
)
def test_invalid_jobs_do_not_produce_a_completion_receipt(source, mutation):
    settings = Settings(_env_file=None, native_compute_secret="test-secret-" * 5)
    job = make_job(settings, source)
    store = ObjectStore()
    store.objects[job.source_key] = source.read_bytes()
    if mutation == "expired":
        job.expires = int(time.time()) - 1
    elif mutation == "policy":
        job.policy["database_url"] = "not-authorized"
        job.policy_sha256 = digest(canonical_json(job.policy))
    elif mutation == "source_hash":
        job.source_sha256 = "0" * 64
    elif mutation == "source_length":
        job.source_bytes = 1
    payload = canonical_json(job.model_dump(mode="json"))
    supplied = (
        "invalid" if mutation == "signature" else signature(payload, settings.native_compute_secret)
    )
    with pytest.raises((PermissionError, ValueError)):
        decoded = decode_job(payload, supplied, settings)
        execute_native_job(decoded, settings, store)
    assert len(store.objects) == 1


def test_policy_never_contains_database_provider_or_storage_credentials():
    settings = Settings(
        _env_file=None,
        r2_access_key_id="secret",
        r2_secret_access_key="secret",
        native_compute_secret="private",
        database_url="postgresql://private/db",
    )
    policy = policy_for(settings)
    assert "secret" not in json.dumps(policy)
    assert not set(policy) & {
        "database_url",
        "gemini_api_key",
        "r2_access_key_id",
        "native_compute_secret",
    }


@pytest.mark.parametrize("valid_date", [True, False])
def test_clock_alignment_preserves_the_original_remaining_ttl(source, monkeypatch, valid_date):
    local = [1_000_000_600]
    monkeypatch.setattr(compute_client.time, "time", lambda: local[0])
    settings = Settings(
        _env_file=None,
        data_dir=source.parents[1],
        native_compute_url="https://native.example/api/native_extract",
        native_compute_secret="private-clock-test-" * 3,
        native_compute_align_clock=True,
    )
    server_epoch = local[0] - 327
    captured = []

    def health(*_, **__):
        local[0] += 7
        return httpx.Response(
            200,
            headers={"date": formatdate(server_epoch, usegmt=True) if valid_date else "invalid"},
            request=httpx.Request("GET", settings.native_compute_url),
        )

    def post(*_, content, **__):
        captured.append(json.loads(content))
        raise httpx.ConnectError("Controlled post boundary")

    monkeypatch.setattr(compute_client.httpx, "get", health)
    monkeypatch.setattr(compute_client.httpx, "post", post)
    with pytest.raises(ApiDownError):
        compute_client.extract_remote(str(source), settings, owner_id=1)
    if valid_date:
        assert len(captured) == 1
        assert captured[0]["expires"] == server_epoch + settings.native_compute_job_ttl_seconds - 7
    else:
        assert captured == []
