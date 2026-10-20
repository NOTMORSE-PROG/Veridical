"""F1 bounded optional layout recovery; failures preserve the native extraction."""

import hashlib
import json
import os
import tempfile
import time
from contextlib import suppress
from pathlib import Path

import psutil
import pymupdf
from filelock import FileLock, Timeout
from pydantic import ValidationError

from app.config import Settings
from app.ingest.recovery_mapping import (
    MappingUnavailable,
    digest_json,
    map_layout_reply,
    native_fingerprint,
)
from app.ingest.recovery_models import LayoutReply, RecoveryEnvelope, RecoveryPage
from app.ingest.recovery_process import managed_worker
from app.ingest.recovery_worker import file_digest
from app.ingest.schemas import ExtractionResult


def selected_pages(extraction: ExtractionResult) -> list[int]:
    """Only the demonstrated table-region failure; never a whole-text swap."""
    return sorted(
        record.page
        for record in extraction.reading_order or []
        if record.status == "unresolved" and record.reason == "table_regions_unresolved"
    )


def worker_environment(temp_dir: Path, threads: int) -> dict[str, str]:
    # Explicit OS/runtime allowlist. No DATABASE_URL, provider/storage keys,
    # proxy credentials, PYTHONPATH, user packages or dotenv configuration.
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(
        {
            "TEMP": str(temp_dir),
            "TMP": str(temp_dir),
            # Windows pathlib.home() requires this even when models are local.
            # Only the child receives a private job profile, never the host's.
            "USERPROFILE": str(temp_dir),
            "XDG_CACHE_HOME": str(temp_dir / "cache"),
            "HF_HOME": str(temp_dir / "hf"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "OMP_NUM_THREADS": str(threads),
            "MKL_NUM_THREADS": str(threads),
            "PYTHONUTF8": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return env


def _run_worker(
    job: dict, directory: Path, settings: Settings
) -> tuple[LayoutReply | None, str | None, int, float]:
    request_path = directory / "request.json"
    output_path = directory / "reply.json"
    request_path.write_text(json.dumps(job), encoding="utf-8")
    arguments = [
        "--job",
        str(request_path),
        "--output",
        str(output_path),
    ]
    started = time.monotonic()
    peak = 0
    reason = None
    with managed_worker(
        settings.ingest_recovery_python,
        Path(__file__).with_name("recovery_worker.py"),
        arguments,
        directory=directory,
        environment=worker_environment(directory, settings.ingest_recovery_threads),
        cleanup_timeout=settings.ingest_recovery_cleanup_timeout_seconds,
        poll=settings.ingest_recovery_poll_seconds,
    ) as process:
        try:
            watched = psutil.Process(process.pid)
            while process.poll() is None:
                try:
                    children = watched.children(recursive=True)
                    rss = 0
                    for item in [watched, *children]:
                        with suppress(psutil.NoSuchProcess):
                            rss += item.memory_info().rss
                    peak = max(peak, rss)
                    if rss > settings.ingest_recovery_memory_mb * 1024**2:
                        reason = "worker_memory_limit"
                    elif time.monotonic() - started > settings.ingest_recovery_timeout_seconds:
                        reason = "worker_time_limit"
                    elif (
                        output_path.exists()
                        and output_path.stat().st_size > job["max_output_bytes"]
                    ):
                        reason = "worker_output_limit"
                    if reason:
                        break
                except psutil.NoSuchProcess:
                    break
                time.sleep(settings.ingest_recovery_poll_seconds)
        except psutil.NoSuchProcess:
            pass
    elapsed = time.monotonic() - started
    if reason:
        return None, reason, peak, elapsed
    if process.returncode != 0 or not output_path.is_file():
        return None, "worker_unavailable", peak, elapsed
    if output_path.stat().st_size > job["max_output_bytes"]:
        return None, "worker_output_limit", peak, elapsed
    try:
        reply = LayoutReply.model_validate_json(output_path.read_bytes())
    except (ValidationError, ValueError):
        return None, "worker_output_invalid", peak, elapsed
    return reply, None, peak, elapsed


def _failure(envelope: RecoveryEnvelope, reason: str) -> RecoveryEnvelope:
    return envelope.model_copy(
        update={
            "pages": [
                RecoveryPage(page=page, status="unavailable", reason=reason)
                for page in envelope.requested_pages
            ]
        }
    )


def _recover_layout(
    extraction: ExtractionResult,
    source: Path,
    settings: Settings,
    previous: RecoveryEnvelope | None = None,
) -> ExtractionResult:
    """Add validated proposals only; re-opening a saved report never calls this."""
    pages = selected_pages(extraction)
    if not pages or settings.ingest_recovery_python is None:
        return extraction
    manifest_path = settings.ingest_recovery_manifest_file or (
        Path(__file__).with_name("data") / "recovery_models.json"
    )
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    source_hash = file_digest(source, settings.content_hash_read_chunk_bytes)
    native_hash = native_fingerprint(extraction)
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    recipe = {
        "source": source_hash,
        "native": native_hash,
        "manifest": manifest_hash,
        "worker": file_digest(
            Path(__file__).with_name("recovery_worker.py"), settings.content_hash_read_chunk_bytes
        ),
        "lifecycle": file_digest(
            Path(__file__).with_name("recovery_process.py"), settings.content_hash_read_chunk_bytes
        ),
        "adapter": file_digest(
            Path(__file__).with_name("recovery_mapping.py"), settings.content_hash_read_chunk_bytes
        ),
        "pages": pages,
        "options": {
            key: value
            for key, value in settings.model_dump(mode="json").items()
            if key.startswith(("ingest_recovery_", "ingest_reading_order_"))
            and not key.endswith(("_python", "_dir", "_file"))
        },
    }
    envelope = RecoveryEnvelope(
        source_sha256=source_hash,
        native_sha256=native_hash,
        request_sha256=digest_json(recipe),
        manifest_sha256=manifest_hash,
        requested_pages=pages,
        pages=[],
    )
    prior = previous or extraction.recovery
    if (
        prior is not None
        and prior.request_sha256 == envelope.request_sha256
        and prior.source_sha256 == source_hash
        and prior.native_sha256 == native_hash
        and prior.manifest_sha256 == manifest_hash
        and prior.versions == manifest["packages"]
        and prior.proposal is not None
        and prior.proposal.versions == manifest["packages"]
        and [page.page for page in prior.proposal.pages] == pages
    ):
        # Saved mapping/status fields are not proof. Re-run all source,
        # topology and geometry checks against the current canonical ledger.
        try:
            envelope.peak_process_rss_bytes = prior.peak_process_rss_bytes
            envelope.elapsed_seconds = prior.elapsed_seconds
            extraction.recovery = map_layout_reply(extraction, envelope, prior.proposal, settings)
            return extraction
        except MappingUnavailable:
            # A damaged cache can only lose reuse, never grant source validity.
            pass
    if len(pages) > settings.ingest_recovery_max_pages:
        extraction.recovery = _failure(envelope, "recovery_page_limit")
        return extraction
    interpreter = settings.ingest_recovery_python
    models_dir = settings.ingest_recovery_models_dir
    if (
        not interpreter.is_absolute()
        or not interpreter.is_file()
        or models_dir is None
        or not models_dir.is_absolute()
        or not models_dir.is_dir()
    ):
        extraction.recovery = _failure(envelope, "worker_not_configured")
        return extraction
    with pymupdf.open(source) as document:
        if document.page_count != extraction.page_count or any(
            document[page - 1].rotation != 0
            or document[page - 1].cropbox != document[page - 1].mediabox
            for page in pages
        ):
            extraction.recovery = _failure(envelope, "unsupported_page_transform")
            return extraction
    # Existing data_dir remains the private local cache even with R2 storage.
    work_root = (settings.data_dir / ".layout-worker").resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    try:
        # OS-backed cross-process lock, no stale PID file or accumulating queue.
        with (
            FileLock(work_root / "worker.lock", timeout=0),
            tempfile.TemporaryDirectory(prefix="job-", dir=work_root) as temp,
        ):
            directory = Path(temp).resolve()
            if not directory.is_relative_to(work_root):
                raise ValueError("Worker directory escaped its private root")
            job = {
                "source": str(source.resolve()),
                "source_sha256": source_hash,
                "request_sha256": envelope.request_sha256,
                "manifest": str(manifest_path.resolve()),
                "manifest_sha256": manifest_hash,
                "models_dir": str(models_dir.resolve()),
                "hash_chunk_bytes": settings.content_hash_read_chunk_bytes,
                "max_output_bytes": settings.ingest_recovery_max_output_mb * 1024**2,
                "threads": settings.ingest_recovery_threads,
                "pages": pages,
                "geometry": {
                    str(g.page): {"width": g.width, "height": g.height}
                    for g in extraction.geometry
                    if g.page in pages
                },
            }
            reply, reason, peak, elapsed = _run_worker(job, directory, settings)
        envelope.peak_process_rss_bytes = peak
        envelope.elapsed_seconds = elapsed
        if reason:
            extraction.recovery = _failure(envelope, reason)
        elif reply is None or reply.versions != manifest["packages"]:
            extraction.recovery = _failure(envelope, "worker_version_mismatch")
        elif file_digest(source, settings.content_hash_read_chunk_bytes) != source_hash:
            extraction.recovery = _failure(envelope, "source_changed_during_recovery")
        else:
            extraction.recovery = map_layout_reply(extraction, envelope, reply, settings)
    except Timeout:
        extraction.recovery = _failure(envelope, "worker_busy")
    except MappingUnavailable as exc:
        extraction.recovery = _failure(envelope, str(exc))
    except (OSError, psutil.Error, ValueError):
        extraction.recovery = _failure(envelope, "recovery_unavailable")
    return extraction


def recover_layout(
    extraction: ExtractionResult,
    source: Path,
    settings: Settings,
    previous: RecoveryEnvelope | None = None,
) -> ExtractionResult:
    """Optional-stage boundary: configuration failures never erase native work."""
    try:
        return _recover_layout(extraction, source, settings, previous)
    except (OSError, ValueError, KeyError, psutil.Error):
        pages = selected_pages(extraction)
        extraction.recovery = _failure(
            RecoveryEnvelope(
                source_sha256="",
                native_sha256=native_fingerprint(extraction),
                request_sha256="",
                manifest_sha256="",
                requested_pages=pages,
                pages=[],
            ),
            "recovery_configuration_unavailable",
        )
        return extraction
