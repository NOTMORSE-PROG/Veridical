"""F1 worker lifetime ownership, including helpers missed between RSS samples.

Trusted interpreter/worker only, not a filesystem or network sandbox. On
Windows the fixed bootstrap joins the parent's job before importing the worker;
operator-installed interpreter startup hooks are outside that guarantee.
"""

import argparse
import ctypes
import os
import runpy
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from ctypes import wintypes
from pathlib import Path

# Win32 ABI constants/structures, not tunable product policy.
_JOB_OBJECT_ASSIGN_PROCESS = 0x0001
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_ERROR_ALREADY_EXISTS = 183


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


def _windows_api() -> ctypes.CDLL:
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "OpenJobObjectW": ([wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
            wintypes.BOOL,
        ),
        "QueryInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p],
            wintypes.BOOL,
        ),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "GetCurrentProcess": ([], wintypes.HANDLE),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        method = getattr(api, name)
        method.argtypes = arguments
        method.restype = result
    return api


def _require(result: int | None) -> int:
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    return result


class _WindowsJob:
    def __init__(self) -> None:
        self.api = _windows_api()
        self.name = f"Local\\veridical-layout-{uuid.uuid4()}"
        ctypes.set_last_error(0)
        self.handle = _require(self.api.CreateJobObjectW(None, self.name))
        try:
            if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
                raise OSError("Worker job name already exists")
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            _require(
                self.api.SetInformationJobObject(
                    self.handle,
                    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(limits),
                    ctypes.sizeof(limits),
                )
            )
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.handle is not None:
            handle, self.handle = self.handle, None
            _require(self.api.CloseHandle(handle))

    def terminate(self, timeout: float, poll: float) -> None:
        _require(self.api.TerminateJobObject(self.handle, 1))
        deadline = time.monotonic() + timeout
        while True:
            accounting = _Accounting()
            _require(
                self.api.QueryInformationJobObject(
                    self.handle,
                    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                    ctypes.byref(accounting),
                    ctypes.sizeof(accounting),
                    None,
                )
            )
            if accounting.ActiveProcesses == 0:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Worker descendants did not finish terminating")
            time.sleep(min(poll, remaining))


def _join_windows_job(name: str) -> None:
    api = _windows_api()
    handle = _require(api.OpenJobObjectW(_JOB_OBJECT_ASSIGN_PROCESS, False, name))
    try:
        _require(api.AssignProcessToJobObject(handle, api.GetCurrentProcess()))
    finally:
        # The parent must own the LAST job handle before the worker starts.
        _require(api.CloseHandle(handle))


@contextmanager
def managed_worker(
    interpreter: Path,
    worker: Path,
    arguments: list[str],
    *,
    directory: Path,
    environment: dict[str, str],
    cleanup_timeout: float,
    poll: float,
) -> Iterator[subprocess.Popen[bytes]]:
    job = None
    process = None
    try:
        command = [str(interpreter), "-I"]
        if os.name == "nt":
            job = _WindowsJob()
            command += [str(Path(__file__).resolve()), "--job-object", job.name, "--worker"]
        command += [str(worker), *arguments]
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=directory,
            env=environment,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            start_new_session=os.name != "nt",
        )
        yield process
    finally:
        try:
            if job is not None:
                job.terminate(cleanup_timeout, poll)
            elif process is not None:
                # Retain the launcher's group identity even if it already exited.
                # A deliberately setsid-detached process is not contained by this.
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
        finally:
            try:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    try:
                        process.wait(timeout=cleanup_timeout)
                    except subprocess.TimeoutExpired:
                        raise TimeoutError("Worker launcher did not finish terminating") from None
            finally:
                if job is not None:
                    job.close()


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--job-object", required=True)
    parser.add_argument("--worker", required=True)
    args, worker_args = parser.parse_known_args()
    _join_windows_job(args.job_object)
    sys.argv = [args.worker, *worker_args]
    runpy.run_path(args.worker, run_name="__main__")


if __name__ == "__main__":
    main()
