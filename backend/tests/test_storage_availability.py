"""V-078: metadata observation cannot hydrate sources or invent absence."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from botocore.stub import Stubber
from pydantic import ValidationError

from app.config import Settings
from app.storage import R2Storage, get_storage
from app.storage.availability import SourceObservation, probe_source


def source_path(tmp_path, manuscript_id=7):
    return tmp_path / "uploads" / f"{manuscript_id}.pdf"


def test_local_copy_needs_no_descriptor_body_read_or_remote_client(tmp_path, monkeypatch):
    path = source_path(tmp_path)
    path.parent.mkdir()
    path.write_bytes(b"source body")
    monkeypatch.setattr(Path, "read_bytes", Mock(side_effect=AssertionError("no body read")))
    monkeypatch.setattr("os.open", Mock(side_effect=AssertionError("no descriptor")))
    client = Mock(side_effect=AssertionError("no remote client"))
    monkeypatch.setattr("app.storage.availability.get_storage", client)
    assert probe_source(Settings(data_dir=tmp_path), 7, str(path)) == SourceObservation(
        "found", "local_metadata"
    )
    client.assert_not_called()


def test_definitive_local_absence_does_not_create_files(tmp_path):
    path = source_path(tmp_path)
    assert probe_source(Settings(data_dir=tmp_path), 7, str(path)).state == "missing"
    assert not path.parent.exists()


@pytest.mark.parametrize("other", ["8.pdf", "7.extraction.json"])
def test_containment_alone_cannot_probe_another_identity(tmp_path, monkeypatch, other):
    path = tmp_path / "uploads" / other
    path.parent.mkdir()
    path.write_bytes(b"private")
    opened = Mock(side_effect=AssertionError("foreign file must not open"))
    monkeypatch.setattr("os.open", opened)
    monkeypatch.setattr(Path, "resolve", Mock(side_effect=AssertionError("no foreign metadata")))
    monkeypatch.setattr(
        Path, "is_symlink", Mock(side_effect=AssertionError("no foreign link read"))
    )
    assert probe_source(Settings(data_dir=tmp_path), 7, str(path)).state == "unverified"
    opened.assert_not_called()


def test_outside_source_root_does_not_guess_basename_key(tmp_path, monkeypatch):
    storage = Mock()
    monkeypatch.setattr("app.storage.availability.get_storage", storage)
    assert (
        probe_source(Settings(data_dir=tmp_path), 7, str(tmp_path.parent / "7.pdf")).state
        == "unverified"
    )
    storage.assert_not_called()


def test_permissions_and_nonregular_files_are_not_absence(tmp_path, monkeypatch):
    path = source_path(tmp_path)
    path.mkdir(parents=True)
    settings = Settings(data_dir=tmp_path)
    assert probe_source(settings, 7, str(path)).state == "unverified"
    path.rmdir()
    path.write_bytes(b"source")
    monkeypatch.setattr(Path, "lstat", Mock(side_effect=PermissionError("secret path")))
    assert probe_source(settings, 7, str(path)).state == "unverified"


@pytest.mark.parametrize("present", [True, False])
def test_durable_metadata_only_does_not_refill_local_copy(tmp_path, monkeypatch, present):
    path = source_path(tmp_path)
    settings = Settings(data_dir=tmp_path, storage_backend="r2")
    storage = Mock()
    storage.exists.return_value = present
    factory = Mock(return_value=storage)
    monkeypatch.setattr("app.storage.availability.get_storage", factory)
    result = probe_source(settings, 7, str(path))
    assert (
        result == SourceObservation("found", "object_metadata")
        if present
        else result.state == "missing"
    )
    factory.assert_called_once_with(settings, metadata_probe=True)
    storage.exists.assert_called_once_with("uploads/7.pdf")
    storage.get_bytes.assert_not_called()
    storage.close.assert_called_once()
    assert not path.exists()


@pytest.mark.parametrize(
    "error", [PermissionError("denied"), TimeoutError("network"), RuntimeError("config")]
)
def test_provider_failures_are_unverified_and_do_not_leak_exception(tmp_path, monkeypatch, error):
    monkeypatch.setattr("app.storage.availability.get_storage", Mock(side_effect=error))
    result = probe_source(
        Settings(data_dir=tmp_path, storage_backend="r2"), 7, str(source_path(tmp_path))
    )
    assert result == SourceObservation("unverified")


def r2():
    return R2Storage(
        bucket="unit-bucket",
        endpoint_url="https://storage.invalid",
        access_key_id="unit",
        secret_access_key="unit",
    )


@pytest.mark.parametrize(
    "code,status,missing",
    [
        ("404", 404, True),
        ("NoSuchKey", 404, True),
        ("NotFound", 404, True),
        ("AccessDenied", 403, False),
        ("404", 403, False),
        ("NoSuchBucket", 404, False),
        ("InternalError", 500, False),
    ],
)
def test_head_error_contract_with_real_sdk_stubber(code, status, missing):
    storage = r2()
    with Stubber(storage._client) as stub:
        stub.add_client_error(
            "head_object",
            service_error_code=code,
            http_status_code=status,
            expected_params={"Bucket": "unit-bucket", "Key": "uploads/7.pdf"},
        )
        if missing:
            assert storage.exists("uploads/7.pdf") is False
        else:
            with pytest.raises(ClientError):
                storage.exists("uploads/7.pdf")
    storage.close()


def test_successful_head_uses_no_get_operation():
    storage = r2()
    with Stubber(storage._client) as stub:
        stub.add_response(
            "head_object",
            {"ContentLength": 12, "ResponseMetadata": {"HTTPStatusCode": 200}},
            {"Bucket": "unit-bucket", "Key": "uploads/7.pdf"},
        )
        assert storage.exists("uploads/7.pdf") is True
    storage.close()


def test_head_timeout_stays_unknown(tmp_path, monkeypatch):
    storage = Mock()
    storage.exists.side_effect = ReadTimeoutError(endpoint_url="https://storage.invalid")
    monkeypatch.setattr("app.storage.availability.get_storage", Mock(return_value=storage))
    assert (
        probe_source(
            Settings(data_dir=tmp_path, storage_backend="r2"), 7, str(source_path(tmp_path))
        ).state
        == "unverified"
    )
    storage.close.assert_called_once()


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"ContentLength": 0},
        {"ContentLength": True},
        {"ContentLength": "12"},
        {"ContentLength": 12},
        {"ContentLength": 12, "ResponseMetadata": {"HTTPStatusCode": 503}},
    ],
)
def test_incomplete_or_empty_head_success_is_not_positive_proof(metadata):
    storage = r2()
    storage._client.head_object = Mock(return_value=metadata)
    with pytest.raises(ValueError, match="did not confirm"):
        storage.exists("uploads/7.pdf")
    storage.close()


def test_empty_local_cache_is_unknown_without_a_remote_request(tmp_path, monkeypatch):
    path = source_path(tmp_path)
    path.parent.mkdir()
    path.touch()
    remote = Mock(side_effect=AssertionError("bad local cache must not be hidden"))
    monkeypatch.setattr("app.storage.availability.get_storage", remote)
    assert (
        probe_source(Settings(data_dir=tmp_path, storage_backend="r2"), 7, str(path)).state
        == "unverified"
    )
    remote.assert_not_called()


def test_probe_client_uses_separate_transport_budget():
    settings = Settings(
        storage_backend="r2",
        r2_bucket="unit-bucket",
        r2_endpoint_url="https://storage.invalid",
        r2_access_key_id="unit",
        r2_secret_access_key="unit",
        storage_probe_connect_timeout_seconds=1.5,
        storage_probe_read_timeout_seconds=2.5,
    )
    storage = get_storage(settings, metadata_probe=True)
    assert storage._client.meta.config.connect_timeout == 1.5
    assert storage._client.meta.config.read_timeout == 2.5
    assert storage._client.meta.config.retries["total_max_attempts"] == 1
    storage.close()


@pytest.mark.parametrize("subdir", ["", ".", "..", "../uploads", "C:\\outside", "uploads/next"])
def test_upload_directory_config_rejects_escape(subdir):
    with pytest.raises(ValidationError):
        Settings(manuscript_upload_subdir=subdir)
