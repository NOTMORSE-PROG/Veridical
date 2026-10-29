"""F8: request-owned, hash-verified disk copies for large source readers."""

import hashlib
import threading
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import Settings
from app.errors import ConflictError, RateLimitedError

_lock = threading.Lock()
_active_snapshots = 0


class SourceSnapshot:
    def __init__(self, path: Path, expected_sha256: str, settings: Settings):
        global _active_snapshots
        self._temporary = None
        self._reserved = False
        with _lock:
            if _active_snapshots >= settings.evidence_source_slots:
                raise RateLimitedError("Source copies are busy. Try again shortly.")
            _active_snapshots += 1
            self._reserved = True
        try:
            self._temporary = TemporaryDirectory(prefix="veridical-source-")
        except BaseException:
            self.close()
            raise
        self.path = Path(self._temporary.name) / "source.pdf"
        self.sha256 = ""
        self.size = 0
        try:
            limit = settings.source_reader_max_mb * 1024 * 1024
            digest = hashlib.sha256()
            with path.open("rb") as source, self.path.open("xb") as target:
                while chunk := source.read(settings.storage_download_chunk_bytes):
                    self.size += len(chunk)
                    if self.size > limit:
                        raise ConflictError("The source exceeds the evidence-reading limit.")
                    digest.update(chunk)
                    if target.write(chunk) != len(chunk):
                        raise OSError("Source snapshot write was incomplete.")
            self.sha256 = digest.hexdigest()
            if self.sha256 != expected_sha256:
                raise ConflictError("The available source no longer matches this evidence record.")
        except BaseException:
            self.close()
            raise

    def close(self):
        global _active_snapshots
        if self._temporary is not None:
            self._temporary.cleanup()
        with _lock:
            if self._reserved:
                self._reserved = False
                _active_snapshots -= 1

    def __del__(self):
        # Explicit consumers close deterministically; abandoned response owners
        # also release the reservation if cancellation prevents transfer.
        with suppress(Exception):
            self.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
