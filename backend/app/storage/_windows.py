"""Read an immutable published winner with Windows delete-sharing compatibility."""

import ctypes
import msvcrt
import os
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

# Win32 ABI constants, not operational limits or permission-policy settings.
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_create_file = _kernel.CreateFileW
_create_file.argtypes = (
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
)
_create_file.restype = wintypes.HANDLE
_close_handle = _kernel.CloseHandle
_close_handle.argtypes = (wintypes.HANDLE,)
_close_handle.restype = wintypes.BOOL


@contextmanager
def open_published_winner(path: Path):
    """Read exact bytes without creating/truncating or permitting a write holder.

    A compatible holder can request DELETE access even when no deletion occurs.
    This supplies handle compatibility only; callers retain source/retention
    coordination, and all genuine access or incompatible-sharing errors fail.
    """
    handle = _create_file(
        str(path),
        _GENERIC_READ,
        _FILE_SHARE_READ | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        _close_handle(handle)
        raise
    # Successful conversion transfers HANDLE ownership to the CRT descriptor.
    # Keep descriptor ownership here even if stream construction/read fails.
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as reader:
            yield reader
    finally:
        os.close(descriptor)


def read_published_winner(path: Path) -> bytes:
    """Compatibility read for callers that explicitly need a small bytes value."""
    with open_published_winner(path) as reader:
        return reader.read()
