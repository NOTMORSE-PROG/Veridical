"""Quotation uncertainty stays within retained bibliography boundaries."""

from dataclasses import replace

import pytest

from app.config import Settings
from app.ingest.analytical_source import AnalyticalSource
from app.ingest.quotations import located_word_quotation, located_word_reference
from app.ingest.schemas import TextBlock


def _source(body, tail):
    blocks = (
        TextBlock(page=1, text=body, max_font_size=12, bold_ratio=0),
        TextBlock(
            page=2,
            text="REFERENCES\nAuthor, A. (2024). A complete source.",
            max_font_size=12,
            bold_ratio=0,
        ),
        TextBlock(page=3, text=tail, max_font_size=12, bold_ratio=0),
    )
    source = AnalyticalSource(
        "not_applicable",
        None,
        (blocks,),
        (),
        (),
        quotation_block_ids=frozenset(),
        reference_block_spans=tuple((id(b), 0, len(b.text)) for b in blocks[1:]),
    )
    return source, blocks


@pytest.mark.parametrize("cached", [False, True])
def test_protected_appendix_quote_does_not_withhold_clear_body(cached):
    source, blocks = _source(
        "Their neighbours’ influences shape the network.",
        'APPENDIX A: A questionnaire contains an "unclosed quotation.',
    )
    settings = Settings(_env_file=None)
    cache = {} if cached else None
    for _ in range(2):
        body = located_word_quotation(source, blocks[0].text, settings, _scope_cache=cache)
        assert body and all(not word[1] for word in body)
        assert {word[2] for word in body} == {1}
        assert located_word_quotation(source, blocks[2].text, settings, _scope_cache=cache) is None
        tail = located_word_reference(source, blocks[2].text, settings)
        assert tail and all(word[1] for word in tail)
    if cached:
        assert len(cache) == 1


def test_protected_closing_quote_cannot_complete_an_open_body_quote():
    source, blocks = _source('The body has an "unclosed quotation.', 'APPENDIX: a closing quote".')
    assert located_word_quotation(source, blocks[0].text, Settings(_env_file=None)) is None


def test_unknown_reference_boundaries_cannot_quarantine_unclosed_text():
    source, blocks = _source("Clear body words.", 'APPENDIX: an "unclosed quotation.')
    source = replace(source, reference_block_spans=None)
    assert located_word_quotation(source, blocks[0].text, Settings(_env_file=None)) is None


def test_balanced_body_quote_keeps_its_word_membership_beside_a_protected_tail():
    source, blocks = _source('Unquoted "quoted pair" afterwards.', 'APPENDIX: an "unclosed quote.')
    words = located_word_quotation(source, blocks[0].text, Settings(_env_file=None))
    assert words and [word[1] for word in words] == [False, True, True, False]


def _chapters(texts):
    blocks = tuple(
        block
        for page, text in enumerate(texts, start=1)
        for block in (
            TextBlock(page=page, text=f"CHAPTER {page}", max_font_size=16, bold_ratio=1),
            TextBlock(page=page, text=text, max_font_size=12, bold_ratio=0),
        )
    )
    return AnalyticalSource(
        "not_applicable",
        None,
        (blocks,),
        (),
        (),
        quotation_block_ids=frozenset(),
        reference_block_spans=(),
        quotation_scope_starts=frozenset(id(block) for block in blocks[::2]),
    )


@pytest.mark.parametrize("extra_error", [False, True])
def test_balanced_cross_chapter_quote_stays_quoted_with_an_unrelated_delimiter_error(extra_error):
    texts = [
        "Opening “a long quotation",
        "The middle chapter is still quoted",
        "the quotation ends”.",
    ]
    if extra_error:
        texts.append('A different delimiter is "unclosed.')
    source = _chapters(texts)
    words = located_word_quotation(source, texts[1], Settings(_env_file=None))
    assert words and all(word[1] for word in words)


def test_same_delimiter_cross_chapter_quote_and_other_error_keep_middle_unknown():
    texts = [
        'Opening "a long quotation',
        "The middle chapter remains protected",
        'the quotation ends".',
        'An "unclosed quote',
    ]
    source = _chapters(texts)
    assert located_word_quotation(source, texts[1], Settings(_env_file=None)) is None


def test_missing_closing_mark_cannot_shift_later_genuine_quotes_to_unquoted():
    texts = [
        'Prose. "missing close. Later "genuinely quoted words" end.',
        "An independent method is readable.",
    ]
    source = _chapters(texts)
    assert (
        located_word_quotation(source, "genuinely quoted words", Settings(_env_file=None)) is None
    )
    assert located_word_quotation(source, texts[1], Settings(_env_file=None)) is None


def test_closing_then_opening_at_chapter_boundaries_cannot_hide_an_unfinished_quote():
    texts = [
        'Opening "first quotation',
        'first ends" and "second quotation',
        'second ends" and "unfinished quotation',
    ]
    source = _chapters(texts)
    assert located_word_quotation(source, "second quotation", Settings(_env_file=None)) is None
    assert located_word_quotation(source, "unfinished quotation", Settings(_env_file=None)) is None


def test_punctuation_cannot_turn_a_closing_mark_into_a_proven_opening():
    texts = [
        'Opening "first quotation ends with punctuation,',
        '", ordinary:" quoteB genuinely remains open',
    ]
    source = _chapters(texts)
    assert (
        located_word_quotation(source, "quoteB genuinely remains open", Settings(_env_file=None))
        is None
    )
