"""F1: rejected native opens release source handles before upload cleanup."""

import gc
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import pytest

from app.config import Settings
from app.errors import FileMalformedError
from app.ingest.pdf import extract_document
from app.messages import FILE_UNREADABLE


@pytest.mark.parametrize("threaded", [False, True], ids=["direct", "retained-future"])
def test_failed_open_releases_handle_while_error_and_future_are_retained(tmp_path, threaded):
    path = tmp_path / "damaged.pdf"
    path.write_bytes(b"%PDF-1.7\ntruncated")
    settings = Settings(_env_file=None)
    was_enabled = gc.isenabled()
    executor = ThreadPoolExecutor(max_workers=1)
    future = None
    retained_error = None
    try:
        gc.disable()
        if threaded:
            future = executor.submit(extract_document, str(path), settings)
            invoke = future.result
        else:

            def invoke():
                return extract_document(str(path), settings)

        try:
            invoke()
        except FileMalformedError as exc:
            retained_error = exc
        assert retained_error is not None and str(retained_error) == FILE_UNREADABLE
        assert future is None or future.done()
        retained_handles = [
            item
            for item in psutil.Process().open_files()
            if Path(item.path).resolve() == path.resolve()
        ]
        assert not retained_handles, "Rejected source remains open"
        # Windows refuses this while the failed constructor still owns a handle.
        # POSIX handle inspection above covers its more permissive unlink rule.
        path.unlink()
        assert not path.exists()
    finally:
        executor.shutdown(wait=True)
        if was_enabled:
            gc.enable()
