"""Observe a source copy without reading bytes, downloading or refilling cache."""

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.storage import get_storage

AvailabilityState = Literal["found", "missing", "unverified", "deleted", "not_recorded"]
AvailabilityBasis = Literal["local_metadata", "object_metadata"]


@dataclass(frozen=True)
class SourceObservation:
    state: AvailabilityState
    basis: AvailabilityBasis | None = None


def probe_source(settings: Settings, manuscript_id: int, file_ref: str) -> SourceObservation:
    """One explicit observation, never a content/integrity or durability claim.

    No fallback basename: a legacy/invalid path outside the configured source
    tree cannot authorize a guessed object key or a filesystem read elsewhere.
    """
    try:
        recorded = Path(os.path.abspath(file_ref))
        lexical_root = Path(os.path.abspath(settings.data_dir))
        filename = f"{manuscript_id}{Path(file_ref).suffix}"
        # Establish the id-shaped mapping without touching the recorded path.
        # Even metadata such as symlink resolution must not probe another id.
        if ".." in Path(file_ref).parts or recorded != (
            lexical_root / settings.manuscript_upload_subdir / filename
        ):
            return SourceObservation("unverified")
        root = settings.data_dir.resolve()
        # Reject aliases even when they resolve to an in-root target. Probe
        # only the writer's own id-shaped source, never link/junction targets.
        for part in (recorded, recorded.parent):
            if part.is_symlink() or part.is_junction():
                return SourceObservation("unverified")
        path = Path(file_ref).resolve()
        expected = root / settings.manuscript_upload_subdir / filename
        if path != expected:
            return SourceObservation("unverified")
        key = path.relative_to(root).as_posix()
    except (OSError, ValueError, RuntimeError):
        return SourceObservation("unverified")

    try:
        # Metadata only: an open descriptor can prevent concurrent deletion on
        # Windows. This proves presence, not read permission or valid contents.
        metadata = path.lstat()
        if stat.S_ISREG(metadata.st_mode) and metadata.st_size:
            return SourceObservation("found", "local_metadata")
        return SourceObservation("unverified")
    except FileNotFoundError:
        pass
    except OSError:
        return SourceObservation("unverified")

    if settings.storage_backend == "local":
        return SourceObservation("missing")
    try:
        storage = get_storage(settings, metadata_probe=True)
        try:
            if storage.exists(key):
                return SourceObservation("found", "object_metadata")
        finally:
            storage.close()
    except Exception:
        # Configuration/permissions/transport failures are not missing files.
        # Never return paths, credentials, bucket names or provider exceptions.
        return SourceObservation("unverified")
    return SourceObservation("missing")
