"""Give freed heap memory back to the operating system after heavy work.

CPython hands freed objects back to the C allocator, but glibc keeps the
freed pages in its heap for reuse. After a big preparation or a check the
process therefore stays at its high-water mark even though almost nothing is
still in use, and on a 512 MiB host that leftover is what the next job runs
out of. `malloc_trim(0)` asks glibc to return those pages. It never frees
anything still referenced, so it cannot change what any later code computes.
"""

import ctypes
import ctypes.util
import gc
import logging
import sys
from functools import lru_cache

_logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _malloc_trim():
    if not sys.platform.startswith("linux"):
        return None
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
        trim = libc.malloc_trim
    except (OSError, AttributeError):
        # Not glibc (musl has no malloc_trim): nothing to ask for.
        return None
    trim.argtypes = [ctypes.c_size_t]
    trim.restype = ctypes.c_int
    return trim


def return_freed_memory(*, collect: bool = True) -> None:
    """Collect unreachable objects, then let the allocator release free pages."""
    if collect:
        gc.collect()
    trim = _malloc_trim()
    if trim is None:
        return
    try:
        trim(0)
    except OSError:
        _logger.debug("malloc_trim failed", exc_info=True)
