"""Scoped supporting evidence keeps its true classification, including absence."""

from types import SimpleNamespace

import pytest

from app.checks.reuse.embed import PassageEmbedding
from app.checks.reuse.query import best_supporting_passage
from app.checks.reuse.service import _supporting_passage_detail
from app.config import get_settings
from app.flags.service import _passage_pair_from_detail


@pytest.mark.parametrize(
    "similarity,level",
    [
        (0.2, None),
        (0.749, None),
        (0.75, "high_similarity"),
        (0.949, "high_similarity"),
        (0.95, "exact_duplicate"),
    ],
)
async def test_actual_scoped_factory_preserves_threshold_classification(similarity, level):
    settings = get_settings().model_copy(
        update={
            "reuse_passage_high_similarity_threshold": 0.75,
            "reuse_passage_exact_duplicate_threshold": 0.95,
        }
    )
    archive = SimpleNamespace(
        manuscript_id=13,
        chapter_index=2,
        page=4,
        paragraph=None,
        char_start=0,
        char_end=22,
        text="Stored central excerpt",
        context_text="Before stored. Stored central excerpt After stored.",
        is_reference_list=False,
        is_block_quote=False,
    )

    class Row:
        instructor_id = 8
        group_label = "Private identity must not be projected"

        def __init__(self):
            self.similarity = similarity

        def __getitem__(self, index):
            assert index == 0
            return archive

    class Session:
        calls = 0

        async def execute(self, statement):
            self.calls += 1
            params = statement.compile().params
            assert 13 in params.values()
            assert 2 in params.values()
            return SimpleNamespace(first=lambda: Row())

    own = PassageEmbedding(
        passage_index=0,
        chapter_index=1,
        anchor_kind="page",
        page=3,
        paragraph=None,
        char_start=0,
        char_end=19,
        text="Own central excerpt",
        context_text="Before own. Own central excerpt After own.",
        is_reference_list=False,
        is_block_quote=False,
        embedding=[1.0] + [0.0] * (settings.embedding_dim - 1),
    )
    session = Session()
    match = await best_supporting_passage(
        session, [own], 13, settings, own_chapter_index=1, matched_chapter_index=2
    )
    assert session.calls == 1
    assert match is not None and match.level == level
    detail = _supporting_passage_detail(match)
    pair = _passage_pair_from_detail(
        "Parent comparison", {"kind": "reuse_exact_duplicate", "supporting_passage": detail}
    )
    assert pair is not None and pair.level == level
    assert pair.own_excerpt == own.text and pair.matched_excerpt == archive.text
    assert "Private identity" not in pair.model_dump_json()
