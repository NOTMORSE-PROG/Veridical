"""F7 batching bounds encoder invocations without losing late source evidence."""

import pytest
from pydantic import ValidationError

import app.checks.reuse.embed as embed
from app.config import Settings
from app.ml.embeddings import embed_texts
from tests.test_checks_reuse_embed import _block, _extraction


@pytest.mark.parametrize("n_passages", [7, 5001])
def test_encoder_calls_are_bounded_and_all_passages_survive(monkeypatch, n_passages):
    settings = Settings().model_copy(
        update={"reuse_passage_embedding_batch_size": 3, "reuse_passage_chunk_words": 5}
    )
    blocks = [
        _block(f"Unique passage{i} supports exact original source.") for i in range(n_passages)
    ]
    calls = []

    def bounded(texts, _settings):
        assert len(texts) <= 3, "The encoder must not receive the whole dense document"
        calls.append(tuple(texts))
        return [[float(len(text))] for text in texts]

    monkeypatch.setattr(embed, "embed_texts", bounded)
    passages = embed.compute_passage_embeddings(_extraction(blocks), settings)
    assert len(passages) == n_passages
    assert [passage.passage_index for passage in passages] == list(range(n_passages))
    assert [passage.text for passage in passages] == [block.text for block in blocks]
    assert [text for batch in calls for text in batch] == [block.text for block in blocks]
    assert f"passage{n_passages - 1}" in passages[-1].text


@pytest.mark.parametrize("size", [1, 3, 8])
def test_cached_local_model_passages_equal_monolithic_encoding(monkeypatch, size):
    blocks = [
        _block(f"Observation {i} has supported evidence with original source words.")
        for i in range(11)
    ]
    settings = Settings().model_copy(
        update={"reuse_passage_embedding_batch_size": size, "reuse_passage_chunk_words": 7}
    )
    passages = embed.compute_passage_embeddings(_extraction(blocks), settings)
    expected = embed_texts([passage.text for passage in passages], settings)
    assert [passage.embedding for passage in passages] == expected


@pytest.mark.parametrize("size", [0, -1])
def test_invalid_embedding_batch_sizes_are_rejected(size):
    with pytest.raises(ValidationError):
        Settings(reuse_passage_embedding_batch_size=size)


@pytest.mark.parametrize("malformation", ["short", "long"])
def test_malformed_encoder_batch_cannot_shift_later_vectors(monkeypatch, malformation):
    settings = Settings().model_copy(
        update={"reuse_passage_embedding_batch_size": 3, "reuse_passage_chunk_words": 5}
    )
    calls = 0

    def malformed(texts, _settings):
        nonlocal calls
        calls += 1
        count = len(texts) - 1 if malformation == "short" else len(texts) + 1
        return [[1.0] for _ in range(count)]

    monkeypatch.setattr(embed, "embed_texts", malformed)
    with pytest.raises(ValueError):
        embed.compute_passage_embeddings(
            _extraction([_block(f"Passage {i} has supported exact evidence.") for i in range(7)]),
            settings,
        )
    assert calls == 1
