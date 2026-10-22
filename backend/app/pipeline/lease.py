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
from app.pipeline.machine import ClaimLost, TerminalFailure

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

    async def current_token(self) -> datetime:
        """Synchronized snapshot; lifecycle callers already hold their run row."""
        async with self.lock:
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
                            or row.claimed_at != self.token
                            or self.token
                            < now - timedelta(seconds=self.settings.pipeline_claim_stale_seconds)
                        ):
                            self.lost = True
                            return
                        row.claimed_at = now
                        await session.commit()
                        self.token = now
                        self.expires = attempt + self.settings.pipeline_claim_stale_seconds
            except (DBAPIError, TimeoutError):
                # A busy pipeline row must release the global guard promptly.
                # Known ownership lasts only until the last conservative expiry.
                logger.warning("Claim renewal deferred for check run %s", self.run_id)

    async def checkpoint(self) -> None:
        """Read ownership without committing or sharing the analytical session."""
        async with self.lock:
            now_mono = asyncio.get_running_loop().time()
            if self.lost or now_mono >= self.expires:
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
                            actual != self.token
                            or now is None
                            or actual
                            < now - timedelta(seconds=self.settings.pipeline_claim_stale_seconds)
                        ):
                            self.lost = True
                            raise ClaimLost
            except (DBAPIError, TimeoutError):
                if asyncio.get_running_loop().time() >= self.expires:
                    self.lost = True
                    raise ClaimLost from None
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
            if asyncio.get_running_loop().time() >= self.expires:
                self.lost = True
