"""Ordered requested models and configured local budgets (F1.3/F3, D-033).

Membership, order, limits and historical budget aliases are data. Their sum
describes this application's configured budget, not a measured provider allowance.
Aliases conservatively share a local cap without asserting provider quota sharing.
"""

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

_DEFAULT_FILE = Path(__file__).parent / "data" / "model_pool.json"


@dataclass(frozen=True)
class ModelSpec:
    """One active requested model and its conservative local budget."""

    model: str
    rpm: int
    daily_quota: int
    # Whether this model accepts image Parts — the V-007 vision pass must
    # never fail over onto a text-only model and silently drop the images.
    vision: bool
    note: str = ""
    budget_aliases: tuple[str, ...] = ()
    # Optional per-minute INPUT token budget used to pace large prompts (a
    # full manuscript for grading). Absent = request-rate pacing only.
    tpm: int | None = None


class ModelPoolError(ValueError):
    """The pool file is unusable — fail loudly at startup, never silently
    fall back to a single model and re-create the 20/day cliff."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in pairs:
        if key in values:
            raise ModelPoolError(f"Model pool contains duplicate JSON field {key!r}.")
        values[key] = value
    return values


def load_model_pool(path: Path | None = None) -> tuple[ModelSpec, ...]:
    raw = json.loads(
        (path or _DEFAULT_FILE).read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object
    )
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
        tpm = entry.get("tpm")
        if tpm is not None and (type(tpm) is not int or tpm < 1):
            raise ModelPoolError(f"Model pool entry {model!r} has an invalid tpm.")
        specs.append(
            ModelSpec(
                model=model,
                rpm=rpm,
                daily_quota=daily_quota,
                vision=bool(entry.get("vision", False)),
                note=str(entry.get("note", "")),
                tpm=tpm,
            )
        )
    aliases = raw.get("budget_aliases", {})
    if not isinstance(aliases, dict):
        raise ModelPoolError("Budget aliases must be an alias-to-active-model mapping.")
    by_target: dict[str, list[str]] = {spec.model: [] for spec in specs}
    seen_aliases: set[str] = set()
    for alias_value, target_value in aliases.items():
        if not isinstance(alias_value, str) or not isinstance(target_value, str):
            raise ModelPoolError("Each budget alias and target must be a nonblank model id.")
        alias, target = alias_value.strip(), target_value.strip()
        if not alias or not target:
            raise ModelPoolError("Each budget alias and target must be a nonblank model id.")
        if alias in seen_aliases:
            raise ModelPoolError(f"Budget alias {alias!r} is assigned more than once.")
        if alias in seen:
            raise ModelPoolError(f"Budget alias {alias!r} is also an active model.")
        if target not in seen:
            raise ModelPoolError(f"Budget alias {alias!r} must target an active model.")
        seen_aliases.add(alias)
        by_target[target].append(alias)
    return tuple(replace(spec, budget_aliases=tuple(by_target[spec.model])) for spec in specs)


def pool_daily_capacity(pool: tuple[ModelSpec, ...]) -> int:
    """Sum of configured active local caps; no provider-capacity measurement."""
    return sum(spec.daily_quota for spec in pool)
