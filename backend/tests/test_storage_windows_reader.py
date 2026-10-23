"""F1/F8: exact winner verification under real Windows access/share handles."""

import ctypes
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from app.config import Settings
from app.storage import ensure_local_file

pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows handle sharing")

READ, WRITE, DELETE = 0x80000000, 0x40000000, 0x00010000
SHARE_READ, SHARE_WRITE, SHARE_DELETE = 1, 2, 4
SHARE_ALL = SHARE_READ | SHARE_WRITE | SHARE_DELETE


@pytest.fixture
def windows():
    from app.storage import _windows

    return _windows


@contextmanager
def held(windows, path, access, sharing):
    handle = windows._create_file(str(path), access, sharing, None, 3, 0x80, None)
    assert handle != windows._INVALID_HANDLE_VALUE
    try:
        yield handle
    finally:
        assert windows._close_handle(handle)


def handle_is_open(windows, handle):
    flags = ctypes.c_uint32()
    get_flags = windows._kernel.GetHandleInformation
    get_flags.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
    get_flags.restype = ctypes.c_int
    return bool(get_flags(handle, ctypes.byref(flags)))


@pytest.mark.parametrize("access", [DELETE, READ | DELETE])
def test_exact_competing_winner_reused_while_delete_access_holder_remains_open(
    windows, tmp_path, access
):
    path = tmp_path / "source.bin"
    payload = b"Complete source\r\n\x1a\x00\xff"
    entered, release = threading.Event(), threading.Event()

    class Durable:
        def get_bytes(self, key):
            entered.set()
            assert release.wait(5), "Release every controlled durable read"
            return payload

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            ensure_local_file, Settings(_env_file=None, data_dir=tmp_path), Durable(), str(path)
        )
        try:
            assert entered.wait(5)
            path.write_bytes(payload)
            identity = path.stat().st_ino
            with held(windows, path, access, SHARE_ALL) as holder:
                # Known-negative CRT control, while the same holder remains open.
                with pytest.raises(PermissionError):
                    path.read_bytes()
                release.set()
                assert future.result(timeout=5) == path
                assert handle_is_open(windows, holder)
                assert windows.read_published_winner(path) == payload
                assert path.stat().st_ino == identity
        finally:
            release.set()
    assert path.read_bytes() == payload
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("access,sharing", [(WRITE, SHARE_ALL), (READ, 0)])
def test_winner_read_still_rejects_write_access_or_exclusive_holder(
    windows, tmp_path, access, sharing
):
    path = tmp_path / "source.bin"
    payload = b"Immutable source"
    path.write_bytes(payload)
    with held(windows, path, access, sharing) as holder:
        with pytest.raises(OSError) as error:
            windows.read_published_winner(path)
        assert error.value.winerror == 32
        assert handle_is_open(windows, holder)
    assert path.read_bytes() == payload


def test_missing_winner_is_not_created(windows, tmp_path):
    path = tmp_path / "missing.bin"
    with pytest.raises(FileNotFoundError):
        windows.read_published_winner(path)
    assert not path.exists()


def test_winner_reader_owns_one_noninheritable_binary_descriptor(windows, tmp_path, monkeypatch):
    path = tmp_path / "source.bin"
    payload = b"\r\n\x1a\x00\xff"
    path.write_bytes(payload)
    convert = windows.msvcrt.open_osfhandle
    observed = []

    def record(handle, flags):
        descriptor = convert(handle, flags)
        observed.append((handle, descriptor))
        assert not os.get_inheritable(descriptor)
        assert flags & os.O_BINARY
        return descriptor

    monkeypatch.setattr(windows.msvcrt, "open_osfhandle", record)
    assert windows.read_published_winner(path) == payload
    assert len(observed) == 1
    handle, descriptor = observed[0]
    with pytest.raises(OSError):
        os.fstat(descriptor)
    assert not handle_is_open(windows, handle)


@pytest.mark.parametrize("phase", ["transfer", "stream", "read"])
def test_ownership_cleanup_on_conversion_stream_and_read_failure(
    windows, tmp_path, monkeypatch, phase
):
    path = tmp_path / "source.bin"
    path.write_bytes(b"Winner remains unchanged")
    create, convert = windows._create_file, windows.msvcrt.open_osfhandle
    close_handle, close_descriptor = windows._close_handle, windows.os.close
    handles, descriptors, handle_closes, descriptor_closes = [], [], [], []
    failure = OSError("Controlled reader failure")

    def record_create(*args):
        result = create(*args)
        handles.append(result)
        return result

    def record_convert(handle, flags):
        if phase == "transfer":
            raise failure
        descriptor = convert(handle, flags)
        descriptors.append(descriptor)
        return descriptor

    def record_handle_close(handle):
        handle_closes.append(handle)
        return close_handle(handle)

    def record_descriptor_close(descriptor):
        descriptor_closes.append(descriptor)
        return close_descriptor(descriptor)

    class BrokenReader:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            raise failure

    def failed_stream(descriptor, mode, *, closefd):
        assert descriptor == descriptors[0] and mode == "rb" and closefd is False
        if phase == "stream":
            raise failure
        return BrokenReader()

    monkeypatch.setattr(windows, "_create_file", record_create)
    monkeypatch.setattr(windows.msvcrt, "open_osfhandle", record_convert)
    monkeypatch.setattr(windows, "_close_handle", record_handle_close)
    monkeypatch.setattr(windows.os, "close", record_descriptor_close)
    monkeypatch.setattr(windows.os, "fdopen", failed_stream)
    with pytest.raises(OSError) as error:
        windows.read_published_winner(path)
    assert error.value is failure and len(handles) == 1
    assert not handle_is_open(windows, handles[0])
    if phase == "transfer":
        assert handle_closes == handles and descriptor_closes == []
    else:
        assert handle_closes == [] and descriptor_closes == descriptors
    assert path.read_bytes() == b"Winner remains unchanged"


def test_native_open_failure_does_not_convert_or_close_invalid_handle(windows, monkeypatch):
    from pathlib import Path

    def failed_open(*args):
        ctypes.set_last_error(5)
        return windows._INVALID_HANDLE_VALUE

    def forbidden(*args):
        pytest.fail("Invalid handle must not be converted or closed")

    monkeypatch.setattr(windows, "_create_file", failed_open)
    monkeypatch.setattr(windows, "_close_handle", forbidden)
    monkeypatch.setattr(windows.msvcrt, "open_osfhandle", forbidden)
    with pytest.raises(PermissionError) as error:
        windows.read_published_winner(Path("unused.bin"))
    assert error.value.winerror == 5
