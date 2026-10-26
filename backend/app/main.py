"""VERIDICAL API entry point."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import PlainTextResponse, Response

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
from app.ingest.admission import (
    begin_preparation_shutdown,
    finish_preparation_lifespan,
    start_preparation_admission,
)
from app.ingest.router import router as ingest_router
from app.library.router import router as library_router
from app.llm.router import router as llm_router
from app.ml.embeddings import get_embedding_model
from app.pipeline.router import router as pipeline_router
from app.pipeline.worker import worker_loop
from app.report.router import (
    export_report_pdf_route,
    get_evidence_location_route,
    get_evidence_manifest_route,
    get_evidence_page_route,
    get_evidence_source_document_route,
    get_evidence_source_file_route,
    get_manuscript_docx_image_route,
    get_manuscript_file_route,
    get_manuscript_page_text_route,
    get_manuscript_paragraphs_route,
    get_manuscript_viewer_route,
    list_reuse_passage_matches_route,
)
from app.report.router import router as report_router
from app.rubric.router import router as rubric_router
from app.rubric.router import (
    rubric_document_route,
    rubric_image_route,
    rubric_original_route,
    rubric_paragraphs_route,
    rubric_pdf_route,
)
from app.settings.router import router as settings_router
from app.share.router import (
    PUBLIC_SHARE_PATH_PREFIX,
    PUBLIC_SHARE_RESPONSE_HEADERS,
    PUBLIC_SHARE_SERVER_ERROR_BODY,
)
from app.share.router import (
    router as share_router,
)

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
_PRIVATE_DOCUMENT_CACHE_CONTROL = "private, no-store"
_PRIVATE_DOCUMENT_ENDPOINTS = frozenset(
    {
        get_evidence_manifest_route,
        get_evidence_page_route,
        get_evidence_location_route,
        get_evidence_source_file_route,
        get_evidence_source_document_route,
        get_manuscript_viewer_route,
        list_reuse_passage_matches_route,
        get_manuscript_paragraphs_route,
        get_manuscript_page_text_route,
        get_manuscript_docx_image_route,
        get_manuscript_file_route,
        rubric_document_route,
        rubric_paragraphs_route,
        rubric_pdf_route,
        rubric_original_route,
        rubric_image_route,
    }
)
_PRIVATE_EXPORT_ENDPOINTS = frozenset({export_report_pdf_route})


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

    **Never lets a failure here fail the boot** (backend review finding,
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

    settings = get_settings()
    preparation_handle = start_preparation_admission(settings)
    task = None
    try:
        wake_event = asyncio.Event()
        app.state.pipeline_wake_event = wake_event
        task = asyncio.create_task(
            worker_loop(
                wake_event=wake_event,
                periodic=settings.veridical_env == "prod" or settings.pipeline_worker_autostart,
            ),
            name="pipeline-scheduler",
        )
        yield
    finally:
        retiring = begin_preparation_shutdown(preparation_handle)
        try:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        finally:
            logging.getLogger(__name__).info(
                "Preparation shutdown retains %s lease(s); native threads are not terminated",
                retiring.active_owners,
            )
            finish_preparation_lifespan(preparation_handle)


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
        expose_headers=["X-Evidence-Source-Identity"],
        # The session cookie (V-014) rides on `credentials: "include"` —
        # browsers refuse the response without this, found live testing
        # V-014's sign-in flow (V-048's CORS setup predates any
        # cookie-based auth, so nothing exercised this gap until now).
        allow_credentials=True,
    )


@app.middleware("http")
async def add_public_share_response_headers(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Prevent private-document caching and indexing of bearer-token reports.

    Starlette's user middleware wraps its handled-exception middleware, so
    this applies after successful routing and the global VeridicalError
    handler's fresh 404/410 responses. Its outer server-error middleware would
    otherwise create a fresh headerless 500, so this path converts unexpected
    failures to the same generic body after logging the traceback.
    """
    is_public_share = request.url.path.startswith(PUBLIC_SHARE_PATH_PREFIX)
    # V-079: active Library files are shared with authenticated instructors.
    # Membership/auth errors must be just as uncacheable as successful reads.
    is_library = request.url.path == "/library" or request.url.path.startswith("/library/")
    try:
        response = await call_next(request)
    except Exception:  # noqa: BLE001 -- this boundary must also harden generic 500s
        if not is_public_share and not is_library:
            raise
        _logger.exception("Unhandled failure while serving a protected document response.")
        response = PlainTextResponse(PUBLIC_SHARE_SERVER_ERROR_BODY, status_code=500)

    if is_public_share:
        for name, value in PUBLIC_SHARE_RESPONSE_HEADERS:
            response.headers[name] = value
    # Routing populates endpoint before dependencies or validation can fail.
    # Inspect it after call_next so handled errors use the private policy too.
    endpoint = request.scope.get("endpoint")
    if (
        is_library
        or endpoint in _PRIVATE_DOCUMENT_ENDPOINTS
        or endpoint in _PRIVATE_EXPORT_ENDPOINTS
    ):
        response.headers["Cache-Control"] = _PRIVATE_DOCUMENT_CACHE_CONTROL
    return response


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
