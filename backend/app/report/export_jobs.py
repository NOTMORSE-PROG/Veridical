"""F8.6/4h: bounded disposable preparation, independent of HTTP deadlines."""

import asyncio
import logging
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import get_settings
from app.db import get_session_factory
from app.errors import ConflictError, GoneError, NotFoundError, RateLimitedError
from app.models.enums import CheckRunStatus
from app.models.manuscript import Manuscript
from app.models.run import CheckRun, ReadinessReport
from app.report.evidence_images import evidence_image_admission
from app.report.export import build_report_pdf
from app.report.service import get_report_export_data


class PrepareExportIn(BaseModel):
    expected_revision: int = Field(ge=0)


class ExportJobOut(BaseModel):
    job_id: str
    check_run_id: int
    assessment_revision: int
    state: str
    message: str | None = None
    poll_seconds: float
    wait_seconds: float


@dataclass(frozen=True)
class ExportBinding:
    owner: int
    run: int
    revision: int
    manuscript: int
    content_hash: str | None
    file_ref: str
    purged_at: object


async def export_binding(session, run: int, owner: int) -> ExportBinding:
    row = (
        await session.execute(
            select(
                ReadinessReport.assessment_revision,
                Manuscript.id,
                Manuscript.content_hash,
                Manuscript.file_ref,
                Manuscript.purged_at,
            )
            .select_from(ReadinessReport)
            .join(CheckRun, CheckRun.id == ReadinessReport.check_run_id)
            .join(Manuscript, Manuscript.id == CheckRun.manuscript_id)
            .where(CheckRun.id == run, Manuscript.instructor_id == owner)
            .where(CheckRun.status == CheckRunStatus.done)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Report not found.")
    return ExportBinding(owner, run, *row)


@dataclass
class ExportJob:
    id: str
    binding: ExportBinding
    state: str = "preparing"
    message: str | None = None
    finished: float | None = None
    directory: tempfile.TemporaryDirectory | None = None
    path: Path | None = None
    leases: int = 0
    task: asyncio.Task | None = None

    def out(self) -> ExportJobOut:
        settings = get_settings()
        return ExportJobOut(
            job_id=self.id,
            check_run_id=self.binding.run,
            assessment_revision=self.binding.revision,
            state=self.state,
            message=self.message,
            poll_seconds=settings.export_job_poll_seconds,
            wait_seconds=settings.export_job_wait_seconds,
        )

    def cleanup(self):
        if self.leases == 0 and self.directory is not None:
            self.directory.cleanup()
            self.directory = None
            self.path = None


class ExportJobs:
    """One active or retained artifact per single-worker backend; no queue."""

    def __init__(self):
        self.job: ExportJob | None = None
        self.closing = False
        self.expiry: asyncio.TimerHandle | None = None

    def expire(self):
        job = self.job
        if (
            job
            and job.finished is not None
            and not job.leases
            and time.monotonic() - job.finished >= get_settings().export_job_ttl_seconds
        ):
            job.cleanup()
            self.job = None

    def prepare(self, binding: ExportBinding) -> ExportJob:
        self.expire()
        if self.closing:
            raise RateLimitedError("PDF preparation is restarting. Try again shortly.")
        previous = self.job
        if previous and previous.binding == binding and previous.state in {"preparing", "ready"}:
            return previous
        if previous and (previous.state == "preparing" or previous.leases):
            raise RateLimitedError("Another PDF is preparing or downloading. Try again shortly.")
        if previous:
            previous.cleanup()
        job = ExportJob(secrets.token_hex(24), binding)
        self.job = job
        job.task = asyncio.create_task(self.run(job), name="report-export")
        return job

    def find(self, job_id: str, binding: ExportBinding) -> ExportJob:
        self.expire()
        job = self.job
        if job is None or job.id != job_id:
            raise GoneError("PDF preparation expired or was interrupted. Export again.")
        if job.binding.owner != binding.owner or job.binding.run != binding.run:
            raise NotFoundError("PDF preparation not found.")
        if job.binding != binding:
            raise ConflictError(
                "The assessment or source changed. Reload the report and export again."
            )
        return job

    async def run(self, job: ExportJob):
        try:
            settings = get_settings()
            with evidence_image_admission(settings):
                async with get_session_factory()() as session:
                    if (
                        await export_binding(session, job.binding.run, job.binding.owner)
                        != job.binding
                    ):
                        raise ConflictError("The assessment or source changed. Export again.")
                    data = await get_report_export_data(session, job.binding.run, job.binding.owner)
                    if data.report.assessment_revision != job.binding.revision:
                        raise ConflictError(
                            "The assessment changed. Reload the report and export again."
                        )
                work = asyncio.create_task(asyncio.to_thread(build_report_pdf, data))
                try:
                    pdf = await asyncio.shield(work)
                except asyncio.CancelledError:
                    while not work.done():
                        try:
                            await asyncio.shield(work)
                        except asyncio.CancelledError:
                            continue
                        except Exception:
                            break
                    if not work.cancelled():
                        work.exception()
                    raise
                del data
                if len(pdf) > settings.export_job_max_bytes:
                    raise ConflictError(
                        "The prepared PDF exceeds the download limit. "
                        "Use the saved report for review."
                    )
                async with get_session_factory()() as session:
                    if (
                        await export_binding(session, job.binding.run, job.binding.owner)
                        != job.binding
                    ):
                        raise ConflictError(
                            "The assessment or source changed. Reload the report and export again."
                        )
                job.directory = tempfile.TemporaryDirectory(prefix="veridical-export-")
                job.path = Path(job.directory.name) / "report.pdf"
                # The bounded local write must finish before cancellation can
                # clean up its directory, just like native generation above.
                write = asyncio.create_task(asyncio.to_thread(job.path.write_bytes, pdf))
                try:
                    await asyncio.shield(write)
                except asyncio.CancelledError:
                    while not write.done():
                        try:
                            await asyncio.shield(write)
                        except asyncio.CancelledError:
                            continue
                        except Exception:
                            break
                    if not write.cancelled():
                        write.exception()
                    raise
                del pdf
                job.state = "ready"
        except ConflictError as exc:
            job.state, job.message = "stale", str(exc)
            job.cleanup()
        except asyncio.CancelledError:
            job.state, job.message = "failed", "PDF preparation was interrupted. Export again."
            job.cleanup()
            raise
        except Exception:
            logging.getLogger(__name__).warning("Report PDF preparation failed", exc_info=False)
            job.state, job.message = (
                "failed",
                "PDF preparation could not finish. Try exporting again shortly.",
            )
            job.cleanup()
        finally:
            job.finished = time.monotonic()
            if self.expiry:
                self.expiry.cancel()
            self.expiry = asyncio.get_running_loop().call_later(
                get_settings().export_job_ttl_seconds, self.expire
            )

    async def shutdown(self):
        self.closing = True
        if self.expiry:
            self.expiry.cancel()
        if self.job and self.job.task and not self.job.task.done():
            # Do not cancel native work or release its admission early.
            await asyncio.shield(self.job.task)
        if self.job:
            self.job.cleanup()
        self.job = None
        if self.expiry:
            self.expiry.cancel()


export_jobs = ExportJobs()
