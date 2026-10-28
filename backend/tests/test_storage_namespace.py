"""F1: independent test databases cannot address production object keys."""

import io
from unittest.mock import Mock

import pytest

from app.config import Settings
from app.ingest.compute_contract import NativeJob, canonical_json, digest, policy_for
from app.storage import R2Storage, get_storage, storage_key_for


def test_nonproduction_cloud_store_requires_namespace_before_connect(monkeypatch):
    monkeypatch.setattr("boto3.client", lambda *a, **kw: pytest.fail("Network client constructed"))
    settings = Settings(
        _env_file=None,
        storage_backend="r2",
        r2_bucket="test",
        r2_endpoint_url="https://example.invalid",
        r2_access_key_id="test",
        r2_secret_access_key="test",
    )
    with pytest.raises(RuntimeError, match="dedicated STORAGE_NAMESPACE"):
        get_storage(settings)


@pytest.mark.parametrize(
    "operation",
    [
        "put_file",
        "upload_stream",
        "get_bytes",
        "download_to",
        "presign_upload",
        "object_metadata",
        "delete_prefix",
        "delete",
        "exists",
    ],
)
def test_foreign_objects_never_reach_transport(operation, tmp_path):
    store = R2Storage.__new__(R2Storage)
    store._namespace, store._client = "private-qa", Mock()
    args = {
        "put_file": (tmp_path / "file", "1.extraction.json"),
        "upload_stream": (io.BytesIO(), "uploads/1.pdf"),
        "download_to": ("uploads/1.pdf", io.BytesIO()),
        "presign_upload": ("uploads/1.pdf", 1, 60),
    }.get(operation, ("uploads/1.pdf",))
    kwargs = {"chunk_bytes": 1024} if operation == "download_to" else {}
    with pytest.raises(ValueError, match="namespace"):
        getattr(store, operation)(*args, **kwargs)
    assert not store._client.mock_calls


def test_raw_and_original_share_the_environment_namespace(tmp_path):
    settings = Settings(
        _env_file=None, data_dir=tmp_path, storage_backend="r2", storage_namespace="private-qa"
    )
    for relative in ("1.extraction.json", "uploads/1.pdf"):
        assert storage_key_for(settings, str(tmp_path / relative)) == "private-qa/" + relative
    local = settings.model_copy(update={"storage_backend": "local"})
    assert storage_key_for(local, str(tmp_path / "1.extraction.json")) == "1.extraction.json"


def test_native_job_cannot_read_another_namespace():
    import time
    from uuid import uuid4

    settings = Settings(_env_file=None)
    policy = policy_for(settings)
    job = NativeJob(
        job_id=str(uuid4()),
        owner_id=1,
        source_key="uploads/1.pdf",
        source_sha256="0" * 64,
        source_bytes=1,
        expires=int(time.time()) + 10,
        policy=policy,
        policy_sha256=digest(canonical_json(policy)),
        storage_namespace="private-qa",
    )
    with pytest.raises(ValueError, match="authorized namespace"):
        job.validate_policy(settings)
    job.source_key = "private-qa/uploads/1.pdf"
    job.validate_policy(settings)
    assert job.result_prefix(settings).startswith("private-qa/native_compute/")
