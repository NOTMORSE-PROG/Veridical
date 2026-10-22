"""Durable object storage (BUG-138).

Render's free-tier disk (`settings.data_dir`) is EPHEMERAL -- wiped on every
redeploy and every 15-minute-idle wake. Nothing that must survive a redeploy
(an uploaded manuscript, its derived extraction JSON) can live there alone.

This module gives every durable read/write a single seam:

- `LocalDiskStorage` -- the dev/CI default. The store IS `data_dir`; every
  existing local run and test keeps working with zero credentials, because
  `put_file` is a no-op (the caller already wrote the file there) and
  `get_bytes` reads the same tree it would already be missing from.
- `R2Storage` -- Cloudflare R2 (S3-compatible), chosen 2026-09-02 over
  Neon-in-DB storage (0.5GB/project, already shared with the reuse archive's
  embeddings) and Render's paid persistent disk (ruled out by ground rule 2
  unless the owner says otherwise): 10GB-month free storage, free egress at
  any volume.

`data_dir` stays the LOCAL CACHE in both modes -- `ensure_local_file` below
is the one place that decides "is this already here, or do I need to fetch
it first" -- because pymupdf/python-docx need a real file on disk, not an
object-store handle.
"""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol

from app.config import Settings


class Storage(Protocol):
    """Durable object storage, keyed by a path-shaped string relative to
    `data_dir` (e.g. ``"uploads/12.pdf"``, ``"12.extraction.json"``)."""

    def put_file(self, local_path: Path, key: str) -> None: ...

    def get_bytes(self, key: str) -> bytes:
        """Raises `FileNotFoundError` if `key` doesn't exist -- the one
        contract both backends honor, so callers never branch on which
        backend is active."""
        ...

    def delete(self, key: str) -> None:
        """Missing key is not an error (same convention as `Path.unlink
        (missing_ok=True)` -- purge/cleanup callers don't need to check
        existence first)."""
        ...

    def exists(self, key: str) -> bool:
        """Metadata-only existence check; errors remain errors, not absence."""
        ...

    def close(self) -> None: ...


class LocalDiskStorage:
    """Dev/CI default: `data_dir` itself is the object store. No network
    call, no credentials -- `put_file` is deliberately a no-op because the
    caller always writes the local cache copy at `root / key` FIRST (the
    same file this would otherwise re-copy onto itself)."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def put_file(self, local_path: Path, key: str) -> None:
        del local_path, key  # already at `root / key` by construction

    def get_bytes(self, key: str) -> bytes:
        return (self._root / key).read_bytes()

    def delete(self, key: str) -> None:
        (self._root / key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        import stat

        try:
            return stat.S_ISREG((self._root / key).stat().st_mode)
        except FileNotFoundError:
            return False

    def close(self) -> None:
        pass


class R2Storage:
    """Cloudflare R2, via boto3's S3-compatible client. Credentials come
    from settings/env only (ground rule 7: no secrets in code) -- `get_storage`
    below refuses to construct this without all four."""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        client_config=None,
    ) -> None:
        import boto3

        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=client_config,
        )

    def put_file(self, local_path: Path, key: str) -> None:
        self._client.upload_file(str(local_path), self._bucket, key)

    def get_bytes(self, key: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            obj = self._client.get_object(Bucket=self._bucket, Key=key)
            return obj["Body"].read()
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchKey"):
                raise FileNotFoundError(key) from exc
            raise

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            metadata = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404 and code in ("404", "NoSuchKey", "NotFound"):
                return False
            # In particular,403 can mean missing OR denied. A missing bucket
            # is also configuration failure, not proof about this file.
            raise
        length = metadata.get("ContentLength")
        status = metadata.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status != 200 or type(length) is not int or length <= 0:
            raise ValueError("Storage metadata did not confirm a nonempty source object.")
        return True

    def close(self) -> None:
        self._client.close()


def get_storage(settings: Settings, *, metadata_probe: bool = False) -> Storage:
    if settings.storage_backend == "r2":
        if not (
            settings.r2_bucket
            and settings.r2_endpoint_url
            and settings.r2_access_key_id
            and settings.r2_secret_access_key
        ):
            raise RuntimeError(
                "STORAGE_BACKEND=r2 but R2 credentials are incomplete -- set "
                "R2_BUCKET, R2_ENDPOINT_URL, R2_ACCESS_KEY_ID and "
                "R2_SECRET_ACCESS_KEY."
            )
        client_config = None
        if metadata_probe:
            from botocore.config import Config

            client_config = Config(
                connect_timeout=settings.storage_probe_connect_timeout_seconds,
                read_timeout=settings.storage_probe_read_timeout_seconds,
                retries={"total_max_attempts": settings.storage_probe_total_attempts},
            )
        return R2Storage(
            bucket=settings.r2_bucket,
            endpoint_url=settings.r2_endpoint_url,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
            client_config=client_config,
        )
    if settings.storage_backend != "local":
        # backend review finding (BUG-138 review): this used to fall
        # through to `LocalDiskStorage` for ANY unrecognized value -- a
        # typo'd `STORAGE_BACKEND` (e.g. "R2", "cloudflare") would silently
        # deploy with zero durability while looking configured. Only the
        # two real values are legal; everything else fails loudly, the same
        # posture as the incomplete-credentials case above.
        raise RuntimeError(
            f'Unknown STORAGE_BACKEND={settings.storage_backend!r} -- must be "local" or "r2".'
        )
    return LocalDiskStorage(settings.data_dir)


def storage_key_for(settings: Settings, path_str: str) -> str:
    """The durable-storage key for a path that was built as
    `settings.data_dir / <something>` (every caller in this codebase
    constructs paths that way -- `raw_store_path`, the upload `dest` in
    `ingest_upload`). Falls back to the path's own name if it somehow isn't
    under `data_dir` rather than raising, so a future caller that doesn't
    honor that convention degrades to "a working, if oddly-namespaced, key"
    instead of a hard failure."""
    try:
        return Path(path_str).relative_to(settings.data_dir).as_posix()
    except ValueError:
        return Path(path_str).name


def ensure_local_file(settings: Settings, storage: Storage, file_ref: str) -> Path:
    """Guarantee the local cache copy at `file_ref` exists, fetching it from
    durable storage first if it doesn't -- this is what lets a manuscript
    survive Render's ephemeral-disk wipe (BUG-138): the FIRST read after a
    redeploy/idle-wake re-populates the cache from R2, transparently, and
    every read after that hits the fast local path unchanged.

    Raises `FileNotFoundError` if the object doesn't exist in durable
    storage either -- callers already handle that (the same exception the
    local-only code raised before this existed)."""
    local_path = Path(file_ref)
    if local_path.exists():
        return local_path
    key = storage_key_for(settings, file_ref)
    data = storage.get_bytes(key)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    # Source readers may run concurrently even while retention is row-locked.
    # Publish only a closed, complete file on the same filesystem; the caller's
    # source transaction still owns serialization against manuscript deletion.
    with NamedTemporaryFile(mode="wb", dir=local_path.parent, delete_on_close=False) as staged:
        staged.write(data)
        staged.close()
        try:
            # Both operations refuse an existing destination. Never replace the
            # winner while another consumer may be opening its complete bytes.
            if os.name == "nt":
                os.rename(staged.name, local_path)
            else:
                os.link(staged.name, local_path)
        except FileExistsError as publication_error:
            # Accept a competing fill only after verifying all downloaded bytes.
            try:
                already_complete = local_path.read_bytes() == data
            except OSError:
                raise publication_error from None
            if not already_complete:
                raise
    return local_path
