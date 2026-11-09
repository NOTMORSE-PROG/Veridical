"""Native quotation uncertainty must survive passage retrieval (F7)."""

from collections import namedtuple
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from docx import Document

from app.checks.reuse import query, source
from app.checks.reuse.embed import PassageEmbedding
from app.config import Settings
from app.ingest.analytical_source import analytical_source
from app.ingest.docx import extract_document
from app.ingest.quotations import located_quote_status

TEXT = (
    "Amber trunks carry copper labels and each numbered marker stays beside its original "
    "branch throughout the recorded inspection."
)


def _source(tmp_path, name, *, style="Normal", prefix=""):
    document = Document()
    document.add_paragraph(prefix + TEXT, style=style)
    path = tmp_path / f"{name}.docx"
    document.save(path)
    result = extract_document(str(path), Settings(_env_file=None))
    native = analytical_source(result)
    assert native.state == "complete" and native.occurrence_count == 0
    return native


def _match(manuscript_id, own_context=TEXT, matched_context=TEXT):
    return query.PassageMatch(
        own_passage_index=0,
        own_chapter_index=0,
        own_page=None,
        own_paragraph=0,
        own_char_start=0,
        own_char_end=len(TEXT),
        own_text=TEXT,
        own_context_text=own_context,
        own_is_reference_list=False,
        own_is_block_quote=False,
        level="exact_duplicate",
        similarity=1.0,
        matched_manuscript_id=manuscript_id,
        matched_group_label="Other research team",
        matched_instructor_id=8,
        matched_chapter_index=0,
        matched_page=None,
        matched_paragraph=0,
        matched_char_start=0,
        matched_char_end=len(TEXT),
        matched_text=TEXT,
        matched_context_text=matched_context,
        matched_is_reference_list=False,
        matched_is_block_quote=False,
    )


