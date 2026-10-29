"""BUG-152: the local embedding model load previously had no error
handling at all -- a raw huggingface_hub/requests exception on a cold
process (Render's disk is ephemeral, so this is a real network dependency
on every spin-down, not just first-ever boot) would propagate as
`run_check_run`'s generic "unexpected_error" catch-all, honest about not
hanging but not about WHY. No live Postgres needed -- pure unit test of
the load wrapper.

BUG-532: the model is now loaded on first use and dropped after it has been
idle, so an idle server does not hold it.
"""

import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import Settings
from app.errors import ApiDownError
from app.ml import embeddings

MODEL_ID = "minishlab/potion-base-8M"


class _FakeModel:
    def __init__(self, source):
        self.source = source

    def encode(self, texts):
        return [[float(len(text)), 1.0] for text in texts]


@pytest.fixture(autouse=True)
def _fresh_model_state():
    embeddings.release_embedding_model()
    embeddings._prefetched.clear()
    yield
    embeddings.release_embedding_model()
    embeddings._prefetched.clear()


def _settings(idle_seconds):
    return Settings(_env_file=None, embedding_model_idle_release_seconds=idle_seconds)


def test_model_load_failure_raises_an_honest_api_down_error():
    with (
        patch(
            "model2vec.StaticModel.from_pretrained",
            side_effect=OSError("Could not reach huggingface.co"),
        ),
        pytest.raises(ApiDownError, match="Could not load the local embedding model"),
    ):
        embeddings._load_model(MODEL_ID)


def test_a_failed_load_is_not_cached_and_retries_in_full_next_call():
    """A transient network blip must not permanently break every later check
    in this process."""
    calls = 0

    def _flaky(source):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("transient")
        return _FakeModel(source)

    with patch("model2vec.StaticModel.from_pretrained", side_effect=_flaky):
        with pytest.raises(ApiDownError):
            embeddings.embed_texts(["a"], _settings(0))
        assert not embeddings.embedding_model_loaded()
        assert embeddings.embed_texts(["abc"], _settings(0)) == [[3.0, 1.0]]
    assert calls == 2


def test_the_model_loads_once_for_repeated_use():
    with patch("model2vec.StaticModel.from_pretrained", side_effect=_FakeModel) as load:
        for _ in range(5):
            embeddings.embed_texts(["a", "bb"], _settings(0))
    assert load.call_count == 1
    assert embeddings.embedding_model_loaded()


def test_an_idle_model_is_released_and_reloaded_on_next_use():
    with patch("model2vec.StaticModel.from_pretrained", side_effect=_FakeModel) as load:
        assert embeddings.embed_texts(["ab"], _settings(0.05)) == [[2.0, 1.0]]
        assert embeddings.embedding_model_loaded()
        deadline = time.monotonic() + 5
        while embeddings.embedding_model_loaded() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not embeddings.embedding_model_loaded()
        assert embeddings.embed_texts(["abc"], _settings(0.05)) == [[3.0, 1.0]]
    assert load.call_count == 2


def test_zero_idle_seconds_keeps_the_model_loaded():
    with patch("model2vec.StaticModel.from_pretrained", side_effect=_FakeModel):
        embeddings.embed_texts(["ab"], _settings(0))
        time.sleep(0.2)
        assert embeddings.embedding_model_loaded()
        assert embeddings._release_timer is None


def test_a_model_in_use_is_never_released_under_its_caller():
    entered, finish = threading.Event(), threading.Event()

    class _SlowModel(_FakeModel):
        def encode(self, texts):
            entered.set()
            finish.wait(5)
            return super().encode(texts)

    with patch("model2vec.StaticModel.from_pretrained", side_effect=_SlowModel) as load:
        worker = threading.Thread(target=embeddings.embed_texts, args=(["ab"], _settings(0.01)))
        worker.start()
        assert entered.wait(5)
        assert embeddings.release_embedding_model() is False
        time.sleep(0.1)
        assert embeddings.embedding_model_loaded()
        # A second caller shares the loaded copy instead of loading another.
        assert embeddings.embed_texts(["abc"], _settings(0.01)) == [[3.0, 1.0]]
        finish.set()
        worker.join(5)
    assert load.call_count == 1


def test_the_idle_timer_never_releases_a_model_that_is_being_used():
    entered, finish = threading.Event(), threading.Event()

    class _SlowModel(_FakeModel):
        def encode(self, texts):
            entered.set()
            finish.wait(5)
            return super().encode(texts)

    with patch("model2vec.StaticModel.from_pretrained", side_effect=_SlowModel) as load:
        # A short first use arms the timer; a slow second use is still running
        # when it fires.
        finish.set()
        embeddings.embed_texts(["a"], _settings(0.05))
        worker = threading.Thread(target=embeddings.embed_texts, args=(["ab"], _settings(0.05)))
        finish.clear()
        entered.clear()
        worker.start()
        assert entered.wait(5)
        time.sleep(0.3)
        assert embeddings.embedding_model_loaded()
        finish.set()
        worker.join(5)
        assert load.call_count == 1
        deadline = time.monotonic() + 5
        while embeddings.embedding_model_loaded() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not embeddings.embedding_model_loaded()


def test_a_prefetched_copy_is_loaded_from_local_disk(tmp_path):
    folder = tmp_path / "snapshot"
    folder.mkdir()
    with patch("huggingface_hub.snapshot_download", return_value=str(folder)) as download:
        assert embeddings.prefetch_embedding_model(_settings(0)) == folder
    download.assert_called_once_with(MODEL_ID, repo_type="model")
    assert not embeddings.embedding_model_loaded()
    with patch("model2vec.StaticModel.from_pretrained", side_effect=_FakeModel) as load:
        embeddings.embed_texts(["a"], _settings(0))
    load.assert_called_once_with(str(folder))


def test_without_a_prefetched_copy_the_model_id_is_loaded():
    embeddings._prefetched[MODEL_ID] = Path("does-not-exist-anywhere")
    with patch("model2vec.StaticModel.from_pretrained", side_effect=_FakeModel) as load:
        embeddings.embed_texts(["a"], _settings(0))
    load.assert_called_once_with(MODEL_ID)


def test_an_idle_api_process_does_not_import_the_heavy_check_libraries():
    """The F6 statistics stack (statcheck -> pandas, scipy.stats), the
    embedding library and numpy cost ~100 MiB of an idle server's memory; they
    are imported when a check needs them, not when the app starts."""
    probe = (
        "import sys, app.main; "
        "print(','.join(m for m in ('pandas', 'scipy', 'statcheck', 'model2vec', "
        "'numpy', 'tokenizers') if m in sys.modules))"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout.strip()
    assert loaded == ""
