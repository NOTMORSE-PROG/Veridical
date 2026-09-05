"""Shared Tier-1 local embedding primitive (D-011): potion-base-8M
(MinishLab's model2vec), loaded once per process and reused for every
similarity use case the cascade names for it — intent<->outcome candidate
pairing (V-035), the originality/reuse archive (V-036/V-037).

RSS measured fresh 2026-08-08, re-verified rather than assumed from V-030's
unrelated finding (DECISIONS.md D-011 addendum): loaded+warm ALONE, this
model peaks at ~102MB and is stable across repeat calls (no leak) — well
inside Render's 512MB free tier even combined with the rest of the app.
Local NLI (the OTHER half of D-011's Tier-1 plan) measured separately and
did NOT clear that gate (same addendum) — entailment/contradiction
judgment stays Tier 2 (Gemini), only candidate-generation similarity is
local. `lru_cache` keeps this a true per-process singleton: the model
loads once no matter how many check_runs or callers ask for it.
"""

import math
from functools import lru_cache

from model2vec import StaticModel

from app.config import Settings, get_settings
from app.errors import ApiDownError


@lru_cache(maxsize=1)
def _load_model(model_id: str) -> StaticModel:
    # BUG-152: nothing previously handled the model host being unreachable
    # on a cold process (Render's ephemeral disk means this is a real
    # network dependency on every spin-down, not just the first-ever
    # boot) -- a raw huggingface_hub/requests exception would otherwise
    # propagate as `run_check_run`'s generic "unexpected_error" catch-all,
    # honest about not hanging but not about WHY (charter rule 9: unverifiable
    # is not fake, api-down is not unverifiable -- this is neither, it's a
    # third check-couldn't-run cause and deserves its own name). `lru_cache`
    # does not cache a raised exception, so a transient failure here is
    # retried in full on the very next call, not stuck.
    try:
        return StaticModel.from_pretrained(model_id)
    except Exception as exc:  # noqa: BLE001 -- any load failure is the same honest cause here
        raise ApiDownError(
            f"Could not load the local embedding model ({model_id}). "
            "The model host may be unreachable; try again shortly."
        ) from exc


def get_embedding_model(settings: Settings | None = None) -> StaticModel:
    settings = settings or get_settings()
    return _load_model(settings.embedding_model_id)


def embed_texts(texts: list[str], settings: Settings | None = None) -> list[list[float]]:
    """Deterministic: same text + same pinned model -> same vector (ticket
    requirement, V-036) — potion-base-8M is a static (non-contextual)
    embedding, so this holds by construction, not by convention."""
    if not texts:
        return []
    model = get_embedding_model(settings)
    return [[float(x) for x in vector] for vector in model.encode(texts)]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
