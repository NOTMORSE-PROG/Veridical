"""Source-bound native compute transport; large artifacts travel through private R2."""

import hashlib
import hmac
import json
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

import httpx
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings
from app.errors import ApiDownError, FileMalformedError, FileTooLargeError
from app.ingest.compute_contract import (
    SIGNATURE_HEADER,
    BoundedWriter,
    NativeJob,
    canonical_json,
    digest,
    policy_for,
    signature,
    source_artifact_prefix,
)
from app.ingest.schemas import ExtractionResult, ImageBlock
from app.storage import Storage, get_storage, storage_key_for


def _verified_download(storage: Storage, key, sha256, length, path: Path, settings: Settings):
    if type(length) is not int or length < 0:
        raise ValueError("Invalid native artifact length.")
    with path.open("wb") as target:
        storage.download_to(
            key, BoundedWriter(target, length), chunk_bytes=settings.storage_download_chunk_bytes
        )
    with path.open("rb") as reader:
        actual = hashlib.file_digest(reader, "sha256").hexdigest()
    if path.stat().st_size != length or actual != sha256:
        path.unlink(missing_ok=True)
        raise ValueError("Native artifact identity did not match.")


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".native.json")


def extract_remote(
    path: str, settings: Settings, *, owner_id: int, job_expires=None
) -> ExtractionResult:
    source = Path(path)
    with source.open("rb") as reader:
        source_sha = hashlib.file_digest(reader, "sha256").hexdigest()
    policy = policy_for(settings)
    job = NativeJob(
        job_id=str(uuid4()),
        owner_id=owner_id,
        storage_namespace=settings.storage_namespace,
        source_key=storage_key_for(settings, path),
        source_sha256=source_sha,
        source_bytes=source.stat().st_size,
        expires=job_expires
        if job_expires is not None
        else int(time.time()) + settings.native_compute_job_ttl_seconds,
        policy=policy,
        policy_sha256=digest(canonical_json(policy)),
    )
    payload = canonical_json(job.model_dump(mode="json"))
    if not settings.native_compute_secret or not settings.native_compute_url:
        raise ApiDownError("Document processing is not configured. Please retry later.")
    try:
        if settings.native_compute_align_clock:
            health = httpx.get(
                settings.native_compute_url,
                timeout=settings.native_compute_timeout_seconds,
                follow_redirects=False,
            )
            health.raise_for_status()
            remaining = job.expires - int(time.time())
            if remaining <= 0:
                raise ValueError("Native job expired before dispatch.")
            server_epoch = int(parsedate_to_datetime(health.headers["date"]).timestamp())
            job.expires = server_epoch + remaining
            payload = canonical_json(job.model_dump(mode="json"))
        response = httpx.post(
            settings.native_compute_url,
            content=payload,
            headers={
                SIGNATURE_HEADER: signature(payload, settings.native_compute_secret),
                "Content-Type": "application/json",
            },
            timeout=settings.native_compute_timeout_seconds,
            follow_redirects=False,
        )
        if (
            response.status_code != 200
            or len(response.content) > settings.native_compute_max_request_bytes
        ):
            raise ValueError("Native compute did not return a completion receipt.")
        envelope = response.json()
        receipt = envelope["receipt"]
        if not hmac.compare_digest(
            signature(canonical_json(receipt), settings.native_compute_secret),
            envelope["signature"],
        ):
            raise ValueError("Native receipt authentication failed.")
        prefix = job.result_prefix(settings)
        identity = {
            "job_id": job.job_id,
            "source_sha256": source_sha,
            "policy_sha256": job.policy_sha256,
            "version": job.version,
        }
        if any(receipt.get(key) != value for key, value in identity.items()):
            raise ValueError("Native receipt belongs to a different source or policy.")
        if receipt.get("error_code") in ("file_malformed", "too_large"):
            error_type = (
                FileMalformedError
                if receipt["error_code"] == "file_malformed"
                else FileTooLargeError
            )
            raise error_type(receipt["error_message"])
        if (
            receipt["job_id"] != job.job_id
            or receipt["source_sha256"] != source_sha
            or receipt["policy_sha256"] != job.policy_sha256
            or receipt["version"] != job.version
            or receipt["manifest_key"] != f"{prefix}/manifest.json"
            or type(receipt["manifest_bytes"]) is not int
            or not 0 < receipt["manifest_bytes"] <= settings.native_compute_max_request_bytes
        ):
            raise ValueError("Native receipt belongs to a different source or policy.")
        storage = get_storage(settings)
        try:
            with NamedTemporaryFile(dir=source.parent, delete=False) as temporary:
                manifest_path = Path(temporary.name)
            try:
                _verified_download(
                    storage,
                    receipt["manifest_key"],
                    receipt["manifest_sha256"],
                    receipt["manifest_bytes"],
                    manifest_path,
                    settings,
                )
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            finally:
                manifest_path.unlink(missing_ok=True)
            if any(
                manifest[field] != getattr(job, field)
                for field in (
                    "version",
                    "job_id",
                    "owner_id",
                    "source_key",
                    "parser_version",
                    "storage_namespace",
                    "source_sha256",
                    "policy_sha256",
                )
            ):
                raise ValueError("Native manifest does not match the authorized job.")
            artifact = manifest["extraction"]
            if (
                artifact["key"] != f"{prefix}/extraction.json"
                or type(artifact["bytes"]) is not int
                or not 0 < artifact["bytes"] <= settings.native_compute_max_result_mb * 1024 * 1024
            ):
                raise ValueError("Invalid native extraction artifact.")
            with NamedTemporaryFile(dir=source.parent, delete=False) as temporary:
                extracted_path = Path(temporary.name)
            try:
                _verified_download(
                    storage,
                    artifact["key"],
                    artifact["sha256"],
                    artifact["bytes"],
                    extracted_path,
                    settings,
                )
                result = ExtractionResult.model_validate_json(extracted_path.read_bytes())
            finally:
                extracted_path.unlink(missing_ok=True)
            # Sidecar is private, source-bound cache metadata, never the raw
            # extraction or a field exposed by public report serialization.
            _sidecar(source).write_bytes(canonical_json(manifest))
            return result
        finally:
            storage.close()
    except Exception as exc:
        if isinstance(exc, (ApiDownError, FileMalformedError, FileTooLargeError)):
            raise
        raise ApiDownError("Document processing could not finish. Please retry later.") from None


