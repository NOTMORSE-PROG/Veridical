"""Whole-file SHA-256 that is computed once per unchanged file (F1).

Several ingest stages need the digest of the same manuscript file: content
identity, the native job binding, and every image crop's source check. On the
free host one pass over a 100 MB+ PDF costs seconds of CPU quota, and a long
thesis has a dozen crops, so each stage reuses the digest of the exact same
file instead of reading it again. The memo key is the file's device, inode,
size and change times, so rewriting the file in any way is a cache miss.
"""

import hashlib
import os
import threading
from collections import OrderedDict
from pathlib import Path

from app.config import Settings

_lock = threading.Lock()
_memo: OrderedDict[tuple, str] = OrderedDict()


def _identity(path: Path, stat: os.stat_result) -> tuple:
    return (
        str(path.resolve()),
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def _remember(key: tuple, digest: str, settings: Settings) -> None:
    with _lock:
        _memo[key] = digest
        _memo.move_to_end(key)
        while len(_memo) > settings.source_digest_memo_entries:
            _memo.popitem(last=False)


def file_sha256(path: Path | str, settings: Settings) -> str:
    path = Path(path)
    with path.open("rb") as reader:
        before = os.fstat(reader.fileno())
        key = _identity(path, before)
        with _lock:
            known = _memo.get(key)
            if known is not None:
                _memo.move_to_end(key)
                return known
        digest = hashlib.file_digest(reader, "sha256").hexdigest()
        after = os.fstat(reader.fileno())
    # Only a read that saw the same file from start to end is reusable.
    if _identity(path, after) == key:
        _remember(key, digest, settings)
    return digest


def record_file_sha256(path: Path | str, digest: str, settings: Settings) -> None:
    """Remember a digest computed while the closed file was being written."""
    path = Path(path)
    # fstat, as in file_sha256: on Windows a path stat and a handle stat
    # disagree about the change time of the same file.
    with path.open("rb") as reader:
        _remember(_identity(path, os.fstat(reader.fileno())), digest, settings)


class HashingWriter:
    """File-like sink that hashes exactly the bytes it writes."""

    def __init__(self, target):
        self._target = target
        self._digest = hashlib.sha256()
        self.written = 0

    def write(self, chunk) -> int:
        written = self._target.write(chunk)
        if written != len(chunk):
            raise OSError("Source write was incomplete.")
        self._digest.update(chunk)
        self.written += written
        return written

    def hexdigest(self) -> str:
        return self._digest.hexdigest()
