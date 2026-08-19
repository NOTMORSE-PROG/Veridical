"""VERIDICAL API entry point."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from alembic import command
from app import db
from app.archive.router import router as archive_router
from app.audit.router import router as audit_router
from app.auth.router import router as auth_router
from app.config import get_settings
from app.dashboard.router import router as dashboard_router
from app.errors import HTTP_STATUS, VeridicalError
from app.flags.router import router as flags_router
from app.groups.router import router as groups_router
from app.groups.service import seed_default_programs
from app.ingest.router import router as ingest_router
from app.llm.router import router as llm_router
from app.pipeline.router import router as pipeline_router
from app.pipeline.worker import worker_loop
from app.report.router import router as report_router
from app.rubric.router import router as rubric_router
from app.settings.router import router as settings_router
from app.share.router import router as share_router

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _upgrade_to_head() -> None:
    """Runs `alembic upgrade head` synchronously (Alembic's own async
    support runs its own `asyncio.run()` inside `env.py` -- calling this
    directly from a running event loop would raise; `asyncio.to_thread`
    at the call site gives it a clean thread of its own). Idempotent: a
    no-op if already at head, safe to run on every boot."""
    command.upgrade(Config(str(_ALEMBIC_INI)), "head")


async def _seed_programs_on_boot() -> None:
    """V-062: picks up any name added to `Settings.default_programs`
    since the last deploy, with no new migration required (ground rule
    7). Idempotent (find-or-create) -- thin, patchable wrapper so tests
    can mock this the same way they mock `_upgrade_to_head`, matching
    it move-for-move rather than needing their own separate story."""
    await seed_default_programs()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Migrations were previously a manual "remember to run alembic upgrade
    # head against the Neon DSN" step (V-048) -- a real deploy landed a
    # schema change (V-055's migration 0011) with no automated way to
    # apply it. Keyed off veridical_env (already "prod" in Render's real
    # environment today, confirmed live via /health) rather than a new
    # settings flag, so this activates the moment this code deploys, no
    # extra Render dashboard step required. Gated to prod only so no
    # TestClient-based test (every one of which imports this module) ever
    # points a real migration run at a test DATABASE_URL by surprise --
    # same reasoning as pipeline_worker_autostart below.
    if get_settings().veridical_env == "prod":
        await asyncio.to_thread(_upgrade_to_head)
        # Gated to prod for the same reason the migration check above is
        # -- every TestClient-based test imports this module and must
        # never open a real DB connection by surprise against whatever
        # DATABASE_URL happens to be configured.
        await _seed_programs_on_boot()

    # Gated by settings (default off, V-018): every TestClient-based test
    # imports this module, and a real polling loop must never start
    # against a test's DATABASE_URL by surprise. Production (Render)
    # turns this on — the "simplest job runner" the ticket's own research
    # note asked for, no paid queue infrastructure.
    task = None
    if get_settings().pipeline_worker_autostart:
        task = asyncio.create_task(worker_loop())
    yield
    if task is not None:
        task.cancel()


app = FastAPI(
    title="VERIDICAL API",
    version="0.1.0",
    description="Defense-readiness checks for capstone manuscripts.",
    lifespan=_lifespan,
)
app.include_router(archive_router)
app.include_router(audit_router)
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(flags_router)
app.include_router(groups_router)
app.include_router(ingest_router)
app.include_router(llm_router)
app.include_router(pipeline_router)
app.include_router(report_router)
app.include_router(rubric_router)
app.include_router(settings_router)
app.include_router(share_router)

_cors_origins = get_settings().cors_allowed_origins_list
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        # The session cookie (V-014) rides on `credentials: "include"` —
        # browsers refuse the response without this, found live testing
        # V-014's sign-in flow (V-048's CORS setup predates any
        # cookie-based auth, so nothing exercised this gap until now).
        allow_credentials=True,
    )


@app.exception_handler(VeridicalError)
async def veridical_error_handler(request: Request, exc: VeridicalError) -> JSONResponse:
    """The ONE place taxonomy exceptions become HTTP (CODING.md §2/§4):
    consistent envelope, never a bare 500 for a user-fixable state."""
    return JSONResponse(
        status_code=HTTP_STATUS.get(exc.code, 500),
        content={"error": {"code": exc.code, "message": str(exc)}},
    )


@app.get("/health")
async def health() -> dict:
    """Liveness + dependency report.

    Always 200 while the process serves; the DB state is reported honestly
    in the body ("degraded", not an error) per the failure taxonomy
    (TESTING.md §5) — an unreachable dependency must never masquerade as
    anything else.
    """
    settings = get_settings()
    db_ok, db_detail = await db.check_connectivity(
        settings.database_url, timeout=settings.db_health_timeout
    )
    return {
        "status": "ok" if db_ok else "degraded",
        "db": db_detail,
        "fake_llm": settings.veridical_fake_llm,
        "env": settings.veridical_env,
    }
