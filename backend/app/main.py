"""VERIDICAL API entry point."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db
from app.config import get_settings
from app.errors import HTTP_STATUS, VeridicalError
from app.ingest.router import router as ingest_router
from app.llm.router import router as llm_router

app = FastAPI(
    title="VERIDICAL API",
    version="0.1.0",
    description="Defense-readiness checks for capstone manuscripts.",
)
app.include_router(ingest_router)
app.include_router(llm_router)

_cors_origins = get_settings().cors_allowed_origins_list
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
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
