"""Model pool loader (V-049): WHICH Gemini models the queue may spend, in
what order, with what measured per-model limits.

Why a pool at all: Gemini's free tier meters `GenerateRequestsPerDay*Per
Model*-FreeTier` — the 429's own `quotaDimensions` name the model, so each
model id carries its OWN independent daily allowance. A single pinned model
therefore caps the whole product at that one model's RPD (measured
2026-07-26: 20/day for `gemini-3.5-flash`), while the same key can still
serve calls on every other model.

The pool is DATA, not code (ground rule 7 / ENGINEERING §8): ordering,
limits, and membership are all edited in the JSON file — no code change —
because these numbers drift (they already did, by 70x) and because the
owner may reasonably disagree with the quality-vs-capacity ordering.
"""

import json
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_FILE = Path(__file__).parent / "data" / "model_pool.json"


@dataclass(frozen=True)
class ModelSpec:
    """One model's own quota island."""

    model: str
    rpm: int
    daily_quota: int
    # Whether this model accepts image Parts — the V-007 vision pass must
    # never fail over onto a text-only model and silently drop the images.
    vision: bool
    note: str = ""


class ModelPoolError(ValueError):
    """The pool file is unusable — fail loudly at startup, never silently
    fall back to a single model and re-create the 20/day cliff."""


def load_model_pool(path: Path | None = None) -> tuple[ModelSpec, ...]:
    raw = json.loads((path or _DEFAULT_FILE).read_text(encoding="utf-8"))
    entries = raw.get("models") or []
    if not entries:
        raise ModelPoolError(f"Model pool {path or _DEFAULT_FILE} lists no models.")

    specs: list[ModelSpec] = []
    seen: set[str] = set()
    for entry in entries:
        model = str(entry["model"]).strip()
        if not model:
            raise ModelPoolError("Model pool contains an entry with a blank model id.")
        if model in seen:
            # Duplicates would double-count one real quota island and let the
            # queue believe it has capacity it does not have.
            raise ModelPoolError(f"Model pool lists {model!r} more than once.")
        seen.add(model)
        rpm = int(entry["rpm"])
        daily_quota = int(entry["daily_quota"])
        if rpm < 1 or daily_quota < 1:
            raise ModelPoolError(f"Model pool entry {model!r} has a non-positive rpm/daily_quota.")
        specs.append(
            ModelSpec(
                model=model,
                rpm=rpm,
                daily_quota=daily_quota,
                vision=bool(entry.get("vision", False)),
                note=str(entry.get("note", "")),
            )
        )
    return tuple(specs)


def pool_daily_capacity(pool: tuple[ModelSpec, ...]) -> int:
    """Total calls/day this key can actually spend — the number every
    capacity claim (D-001, D-011, the paper) must be priced against."""
    return sum(spec.daily_quota for spec in pool)
