"""Signed source-bound native-compute jobs; no credentials or manuscript payloads."""

import hashlib
import hmac
import json
import time
from importlib.metadata import version as package_version
from pathlib import PurePosixPath
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import Settings

SIGNATURE_HEADER = "X-Veridical-Compute-Signature"


class BoundedWriter:
    def __init__(self, target, limit):
        self.target, self.limit, self.written = target, limit, 0

    def write(self, chunk):
        if self.written + len(chunk) > self.limit:
            raise ValueError("Native artifact exceeds its authorized length.")
        count = self.target.write(chunk)
        self.written += count
        return count


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def source_artifact_prefix(settings: Settings, source_key: str, *, namespace=None) -> str:
    namespace = settings.storage_namespace if namespace is None else namespace
    base = f"{namespace}/" if namespace else ""
    return f"{base}{settings.native_compute_results_subdir}/{digest(source_key.encode())}/"


def signature(payload: bytes, secret: str) -> str:
    if len(secret) < 32:
        raise ValueError("Native compute requires a strong shared credential.")
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def policy_for(settings: Settings) -> dict[str, Any]:
    # Preserve parser settings without forwarding API/DB/provider credentials.
    keys = [
        key for key in Settings.model_fields if key.startswith(("ingest_", "docx_", "max_docx_"))
    ]
    values = settings.model_dump(mode="json", include=set(keys))
    # Filesystem resources must come from the deployed parser package.
    for key in tuple(values):
        if key.endswith(("_file", "_dir", "_python")):
            if values[key] is not None:
                raise ValueError(
                    "Custom native parser resources require a matching helper deployment."
                )
            values.pop(key)
    return values


class NativeJob(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal["native-v1"] = "native-v1"
    parser_version: str = Field(default_factory=lambda: package_version("pymupdf"))
    storage_namespace: str = ""
    job_id: str
    owner_id: int = Field(gt=0)
    source_key: str = Field(min_length=1, max_length=1024)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_bytes: int = Field(gt=0)
    expires: int
    policy: dict[str, Any]
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("job_id")
    @classmethod
    def uuid_job(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("Noncanonical native job identity.")
        return value

    @field_validator("source_key")
    @classmethod
    def relative_key(cls, value):
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or str(path) != value
            or ".." in path.parts
            or "\\" in value
            or ":" in value
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("Invalid native source key.")
        return value

    def validate_policy(self, settings: Settings) -> Settings:
        if self.storage_namespace:
            Settings(_env_file=None, storage_namespace=self.storage_namespace)
            if not self.source_key.startswith(self.storage_namespace + "/"):
                raise ValueError("Native source is outside the authorized namespace.")
        if self.parser_version != package_version("pymupdf"):
            raise ValueError("Native parser versions do not match.")
        if (
            self.expires < time.time()
            or self.expires > time.time() + settings.native_compute_job_ttl_seconds
        ):
            raise ValueError("Native job has expired or has an invalid lifetime.")
        if self.source_bytes > settings.native_compute_max_source_mb * 1024 * 1024:
            raise ValueError("Native source exceeds the helper limit.")
        if self.policy_sha256 != digest(canonical_json(self.policy)):
            raise ValueError("Native extraction policy does not match.")
        allowed = set(policy_for(Settings(_env_file=None)))
        if set(self.policy) - allowed:
            raise ValueError("Unsupported native policy field.")
        return Settings(_env_file=None, **self.policy)

    def result_prefix(self, settings: Settings) -> str:
        prefix = source_artifact_prefix(
            settings, self.source_key, namespace=self.storage_namespace
        )
        return f"{prefix}{self.job_id}"


def decode_job(payload: bytes, supplied_signature: str, settings: Settings) -> NativeJob:
    if len(payload) > settings.native_compute_max_request_bytes:
        raise ValueError("Native job descriptor is too large.")
    if not settings.native_compute_secret or not hmac.compare_digest(
        signature(payload, settings.native_compute_secret), supplied_signature
    ):
        raise PermissionError("Native job authentication failed.")
    job = NativeJob.model_validate_json(payload)
    job.validate_policy(settings)
    return job
