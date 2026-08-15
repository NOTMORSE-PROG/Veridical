"""Settings screen data (F8.9 display slice, V-042, screen 4u)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.audit import AuditLog
from app.settings.schemas import PromptVersionOut, SettingsOut, ThresholdsOut

# TODO(V-052): once BYOK ships (per-instructor keys/models), this query's
# lack of instructor scoping stops being harmless. Today it's correct --
# every instructor shares one Gemini pool/config, so "which prompt/model
# versions are running" is the same true answer for everyone, and
# audit/service.py's own instructor-scoped query already excludes rows
# with no check_run_id (rubric decomposition, for one), which would
# silently hide exactly the versions this screen exists to disclose. Once
# V-052 lands, an unscoped read here would disclose one instructor's own
# live model/prompt configuration to every other instructor -- revisit
# scoping at that point (backend-critic finding, V-042).


async def get_settings_view(session: AsyncSession, settings: Settings) -> SettingsOut:
    thresholds = ThresholdsOut(
        ready_min_score=settings.scoring_ready_min_score,
        not_ready_max_score=settings.scoring_not_ready_max_score,
        escalation_agreement_threshold=settings.escalation_agreement_threshold,
    )

    # backend-critic finding (P2): a shared `LIMIT N` window over ALL
    # prompt types, newest-first, can silently drop a real, currently-
    # running LOW-frequency prompt type (e.g. rubric_decomposition, once
    # per rubric upload) once a HIGH-frequency one (semantic_grading, once
    # per criterion per manuscript) fills the window during a real
    # defense-day burst -- an honest absence, but one that defeats this
    # screen's whole "in use" claim. `DISTINCT ON` gets the single most
    # recent row PER prompt_type directly in SQL instead, with no window
    # size to ever outgrow.
    prompt_type_col = AuditLog.payload["prompt_type"].astext
    rows = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.event_type.in_(("llm_call", "llm_cache_hit")))
            .where(prompt_type_col.isnot(None))
            .where(AuditLog.prompt_version.isnot(None))
            .distinct(prompt_type_col)
            .order_by(prompt_type_col, AuditLog.created_at.desc())
        )
    ).all()

    prompt_versions = []
    for row in rows:
        payload = row.payload or {}
        prompt_type = payload.get("prompt_type")
        model = payload.get("model")
        if not prompt_type or not model or row.prompt_version is None:
            continue
        prompt_versions.append(
            PromptVersionOut(
                prompt_type=prompt_type,
                prompt_version=row.prompt_version,
                model=model,
                observed_at=row.created_at,
            )
        )

    return SettingsOut(
        thresholds=thresholds,
        prompt_versions=sorted(prompt_versions, key=lambda p: p.prompt_type),
    )