def remote_raster(path: Path, image: ImageBlock, settings: Settings, *, source_hash=None):
    try:
        return _remote_raster(path, image, settings, source_hash=source_hash)
    except (OSError, BotoCoreError, ClientError, KeyError, json.JSONDecodeError):
        raise ValueError("The native source crop could not be read.") from None


def _remote_raster(path: Path, image: ImageBlock, settings: Settings, *, source_hash=None):
    manifest = json.loads(_sidecar(path).read_text(encoding="utf-8"))
    with path.open("rb") as reader:
        actual_sha = hashlib.file_digest(reader, "sha256").hexdigest()
    if (
        manifest["source_sha256"] != actual_sha
        or source_hash is not None
        and source_hash != actual_sha
        or manifest["policy_sha256"] != digest(canonical_json(policy_for(settings)))
    ):
        raise ValueError("Native crop source has changed.")
    matches = [item for item in manifest["crops"] if item["image"] == image.model_dump(mode="json")]
    if len(matches) != 1 or "error" in matches[0]:
        raise ValueError("Native source crop is unavailable.")
    item = matches[0]
    source_prefix = source_artifact_prefix(settings, storage_key_for(settings, str(path)))
    expected_key = f"{source_prefix}{manifest['job_id']}/image-{item['image_index']}.png"
    if (
        item["key"] != expected_key
        or type(item["bytes"]) is not int
        or not 0 < item["bytes"] <= settings.native_compute_max_crop_mb * 1024 * 1024
    ):
        raise ValueError("Native source crop descriptor is invalid.")
    storage = get_storage(settings)
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            crop_path = Path(temporary.name)
        try:
            _verified_download(
                storage, item["key"], item["sha256"], item["bytes"], crop_path, settings
            )
            return crop_path.read_bytes(), item["binding"]
        finally:
            crop_path.unlink(missing_ok=True)
    finally:
        storage.close()


def delete_native_artifacts(path: Path, settings: Settings, storage=None):
    owns_storage = storage is None
    storage = storage or get_storage(settings)
    try:
        if hasattr(storage, "delete_prefix"):
            storage.delete_prefix(
                source_artifact_prefix(settings, storage_key_for(settings, str(path)))
            )
        _sidecar(path).unlink(missing_ok=True)
    finally:
        if owns_storage:
            storage.close()
