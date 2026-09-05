"""VERIDICAL API entry point."""

import asyncio
import logging
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
from app.library.router import router as library_router
from app.llm.router import router as llm_router
from app.ml.embeddings import get_embedding_model
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


_logger = logging.getLogger(__name__)


async def _prewarm_embedding_model_on_boot() -> None:
    """BUG-152: the shared local embedding model (F4/F7's Tier 1 candidate
    generation) is an `lru_cache`d per-process singleton that previously
    loaded on whichever instructor's request happened to hit it first --
    on Render, whose disk is ephemeral, that means a real HuggingFace
    fetch stacked on top of the ~47s cold-wake this compounds (BUG-142),
    paid by a real check_run instead of at boot where a failure is
    ops-visible rather than silently degrading one instructor's run.
    `asyncio.to_thread` (same reasoning as `_upgrade_to_head` above):
    `StaticModel.from_pretrained` is sync and genuinely blocking, and
    boot has no event-loop traffic to protect yet, but no reason to block
    it anyway now that this runs off-thread everywhere else it's called.

    **Never lets a failure here fail the boot** (`backend-critic` finding,
    live-verified: an exception raised in a FastAPI lifespan BEFORE
    `yield` prevents the app from serving ANY request -- Render's ENTIRE
    process boots fresh on every spin-down, so an unguarded prewarm would
    turn one transient HuggingFace hiccup into total downtime for every
    instructor, not just the F4/F7 checks that actually need the model.
    That is a strictly worse failure than the one this fix exists to
    catch. `get_embedding_model()` is still called lazily, unchanged, on
    first real use if this pre-warm attempt fails -- this is a best-effort
    head start, never a hard boot dependency."""
    try:
        await asyncio.to_thread(get_embedding_model)
    except Exception:  # noqa: BLE001 -- any failure here must never fail the boot
        _logger.warning(
            "Embedding model pre-warm failed at boot; will retry lazily on first "
            "use. This is expected during a real HuggingFace outage and is not, "
            "by itself, a reason the API should be unavailable.",
            exc_info=True,
        )


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
        # BUG-152: same prod-only gate -- a test importing this module
        # must never make a real HuggingFace fetch by surprise.
        await _prewarm_embedding_model_on_boot()

    # BUG-136: this used to be gated ONLY on `pipeline_worker_autostart`
    # (default False), with a comment claiming "production (Render) turns
    # this on" -- but nothing in the deploy actually set that env var, so
    # production silently ran with the loop OFF since the day this
    # shipped. A `quota_exhausted`/`api_down` block's "resumes
    # automatically at <time>" promise (D-001's resumability requirement)
    # was never true in prod: every check_run only ever advanced via the
    # one-time `advance_once` kick at creation, and a run that blocked
    # anywhere in its own single-request walk stayed parked forever,
    # invisible until an instructor happened to notice. Same class of bug
    # D-021 already fixed for migrations, and the same fix: gate on
    # `veridical_env == "prod"` directly (matching the migration/seed
    # gates just above), same reasoning as those -- every TestClient-based
    # test imports this module and must never start a real polling loop
    # against a test's DATABASE_URL by surprise, and a non-prod env can
    # never satisfy this condition. `pipeline_worker_autostart` still
    # exists as an explicit opt-in for exercising the loop outside prod
    # (e.g. local manual testing) without faking `veridical_env`.
    task = None
    settings = get_settings()
    if settings.veridical_env == "prod" or settings.pipeline_worker_autostart:
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
app.include_router(library_router)
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
