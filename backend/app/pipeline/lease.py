"""F3 independent short-session renewal; analytical sessions remain private."""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.run import CheckRun
from app.pipeline.coordination import Admission
from app.pipeline.guards import try_guard
from app.pipeline.machine import ClaimLost, ClaimYield, TerminalFailure

logger = logging.getLogger(__name__)


class ClaimLease:
    def __init__(
        self, factory: async_sessionmaker[AsyncSession], admission: Admission, settings: Settings
    ):
        assert admission.run is not None and admission.token is not None
        assert admission.attempt_started is not None
        self.factory = factory
        self.run_id = admission.run.id
        self.token = admission.token
        self.settings = settings
        self.expires = admission.attempt_started + settings.pipeline_claim_stale_seconds
        elapsed = (
            max((datetime.now(UTC) - admission.run.started_at).total_seconds(), 0)
            if admission.run.started_at is not None
            else 0
        )
        self.deadline = admission.attempt_started + max(
            settings.pipeline_run_deadline_seconds - elapsed, 0
        )
        self.lost = False
        # A renewal whose commit was sent but whose reply never arrived in
        # time: (token it wrote, monotonic start of that attempt). The commit
        # may or may not have landed, so the stored claim can hold either
        # value until a later read settles it.
        self.pending: tuple[datetime, float] | None = None
        self.lock = asyncio.Lock()
        self.task: asyncio.Task | None = None
        self.stopping = asyncio.Event()

    def start(self) -> None:
        self.task = asyncio.create_task(self._loop(), name=f"pipeline-lease-{self.run_id}")

    async def stop(self) -> None:
        if self.task is not None:
            # Let an in-flight bounded commit publish its token before release.
            self.stopping.set()
            await self.task
            self.task = None

    def tokens(self) -> tuple[datetime, ...]:
        """Every value the stored claim may hold for this lease (for release)."""
        return (self.token,) if self.pending is None else (self.token, self.pending[0])

    def _owns(self, stored: datetime | None) -> bool:
        """True when the stored claim is this lease's. A renewal that timed out
        after sending its commit may still have landed; finding its token in
        the row proves it did, so it becomes the current token."""
        if stored is not None and stored == self.token:
            return True
        if self.pending is not None and stored == self.pending[0]:
            token, attempt = self.pending
            self.token, self.pending = token, None
            self.expires = max(self.expires, attempt + self.settings.pipeline_claim_stale_seconds)
            return True
        return False

    async def _settle_pending(self) -> None:
        """Bounded read that adopts a renewal whose commit outcome was unknown."""
        try:
            async with asyncio.timeout(self.settings.pipeline_coordination_timeout_seconds):
                async with self.factory() as session:
                    stored = await session.scalar(
                        select(CheckRun.claimed_at).where(CheckRun.id == self.run_id)
                    )
            self._owns(stored)
        except (DBAPIError, TimeoutError):
            # Still unknown; the next renewal or checkpoint settles it.
            pass

    async def current_token(self) -> datetime:
        """Synchronized snapshot; lifecycle callers already hold their run row."""
        async with self.lock:
            if self.pending is not None and not self.lost:
                # The caller compares this token with the row it has locked;
                # an unsettled renewal would make our own claim look replaced.
                await self._settle_pending()
                if self.pending is not None:
                    # Still unknown which token the row holds: step back for
                    # a later turn rather than report the claim as taken.
                    raise ClaimYield
            if self.lost or asyncio.get_running_loop().time() >= self.expires:
                raise ClaimLost
            return self.token

    async def _renew(self) -> None:
        """The only token writer, serialized locally and by the admission guard."""
        async with self.lock:
            if self.lost or self.stopping.is_set():
                return
            attempt = asyncio.get_running_loop().time()
            try:
                async with asyncio.timeout(self.settings.pipeline_coordination_timeout_seconds):
                    async with self.factory() as session:
                        if not await try_guard(session, self.settings):
                            return
                        now = await session.scalar(select(func.clock_timestamp()))
                        row = await session.scalar(
                            select(CheckRun)
                            .where(CheckRun.id == self.run_id)
                            .with_for_update(nowait=True, key_share=True)
                        )
                        if (
                            row is None
                            or not self._owns(row.claimed_at)
                            or self.token
                            < now - timedelta(seconds=self.settings.pipeline_claim_stale_seconds)
                        ):
                            self.lost = True
                            return
                        # Holding the row lock means no earlier renewal is still
                        # in flight, so the new attempt replaces any older one.
                        row.claimed_at = now
                        self.pending = (now, attempt)
                        await session.commit()
                        self.token, self.pending = now, None
                        self.expires = attempt + self.settings.pipeline_claim_stale_seconds
            except (DBAPIError, TimeoutError):
                # A busy pipeline row must release the global guard promptly.
                # Known ownership lasts only until the last conservative expiry.
                logger.warning("Claim renewal deferred for check run %s", self.run_id)

    async def checkpoint(self) -> None:
        """Read ownership without committing or sharing the analytical session."""
        async with self.lock:
            now_mono = asyncio.get_running_loop().time()
            # An unsettled renewal may have extended ownership; read first.
            if self.lost or (now_mono >= self.expires and self.pending is None):
                raise ClaimLost
            try:
                async with asyncio.timeout(self.settings.pipeline_coordination_timeout_seconds):
                    async with self.factory() as session:
                        actual, now = (
                            await session.execute(
                                select(CheckRun.claimed_at, func.clock_timestamp()).where(
                                    CheckRun.id == self.run_id
                                )
                            )
                        ).one_or_none() or (None, None)
                        if (
                            not self._owns(actual)
                            or now is None
                            or actual
                            < now - timedelta(seconds=self.settings.pipeline_claim_stale_seconds)
                        ):
                            self.lost = True
                            raise ClaimLost
            except (DBAPIError, TimeoutError):
                pass
            if asyncio.get_running_loop().time() >= self.expires:
                self.lost = True
                raise ClaimLost
            if asyncio.get_running_loop().time() >= self.deadline:
                raise TerminalFailure(
                    "run_deadline_exceeded",
                    "This check reached its processing time limit. Start a new check when ready.",
                )

    async def _loop(self) -> None:
        while not self.lost and not self.stopping.is_set():
            remaining = self.deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self.stopping.wait(),
                    timeout=min(self.settings.pipeline_claim_renew_seconds, remaining),
                )
            if self.stopping.is_set() or asyncio.get_running_loop().time() >= self.deadline:
                return
            try:
                await self._renew()
            except Exception:
                self.lost = True
                logger.warning("Claim renewal stopped for check run %s", self.run_id)
            # With an unsettled renewal the next locked read decides; giving up
            # here would abandon a claim that may already have been extended.
            if asyncio.get_running_loop().time() >= self.expires and self.pending is None:
                self.lost = True
