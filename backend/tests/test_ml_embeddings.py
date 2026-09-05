"""BUG-152: the local embedding model load previously had no error
handling at all -- a raw huggingface_hub/requests exception on a cold
process (Render's disk is ephemeral, so this is a real network dependency
on every spin-down, not just first-ever boot) would propagate as
`run_check_run`'s generic "unexpected_error" catch-all, honest about not
hanging but not about WHY. No live Postgres needed -- pure unit test of
the load wrapper.
"""

from unittest.mock import patch

import pytest

from app.errors import ApiDownError
from app.ml import embeddings


def test_model_load_failure_raises_an_honest_api_down_error():
    embeddings._load_model.cache_clear()
    with (
        patch(
            "app.ml.embeddings.StaticModel.from_pretrained",
            side_effect=OSError("Could not reach huggingface.co"),
        ),
        pytest.raises(ApiDownError, match="Could not load the local embedding model"),
    ):
        embeddings._load_model("minishlab/potion-base-8M")
    embeddings._load_model.cache_clear()


def test_a_failed_load_is_not_cached_and_retries_in_full_next_call():
    """`lru_cache` must not remember a raised exception -- a transient
    network blip should not permanently break every later check in this
    process."""
    embeddings._load_model.cache_clear()
    calls = 0

    def _flaky(model_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("transient")
        return "a real model object"

    with patch("app.ml.embeddings.StaticModel.from_pretrained", side_effect=_flaky):
        with pytest.raises(ApiDownError):
            embeddings._load_model("minishlab/potion-base-8M")
        assert embeddings._load_model("minishlab/potion-base-8M") == "a real model object"
    assert calls == 2
    embeddings._load_model.cache_clear()
