"""Library preparation bounds vectors while ordinary checks keep late evidence."""

from dataclasses import asdict
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import app.checks.reuse.embed as embed
from app.config import Settings
from app.ingest.schemas import SectionNode
from tests.test_checks_reuse_embed import _block, _extraction


def _settings(limit=3):
    return Settings().model_copy(
        update={
            "reuse_max_passages_per_manuscript": limit,
            "reuse_passage_chunk_words": 5,
            "reuse_passage_embedding_batch_size": 2,
        }
    )


def _source(count=11):
    return _extraction(
        [_block(f"Passage {i} contains original software evidence.") for i in range(count)]
    )


@pytest.mark.parametrize("limit", [1, 3, 11, 20])
def test_library_budget_preserves_exact_full_prefix_and_counts(monkeypatch, limit):
    settings = _settings(limit)
    source = _source()
    encoder_calls = []

    def encode(texts, _settings):
        encoder_calls.extend(texts)
        return [[float(len(text)), float(sum(map(ord, text)))] for text in texts]

    monkeypatch.setattr(embed, "embed_texts", encode)
    full = embed.compute_passage_embeddings(source, settings)
    encoder_calls.clear()
    context_calls = []
    original = embed._add_context

    def context(*args, **kwargs):
        context_calls.append(args[1:3])
        return original(*args, **kwargs)

    monkeypatch.setattr(embed, "_add_context", context)
    result = embed.compute_library_passage_embeddings(source, settings)
    assert result.passages_found == len(full) == 11
    assert result.limit == limit
    assert result.inventory_state == "measured"
    assert [asdict(p) for p in result.passages] == [asdict(p) for p in full[:limit]]
    assert encoder_calls == [p.text for p in full[:limit]]
    assert len(context_calls) == min(limit, len(full))


def test_library_budget_does_not_truncate_ordinary_manuscript(monkeypatch):
    settings = _settings()
    monkeypatch.setattr(embed, "embed_texts", lambda texts, _: [[1.0] for _ in texts])
    source = _source(5001)
    limited = embed.compute_library_passage_embeddings(source, settings)
    ordinary = embed.compute_passage_embeddings(source, settings)
    assert len(limited.passages) == 3 and limited.passages_found == 5001
    assert len(ordinary) == 5001
    assert ordinary[-1].passage_index == 5000
    assert "5000" in ordinary[-1].text


def test_refused_inventory_is_distinct_from_measured_empty(monkeypatch):
    settings = _settings()
    empty = embed.compute_library_passage_embeddings(_extraction([]), settings)
    assert empty.inventory_state == "measured" and empty.passages_found == 0
    assert empty.passages == []
    monkeypatch.setattr(
        embed, "select_references", lambda *_: SimpleNamespace(issues=["ambiguous"])
    )
    refused = embed.compute_library_passage_embeddings(_source(), settings)
    assert refused.inventory_state == "unavailable" and refused.passages_found is None
    assert refused.passages == []


@pytest.mark.parametrize("limit", [-1])
def test_library_budget_rejects_negative_limit(limit):
    with pytest.raises(ValidationError):
        Settings(reuse_max_passages_per_manuscript=limit)


def test_zero_budget_preserves_existing_disabled_passage_storage(monkeypatch):
    settings = Settings(reuse_max_passages_per_manuscript=0).model_copy(
        update={"reuse_passage_chunk_words": 5}
    )
    monkeypatch.setattr(embed, "embed_texts", lambda *_: pytest.fail("Zero budget must not encode"))
    result = embed.compute_library_passage_embeddings(_source(), settings)
    assert result.passages == [] and result.passages_found == 11 and result.limit == 0


def test_library_budget_refuses_malformed_encoder_batch(monkeypatch):
    monkeypatch.setattr(embed, "embed_texts", lambda *_: [])
    with pytest.raises(ValueError):
        embed.compute_library_passage_embeddings(_source(), _settings())


def test_cached_model_library_prefix_is_identical_to_full_computation():
    source = _source()
    settings = _settings()
    full = embed.compute_passage_embeddings(source, settings)
    limited = embed.compute_library_passage_embeddings(source, settings)
    assert limited.passages_found == len(full)
    assert limited.passages == full[: settings.reuse_max_passages_per_manuscript]


def test_library_prefix_keeps_existing_unstructured_reference_order():
    source = _extraction(
        [
            _block("The software method records supported source evidence for this study.", page=1),
            _block("References", page=2),
            _block("Doe, J. (2020). A study of things. Journal of Studies, 1(1), 1-10.", page=2),
        ],
        [SectionNode(title="References", level=1, page=2)],
    )
    settings = _settings(1)
    full = embed.compute_passage_embeddings(source, settings)
    limited = embed.compute_library_passage_embeddings(source, settings)
    assert len(full) > 1 and full[0].is_reference_list
    assert limited.passages == full[:1] and limited.passages_found == len(full)