async def _query(monkeypatch, sources, matches, *, include_quotes=False, literal=False):
    settings = Settings(_env_file=None, reuse_literal_enabled=literal)
    monkeypatch.setattr(query, "_group_sibling_manuscript_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(query, "passage_archive_size", AsyncMock(return_value=len(matches)))
    monkeypatch.setattr(query, "_passage_candidates_for", AsyncMock(return_value=matches))
    monkeypatch.setattr(
        source, "selected_native_source", AsyncMock(side_effect=lambda _, key, *__: sources[key])
    )
    if literal:
        monkeypatch.setattr(query, "_literal_matches_for", AsyncMock(return_value=[]))
    passage = PassageEmbedding(
        passage_index=0,
        chapter_index=0,
        anchor_kind="paragraph",
        page=None,
        paragraph=0,
        char_start=0,
        char_end=len(TEXT),
        text=TEXT,
        context_text=TEXT,
        is_reference_list=False,
        is_block_quote=False,
        embedding=[1.0],
    )
    return await query.query_similar_passages(
        object(),
        1,
        [passage],
        settings,
        include_block_quote=include_quotes,
        confirm=lambda match: True,
        candidates=len(matches),
    )


@pytest.mark.parametrize("side", ["own", "matched"])
@pytest.mark.parametrize("literal", [False, True])
async def test_unclosed_native_quotation_is_unassessed_not_an_unquoted_match(
    tmp_path, monkeypatch, side, literal
):
    own_prefix, other_prefix = ('"', "") if side == "own" else ("", '"')
    own = _source(tmp_path, "own", prefix=own_prefix)
    other = _source(tmp_path, "other", prefix=other_prefix)
    assert (
        located_quote_status(own if side == "own" else other, TEXT, Settings(_env_file=None))
        is None
    )
    result = await _query(
        monkeypatch,
        {1: own, 2: other},
        [_match(2, own_prefix + TEXT, other_prefix + TEXT)],
        literal=literal,
    )
    assert result.matches == []
    assert result.n_source_candidates_withheld == 1
    if literal:
        assert result.literal_scope["unassessed"] == [
            {
                "passage_index": 0,
                "reason": "source_or_quotation_not_assessed",
                "matched_manuscript_id": 2,
            }
        ]


async def test_known_unquoted_source_remains_a_match(tmp_path, monkeypatch):
    result = await _query(
        monkeypatch, {1: _source(tmp_path, "own"), 2: _source(tmp_path, "other")}, [_match(2)]
    )
    assert len(result.matches) == 1 and result.n_source_candidates_withheld == 0


async def test_known_quote_is_an_exclusion_rather_than_missing_coverage(tmp_path, monkeypatch):
    result = await _query(
        monkeypatch,
        {1: _source(tmp_path, "own"), 2: _source(tmp_path, "other", style="Quote")},
        [_match(2)],
    )
    assert result.matches == [] and result.n_source_candidates_withheld == 0


async def test_unknown_candidate_does_not_hide_a_later_supported_source(tmp_path, monkeypatch):
    result = await _query(
        monkeypatch,
        {
            1: _source(tmp_path, "own"),
            2: _source(tmp_path, "uncertain", prefix='"'),
            3: _source(tmp_path, "known"),
        },
        [_match(2, matched_context='"' + TEXT), _match(3)],
    )
    assert [match.matched_manuscript_id for match in result.matches] == [3]
    assert result.n_source_candidates_withheld == 0


async def test_explicit_quotation_exploration_keeps_source_supported_text(tmp_path, monkeypatch):
    result = await _query(
        monkeypatch,
        {1: _source(tmp_path, "own"), 2: _source(tmp_path, "other", prefix='"')},
        [_match(2, matched_context='"' + TEXT)],
        include_quotes=True,
    )
    assert len(result.matches) == 1 and result.n_source_candidates_withheld == 0


@pytest.mark.parametrize(
    "style,prefix,expected", [("Normal", "", True), ("Quote", "", False), ("Normal", '"', False)]
)
async def test_aggregate_admission_requires_known_unquoted_source(
    tmp_path, monkeypatch, style, prefix, expected
):
    native = _source(tmp_path, "aggregate", style=style, prefix=prefix)
    monkeypatch.setattr(source, "selected_native_source", AsyncMock(return_value=native))
    assert (
        await source.coarse_candidate_supported(object(), 1, Settings(_env_file=None), {})
        is expected
    )


@pytest.mark.parametrize("side", ["own", "matched"])
@pytest.mark.parametrize("style,prefix", [("Normal", '"'), ("Quote", "")])
async def test_default_canonical_validation_rejects_uncertain_or_quoted_vector_text(
    tmp_path, monkeypatch, side, style, prefix
):
    normal = _source(tmp_path, "known")
    protected = _source(tmp_path, "protected", style=style, prefix=prefix)
    sources = {
        1: protected if side == "own" else normal,
        2: protected if side == "matched" else normal,
    }
    monkeypatch.setattr(
        source, "selected_native_source", AsyncMock(side_effect=lambda _, key, *__: sources[key])
    )
    match = _match(
        2, prefix + TEXT if side == "own" else TEXT, prefix + TEXT if side == "matched" else TEXT
    )
    settings = Settings(_env_file=None)
    # Explicit exploration retains native text without claiming an unquoted match.
    assert await source.passage_match_supported(object(), 1, match, settings, {})
    assert not await source.passage_match_supported(
        object(), 1, match, settings, {}, require_unquoted=True
    )


async def test_default_canonical_validation_keeps_known_unquoted_vector_text(tmp_path, monkeypatch):
    native = _source(tmp_path, "known")
    monkeypatch.setattr(source, "selected_native_source", AsyncMock(return_value=native))
    assert await source.passage_match_supported(
        object(), 1, _match(2), Settings(_env_file=None), {}, require_unquoted=True
    )


@pytest.mark.parametrize(
    "style,prefix,expected", [("Normal", "", True), ("Quote", "", False), ("Normal", '"', False)]
)
async def test_supporting_fallback_requires_known_unquoted_library_text(
    tmp_path, monkeypatch, style, prefix, expected
):
    native = _source(tmp_path, "fallback", style=style, prefix=prefix)
    monkeypatch.setattr(source, "selected_native_source", AsyncMock(return_value=native))
    archive = SimpleNamespace(
        manuscript_id=2,
        chapter_index=0,
        page=None,
        paragraph=0,
        char_start=0,
        char_end=len(TEXT),
        text=TEXT,
        context_text=prefix + TEXT,
        is_reference_list=False,
        is_block_quote=False,
    )
    row = namedtuple("ArchiveRow", "archive similarity group_label instructor_id")(
        archive, 1.0, "Other team", 8
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(first=lambda: row)))
    passage = PassageEmbedding(
        passage_index=0,
        chapter_index=0,
        anchor_kind="paragraph",
        page=None,
        paragraph=0,
        char_start=0,
        char_end=len(TEXT),
        text=TEXT,
        context_text=TEXT,
        is_reference_list=False,
        is_block_quote=False,
        embedding=[1.0],
    )
    result = await query.best_supporting_passage(session, [passage], 2, Settings(_env_file=None))
    assert (result is not None) is expected
