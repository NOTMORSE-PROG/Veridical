"""BUG-687: the originality result counts the pages whose text it actually
compared, so no surface has to estimate it from the other fields."""

from app.checks.reuse.embed import PassageEmbedding, _build_passages, compared_pages
from app.ingest.schemas import TextBlock

WORDS = " ".join(f"word{i}" for i in range(40))


def _block(page: int, text: str = WORDS) -> TextBlock:
    return TextBlock(page=page, text=text, bbox=(50, 100, 500, 120), max_font_size=12, bold_ratio=0)


def _passage(pages, *, reference=False, quote=False) -> PassageEmbedding:
    return PassageEmbedding(
        passage_index=0,
        chapter_index=0,
        anchor_kind="page",
        page=pages[0] if pages else None,
        paragraph=None,
        char_start=0,
        char_end=1,
        text="x",
        context_text="x",
        is_reference_list=reference,
        is_block_quote=quote,
        embedding=[0.0],
        pages=tuple(pages),
    )


def test_a_passage_over_a_page_break_records_both_pages():
    drafts = _build_passages([_block(3), _block(4)], chunk_words=75, ref_ids=set(), quote_ids=set())
    assert [d["pages"] for d in drafts] == [(3, 4)]


def test_a_block_longer_than_two_passages_keeps_its_page():
    long_block = _block(7, " ".join(f"w{i}" for i in range(400)))
    drafts = _build_passages([long_block], chunk_words=75, ref_ids=set(), quote_ids=set())
    assert drafts and all(d["pages"] == (7,) for d in drafts)


def test_only_pages_of_passages_that_are_matched_count():
    passages = [
        _passage([1, 2]),
        _passage([2, 3], quote=True),
        _passage([9], reference=True),
        _passage([4]),
    ]
    assert compared_pages(passages) == [1, 2, 4]


def test_no_passages_means_nothing_compared():
    assert compared_pages([]) == []
