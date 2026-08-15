"""Settings screen data (F8.9 display slice, V-042, screen 4u; BYOK V-052)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ConflictError
from app.llm import evict_instructor_client
from app.llm.keystore import encrypt_api_key, validate_gemini_api_key
from app.models.audit import AuditLog
from app.models.instructor import Instructor
from app.settings.schemas import ApiKeyStatusOut, PromptVersionOut, SettingsOut, ThresholdsOut

# TODO(V-052 follow-up): the prompt/model-version query below is STILL
# unscoped by instructor -- now genuinely incomplete rather than merely
# forward-looking, since an instructor using their own BYOK key spends a
# DIFFERENT key/model than the shared pool, and `audit_log` has no
# instructor_id column to scope by (it only carries `check_run_id`, and
# rubric-decomposition calls have none at all -- see the prior version of
# this comment, V-042). Left as a known, disclosed gap: this screen can
# show a BYOK instructor prompt/model versions that were actually served
# by someone else's shared-pool call, or vice versa. Not fixed in V-052
# (BYOK's own ACs don't require it, and fixing it means either adding
# `instructor_id` to `audit_log` or a second, differently-scoped query --
# a real design decision, not a one-line fix) -- revisit if this becomes
# actually misleading in practice, not just theoretically imprecise.


async def get_settings_view(
    session: AsyncSession, settings: Settings, instructor: Instructor
) -> SettingsOut:
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
        api_key=ApiKeyStatusOut(
            # `bool(...)`, not `is not None` (backend-critic finding, P1,
            # live-reproduced): `.env.example` ships `BYOK_ENCRYPTION_KEY=`
            # blank, which pydantic-settings reads as `""`, not `None` --
            # an `is not None` check would report BYOK "available" on
            # exactly the deployment state the blank template documents as
            # "not configured yet", inviting a save that then hard-fails
            # inside `encrypt_api_key`. Falsy check matches
            # `keystore._fernet`'s own guard, which was already correct.
            byok_available=bool(settings.byok_encryption_key),
            has_own_api_key=instructor.gemini_api_key_encrypted is not None,
        ),
    )


async def set_instructor_api_key(
    session: AsyncSession, settings: Settings, instructor: Instructor, api_key: str
) -> None:
    """Validates with a real probe call BEFORE ever touching the database
    (AC) -- an invalid key never gets encrypted and stored even
    transiently. `validate_gemini_api_key` raises `InvalidApiKeyError`
    (422, user-fixable) on any non-2xx response; that propagates as-is."""
    if not settings.byok_encryption_key:
        # A deployment-configuration gap, not a user-fixable input error --
        # the frontend also hides this behind `api_key.byok_available`, so
        # reaching this means that check was bypassed (a stale page, a
        # direct API call), not the normal path.
        raise ConflictError("Bringing your own API key isn't available on this deployment yet.")
    await validate_gemini_api_key(api_key, settings)
    instructor.gemini_api_key_encrypted = encrypt_api_key(api_key, settings)
    await session.commit()
    # backend-critic finding (P2): a rotated key must drop the OLD
    # decrypted key + its live GeminiLLMClient from the process-wide
    # cache, not just become unreachable through routing -- the stored
    # blob comparison alone only self-invalidates on the NEXT lookup.
    evict_instructor_client(instructor.id)


async def delete_instructor_api_key(session: AsyncSession, instructor: Instructor) -> None:
    """Reverts to the shared pool key, and evicts any cached client for
    this instructor (backend-critic finding, P2) so the removed key's
    decrypted material and its live `GeminiLLMClient` are actually
    collectible, not merely unreachable via `get_llm_client_for`'s own
    `gemini_api_key_encrypted is None` check."""
    instructor.gemini_api_key_encrypted = None
    await session.commit()
    evict_instructor_client(instructor.id)
