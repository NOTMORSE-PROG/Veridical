"""V-049: the model pool — the free tier meters requests-per-day PER MODEL,
so the queue spends a list of independent quota islands rather than one
pinned model.

Pure unit tests (no DB): pool file parsing and the packaged pool's own
invariants. Failover behaviour against real quota rows lives in
test_llm_queue.py (needs a live Postgres).
"""

import json

import pytest

from app.llm.pool import ModelPoolError, ModelSpec, load_model_pool, pool_daily_capacity


def _write_pool(tmp_path, models):
    path = tmp_path / "pool.json"
    path.write_text(json.dumps({"models": models}), encoding="utf-8")
    return path


def test_packaged_pool_loads_and_is_ordered_by_capacity():
    pool = load_model_pool()
    assert pool, "the packaged pool must never be empty"
    limits = [spec.daily_quota for spec in pool]
    assert limits == sorted(limits, reverse=True), (
        "the pool is tried in order, so the biggest island must come first: a "
        "small head model would switch models mid-day and break the "
        "same-manuscript-twice-agrees claim (V3 exit checkpoint)."
    )


def test_packaged_pool_can_serve_a_vision_call():
    """The V-007 vision pass has no fallback if no pooled model is
    multimodal — it would fail closed on every image."""
    assert any(spec.vision for spec in load_model_pool())


def test_packaged_pool_capacity_exceeds_one_manuscript():
    """D-014's whole point: a 20/day head model cannot even finish one
    manuscript (~17 calls) twice. The pool must clear that bar with room."""
    assert pool_daily_capacity(load_model_pool()) > 100


def test_capacity_is_the_sum_of_islands():
    pool = (
        ModelSpec(model="a", rpm=5, daily_quota=20, vision=True),
        ModelSpec(model="b", rpm=5, daily_quota=180, vision=False),
    )
    assert pool_daily_capacity(pool) == 200


def test_pool_rejects_duplicate_models(tmp_path):
    """A duplicate would double-count ONE real island and let the queue
    believe it has capacity that does not exist."""
    path = _write_pool(
        tmp_path,
        [
            {"model": "dup", "rpm": 5, "daily_quota": 20},
            {"model": "dup", "rpm": 5, "daily_quota": 20},
        ],
    )
    with pytest.raises(ModelPoolError, match="more than once"):
        load_model_pool(path)


def test_pool_rejects_empty_and_nonpositive_entries(tmp_path):
    with pytest.raises(ModelPoolError, match="no models"):
        load_model_pool(_write_pool(tmp_path, []))
    with pytest.raises(ModelPoolError, match="non-positive"):
        load_model_pool(_write_pool(tmp_path, [{"model": "m", "rpm": 0, "daily_quota": 20}]))


def test_pool_reads_vision_flag_and_note(tmp_path):
    path = _write_pool(
        tmp_path,
        [{"model": "m", "rpm": 5, "daily_quota": 20, "vision": True, "note": "measured"}],
    )
    (spec,) = load_model_pool(path)
    assert (spec.model, spec.rpm, spec.daily_quota, spec.vision, spec.note) == (
        "m",
        5,
        20,
        True,
        "measured",
    )
