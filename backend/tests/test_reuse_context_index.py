"""F7 surrounding excerpts preserve exact source without repeated source scans."""

import re

import pytest

import app.checks.reuse.embed as embed
from app.ingest.analytical_source import AnalyticalSource
from app.ingest.schemas import TextBlock


def _original_context(text, start, end, context_words):
    """Independent prefix/suffix oracle for the existing excerpt contract."""
    before = [match.span() for match in re.finditer(r"\S+", text[:start])]
    after = [match.span() for match in re.finditer(r"\S+", text[end:])]
    pre_start = (
        start
        if context_words <= 0
        else (0 if len(before) <= context_words else before[len(before) - context_words][0])
    )
    kept = after[:context_words]
    post_end = end + kept[-1][1] if kept else end
    return text[pre_start:post_end]


@pytest.mark.parametrize("text", ["", "one", "  one\tTWO\n three  ", "αβ 中文\u00a0last!"])
@pytest.mark.parametrize("context_words", [-2, -1, 0, 1, 2, 10])
def test_context_preserves_every_substring_boundary(text, context_words):
    for start in range(len(text) + 1):
        for end in range(start, len(text) + 1):
            assert embed._add_context(text, start, end, context_words) == _original_context(
                text, start, end, context_words
            )


@pytest.mark.parametrize("n_blocks", [20, 80])
def test_scoped_context_does_not_repeatedly_scan_the_whole_source(monkeypatch, n_blocks):
    blocks = [
        TextBlock(
            text=f"passage{i} has exact original words", paragraph=i, max_font_size=0, bold_ratio=0
        )
        for i in range(n_blocks)
    ]
    source = AnalyticalSource("not_applicable", None, (tuple(blocks),), (), ())
    full_text = "\n".join(block.text for block in blocks)
    original = embed._word_spans
    chars_scanned = 0

    def counted(text):
        nonlocal chars_scanned
        chars_scanned += len(text)
        return original(text)

    monkeypatch.setattr(embed, "_word_spans", counted)
    drafts = embed._scoped_passages(
        source, blocks, chunk_words=5, ref_ids=set(), quote_ids=set(), context_words=3
    )
    assert len(drafts) == n_blocks
    assert chars_scanned <= 2 * len(full_text), "Context work must scale with source size"
    for draft in drafts:
        assert draft["context_text"] == _original_context(
            full_text, draft["char_start"], draft["char_end"], 3
        )
