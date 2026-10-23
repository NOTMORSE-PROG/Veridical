"""F1/F2 preparation ownership follows native work past awaiter cancellation."""

import asyncio
import inspect
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from types import TracebackType
from typing import TypeVar

from app import messages
from app.config import Settings
from app.errors import DocumentPreparationBusyError

T = TypeVar("T")


def _execute_sync(function, args, kwargs):
    result = function(*args, **kwargs)
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise TypeError("Document preparation requires synchronous work")
    return result


class PreparationLease:
    """Internal owner; an asyncio wrapper's cancellation cannot finish this lease."""

    def __init__(self, admission: "PreparationAdmission"):
        self._admission = admission
        self._entered = False
        self._owner_finished = False
        self._futures: set[Future] = set()

    def __enter__(self) -> "PreparationLease":
        with self._admission._lock:
            if self._entered or self._owner_finished:
                raise RuntimeError("Preparation lease cannot be entered again")
            self._entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        with self._admission._lock:
            self._owner_finished = True
            self._admission._release_if_finished(self)

    async def run(self, function: Callable[..., T], *args, **kwargs) -> T:
        future = self._admission._submit(self, function, args, kwargs)
        return await asyncio.wrap_future(future)


class PreparationAdmission:
    """No waiting queue; track request ownership AND actual concurrent futures.

    This controller is independent of asyncio loop identity. Shutdown prevents
    submission and cancels only work that has not started. A running thread is
    never claimed terminated; it can keep this controller occupied indefinitely.
    """

    def __init__(self, settings: Settings):
        self._slots = settings.document_preparation_slots
        self._lock = threading.RLock()
        self._leases: set[PreparationLease] = set()
        self._accepting = True
        self._executor = ThreadPoolExecutor(
            max_workers=self._slots, thread_name_prefix="document-preparation"
        )

    @property
    def active_owners(self) -> int:
        with self._lock:
            return len(self._leases)

    @property
    def drained(self) -> bool:
        return self.active_owners == 0

    @property
    def accepting(self) -> bool:
        with self._lock:
            return self._accepting

    def acquire(self) -> PreparationLease:
        with self._lock:
            if not self._accepting:
                raise RuntimeError("Document preparation admission is closed")
            if len(self._leases) >= self._slots:
                raise DocumentPreparationBusyError(messages.DOCUMENT_PREPARATION_BUSY)
            lease = PreparationLease(self)
            self._leases.add(lease)
            return lease

    def _submit(self, lease: PreparationLease, function, args, kwargs) -> Future:
        with self._lock:
            if not callable(function):
                raise TypeError("Document preparation requires synchronous work")
            if inspect.iscoroutinefunction(function) or inspect.iscoroutinefunction(
                function.__call__
            ):
                raise TypeError("Document preparation requires synchronous work")
            if not self._accepting:
                raise RuntimeError("Document preparation admission is closed")
            if lease not in self._leases or lease._owner_finished or not lease._entered:
                raise RuntimeError("Document preparation lease is not active")
            if lease._futures:
                # One owner must not create an unbounded executor queue inside
                # the apparent admission bound, including after cancellation.
                raise RuntimeError("Preparation lease already has native work")
            future = self._executor.submit(_execute_sync, function, args, kwargs)
            lease._futures.add(future)
            future.add_done_callback(lambda done: self._native_finished(lease, done))
            return future

    def _native_finished(self, lease: PreparationLease, future: Future) -> None:
        # The callback may run on a worker after the request loop has closed.
        # Never call the old loop or log raw document/exception data here.
        if not future.cancelled():
            future.exception()
        with self._lock:
            lease._futures.discard(future)
            self._release_if_finished(lease)

    def _release_if_finished(self, lease: PreparationLease) -> None:
        if lease._owner_finished and not lease._futures:
            self._leases.discard(lease)

    def shutdown(self) -> None:
        with self._lock:
            self._accepting = False
        # Do not wait on a native callable from the event loop. Python still
        # waits for executor threads at process exit; this is not termination.
        self._executor.shutdown(wait=False, cancel_futures=True)


@dataclass(frozen=True, eq=False)
class PreparationLifespan:
    controller: PreparationAdmission


_registry_lock = threading.RLock()
_current: PreparationAdmission | None = None
_managed_lifespan: PreparationLifespan | None = None
_managed_closing = False


def _get_locked(settings: Settings) -> PreparationAdmission:
    global _current
    if _managed_closing:
        raise DocumentPreparationBusyError(messages.DOCUMENT_PREPARATION_UNAVAILABLE)
    if _current is not None and not _current.accepting:
        if not _current.drained or _managed_lifespan is not None:
            raise DocumentPreparationBusyError(messages.DOCUMENT_PREPARATION_UNAVAILABLE)
        _current = None
    if _current is None:
        _current = PreparationAdmission(settings)
    if _current._slots != settings.document_preparation_slots:
        raise RuntimeError("Document preparation configuration differs from the active controller")
    return _current


def get_preparation_admission(settings: Settings) -> PreparationAdmission:
    """Same process controller across loops; never replace surviving native work."""
    with _registry_lock:
        return _get_locked(settings)


@contextmanager
def preparation_scope(settings: Settings, lease: PreparationLease | None = None):
    if lease is not None:
        with _registry_lock, lease._admission._lock:
            if lease._admission is not _current:
                raise RuntimeError("Document preparation lease belongs to another controller")
            if (
                not lease._entered
                or lease._owner_finished
                or lease not in lease._admission._leases
                or not lease._admission._accepting
                or lease._futures
            ):
                raise RuntimeError("Document preparation lease is not active")
        yield lease
        return
    # Lookup and acquisition share the lifecycle lock. Shutdown cannot close
    # this controller between those two steps and turn busy into a generic 500.
    with _registry_lock:
        owned = _get_locked(settings).acquire()
    with owned:
        yield owned


def start_preparation_admission(settings: Settings) -> PreparationLifespan:
    global _managed_lifespan
    with _registry_lock:
        if _managed_lifespan is not None:
            raise RuntimeError("Document preparation already has a managed lifespan")
        if _current is not None and not _current.accepting and not _current.drained:
            raise RuntimeError("Previous document preparation work is still draining")
        handle = PreparationLifespan(_get_locked(settings))
        _managed_lifespan = handle
        return handle


def begin_preparation_shutdown(handle: PreparationLifespan) -> PreparationAdmission:
    global _managed_closing
    with _registry_lock:
        if handle is not _managed_lifespan or handle.controller is not _current:
            raise RuntimeError("Document preparation lifespan is no longer current")
        _managed_closing = True
        handle.controller.shutdown()
        return handle.controller


def finish_preparation_lifespan(handle: PreparationLifespan) -> None:
    global _managed_lifespan, _managed_closing
    with _registry_lock:
        if handle is not _managed_lifespan or not _managed_closing:
            raise RuntimeError("Document preparation lifespan is not closing")
        _managed_lifespan = None
        _managed_closing = False
