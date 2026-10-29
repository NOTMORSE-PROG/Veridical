"""Shared Tier-1 local embedding primitive (D-011): potion-base-8M
(MinishLab's model2vec), reused for every similarity use case the cascade
names for it: intent<->outcome candidate pairing (V-035), the
originality/reuse archive (V-036/V-037).

RSS measured fresh 2026-08-08, re-verified rather than assumed from V-030's
unrelated finding (DECISIONS.md D-011 addendum): loaded+warm ALONE, this
model peaks at ~102MB and is stable across repeat calls (no leak). Local NLI
(the OTHER half of D-011's Tier-1 plan) measured separately and did NOT clear
that gate (same addendum); entailment/contradiction judgment stays Tier 2
(Gemini), only candidate-generation similarity is local.

The model is loaded when a check first needs it and dropped again once it
has been idle for `embedding_model_idle_release_seconds` (BUG-532). Loading
it at boot and holding it for the life of the process kept ~70 MiB resident
(measured on the production image) on a 512 MiB host even while nobody was
checking anything, and that is the headroom a large upload's preparation
runs out of. Boot
still downloads the files (`prefetch_embedding_model`), so a model-host
outage shows up at boot as before, and a later load reads the local copy.
The same files load into the same arrays, so vectors do not change.
"""

import math
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import Settings, get_settings
from app.errors import ApiDownError
from app.memory import return_freed_memory

if TYPE_CHECKING:
    from model2vec import StaticModel

_lock = threading.Lock()
_model: "StaticModel | None" = None
_model_id: str | None = None
_in_use = 0
_last_used = 0.0
_release_timer: threading.Timer | None = None
_idle_seconds = 0.0
# Local copies downloaded at boot, by model id.
_prefetched: dict[str, Path] = {}


def _load_model(model_id: str) -> "StaticModel":
    # BUG-152: nothing previously handled the model host being unreachable
    # on a cold process (Render's ephemeral disk means this is a real
    # network dependency on every spin-down, not just the first-ever
    # boot) -- a raw huggingface_hub/requests exception would otherwise
    # propagate as `run_check_run`'s generic "unexpected_error" catch-all,
    # honest about not hanging but not about WHY (charter rule 9: unverifiable
    # is not fake, api-down is not unverifiable -- this is neither, it's a
    # third check-couldn't-run cause and deserves its own name). Nothing is
    # remembered on failure, so a transient error is retried in full on the
    # very next call, not stuck.
    try:
        # Imported here: the library and numpy stay out of an idle process.
        from model2vec import StaticModel

        local = _prefetched.get(model_id)
        source = str(local) if local is not None and local.is_dir() else model_id
        return StaticModel.from_pretrained(source)
    except Exception as exc:  # noqa: BLE001 -- any load failure is the same honest cause here
        raise ApiDownError(
            f"Could not load the local embedding model ({model_id}). "
            "The model host may be unreachable; try again shortly."
        ) from exc


def prefetch_embedding_model(settings: Settings | None = None) -> Path:
    """Download the model files to local disk without loading them.

    Boot calls this so the network fetch happens where a failure is visible
    to operators, not inside an instructor's check, while the model itself
    stays out of memory until a check needs it."""
    settings = settings or get_settings()
    from huggingface_hub import snapshot_download

    folder = Path(snapshot_download(settings.embedding_model_id, repo_type="model"))
    _prefetched[settings.embedding_model_id] = folder
    return folder


def _schedule_release_locked(delay: float) -> None:
    global _release_timer
    if _release_timer is not None:
        return
    timer = threading.Timer(delay, _release_if_idle)
    timer.daemon = True
    _release_timer = timer
    timer.start()


def _release_if_idle() -> None:
    global _model, _model_id, _release_timer
    with _lock:
        _release_timer = None
        if _model is None or _idle_seconds <= 0:
            return
        remaining = _last_used + _idle_seconds - time.monotonic()
        if _in_use or remaining > 0:
            # Still being used: look again when the idle window would end.
            _schedule_release_locked(max(remaining, _idle_seconds / 10, 0.01))
            return
        _model, _model_id = None, None
    return_freed_memory()


def release_embedding_model() -> bool:
    """Drop the loaded model now unless a caller is using it. Returns whether
    it was dropped (or nothing was loaded)."""
    global _model, _model_id, _release_timer
    with _lock:
        if _in_use:
            return False
        if _release_timer is not None:
            _release_timer.cancel()
            _release_timer = None
        dropped = _model is not None
        _model, _model_id = None, None
    if dropped:
        return_freed_memory()
    return True


def embedding_model_loaded() -> bool:
    return _model is not None


@contextmanager
def _borrowed_model(settings: Settings) -> Iterator["StaticModel"]:
    global _model, _model_id, _in_use, _last_used, _idle_seconds
    model_id = settings.embedding_model_id
    with _lock:
        if _model is None or _model_id != model_id:
            # Load under the lock so two threads never hold two copies.
            _model, _model_id = None, None
            _model, _model_id = _load_model(model_id), model_id
        _in_use += 1
        model = _model
    try:
        yield model
    finally:
        with _lock:
            _in_use -= 1
            _last_used = time.monotonic()
            _idle_seconds = settings.embedding_model_idle_release_seconds
            if _idle_seconds > 0:
                _schedule_release_locked(_idle_seconds)


def get_embedding_model(settings: Settings | None = None) -> "StaticModel":
    """The loaded model (loading it if needed). The idle release still applies,
    so keep the returned object only for the call at hand."""
    settings = settings or get_settings()
    with _borrowed_model(settings) as model:
        return model


def embed_texts(texts: list[str], settings: Settings | None = None) -> list[list[float]]:
    """Deterministic: same text + same pinned model -> same vector (ticket
    requirement, V-036) — potion-base-8M is a static (non-contextual)
    embedding, so this holds by construction, not by convention."""
    if not texts:
        return []
    settings = settings or get_settings()
    with _borrowed_model(settings) as model:
        return [[float(x) for x in vector] for vector in model.encode(texts)]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
