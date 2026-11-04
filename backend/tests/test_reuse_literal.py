"""Literal proof, retrieval coverage bounds and mixed quotation controls."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.checks.reuse.literal import (
    literal_candidates,
    literal_spans,
    literal_window,
    maximal_literal_matches,
    phrase_probes,
    word_spans,
)
from app.checks.reuse.scope import literal_unassessed_count
from app.config import Settings
from app.ingest.analytical_source import AnalyticalSource
from app.ingest.quotations import located_word_quotation, located_word_reference
from app.ingest.schemas import TextBlock
from app.ingest.service import load_raw_store, raw_store_path
from app.storage import ensure_local_file


def runs(left, right, *, quoted_left=None, quoted_right=None, budget=100000):
    return literal_spans(
        left,
        right,
        budget,
        minimum=4,
        span_limit=16,
        own_quotes=quoted_left or [False] * len(left.split()),
        matched_quotes=quoted_right or [False] * len(right.split()),
    )[0]


def test_contiguous_span_retains_original_offsets_case_and_punctuation():
    left = "preface Maple  trunks\ncarry copper labels. finish"
    right = "Maple trunks carry COPPER labels."
    found = runs(left, right)
    assert len(found) == 1 and found[0].word_count == 5
    assert left[found[0].own_start : found[0].own_end] == "Maple  trunks\ncarry copper labels."
    assert right[found[0].matched_start : found[0].matched_end] == right
    assert runs("Maple trunks carry copper labels!", right)[0].word_count == 4


def test_noncontiguous_collage_is_not_summed():
    assert not runs("amber birch x cedar delta y elm flint", "amber birch cedar delta elm flint")


@pytest.mark.parametrize("side", ["own", "matched"])
def test_quotes_split_runs_without_joining_unquoted_fragments(side):
    text = "amber birch cedar delta elm flint gale haze ivory juniper"
    mask = [False, False, True, True, True, True, False, False, False, False]
    kwargs = {"quoted_left" if side == "own" else "quoted_right": mask}
    found = runs(text, text, **kwargs)
    assert [(item.word_count, text[item.own_start : item.own_end]) for item in found] == [
        (4, "gale haze ivory juniper")
    ]


def test_work_exhaustion_is_explicit():
    with pytest.raises(OverflowError):
        runs("amber birch cedar delta", "amber birch cedar delta", budget=15)


def receipt(first, last, **overrides):
    text = " ".join(f"word{index}" for index in range(100))
    words = word_spans(text)
    start, end = words[first][1], words[last - 1][2]
    proof = dict(
        normalization="casefold_whitespace_tokens_v1",
        word_count=last - first,
        own_start=start,
        own_end=end,
        matched_start=start,
        matched_end=end,
    )
    return SimpleNamespace(
        **dict(
            dict(
                own_passage_index=first,
                own_chapter_index=0,
                matched_chapter_index=0,
                matched_manuscript_id=2,
                own_is_block_quote=False,
                matched_is_block_quote=False,
                own_is_reference_list=False,
                matched_is_reference_list=False,
                own_char_start=start,
                own_char_end=end,
                matched_char_start=start,
                matched_char_end=end,
                own_text=text[start:end],
                matched_text=text[start:end],
                literal_overlap=proof,
                level="literal_overlap",
            ),
            **overrides,
        )
    )


def test_only_redundant_aligned_contained_literal_alerts_are_removed():
    bigger, nested, tail = receipt(10, 90), receipt(30, 90), receipt(60, 90)
    matches, unassessed = maximal_literal_matches([nested, bigger, tail], 100000)
    assert matches == [bigger] and not unassessed


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("matched_manuscript_id", 3),
        ("own_is_block_quote", True),
        ("matched_is_block_quote", True),
        ("own_is_reference_list", True),
        ("matched_is_reference_list", True),
        ("level", "high_similarity"),
        ("level", "exact_duplicate"),
    ],
)
def test_containment_retains_distinct_ownership_and_vector_evidence(attribute, value):
    bigger, other = receipt(10, 90), receipt(30, 90, **{attribute: value})
    assert maximal_literal_matches([bigger, other], 100000)[0] == [bigger, other]


def test_separate_copied_occurrences_and_different_alignments_remain():
    first, second = receipt(10, 90), receipt(10, 90)
    second.own_char_start += 10000
    second.own_char_end += 10000
    second.literal_overlap = dict(
        second.literal_overlap, own_start=second.own_char_start, own_end=second.own_char_end
    )
    assert maximal_literal_matches([first, second], 100000)[0] == [first, second]
    shifted = receipt(30, 90)
    shifted.literal_overlap = dict(
        shifted.literal_overlap, matched_start=shifted.matched_char_start + 1
    )
    assert maximal_literal_matches([first, shifted], 100000)[0] == [first, shifted]


def test_containment_work_limit_preserves_evidence_and_marks_unassessed():
    matches = [receipt(10, 90), receipt(30, 90)]
    retained, unassessed = maximal_literal_matches(matches, 1)
    assert retained == matches and unassessed == {10, 30}


def test_every_supported_40_word_alignment_contains_a_probe():
    settings = Settings(_env_file=None)
    text = " ".join(f"term{index}" for index in range(180))
    probes, limited = phrase_probes(text, settings)
    assert not limited
    for start in range(141):
        window = " ".join(text.split()[start : start + 40])
        assert any(probe in window for probe in probes)


def test_invalid_probe_bound_is_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, reuse_literal_probe_stride=34)


def test_boundary_window_preserves_original_offsets_and_declines_ambiguous_context():
    text = "cedar delta elm flint"
    context = "amber birch " + text + " gale haze"
    assert literal_window(text, context, 112) == (context, 100, None)
    for unsupported in ("unrelated context", text + " " + text):
        assert literal_window(text, unsupported, 112) == (
            text,
            112,
            "boundary_context_not_verified",
        )
    assert literal_window(text, context, 2) == (text, 2, "boundary_context_not_verified")


def test_probe_grid_reaches_each_side_of_a_library_boundary():
    settings = Settings(_env_file=None)
    tokens = [f"term{index}" for index in range(270)]
    probes, limited = phrase_probes(" ".join(tokens), settings)
    assert not limited
    for start in range(231):
        for boundary in range(start + 1, start + settings.reuse_literal_min_words):
            sides = [" ".join(tokens[start:boundary]), " ".join(tokens[boundary : start + 40])]
            assert any(probe in side for probe in probes for side in sides)


@pytest.mark.parametrize(
    "style,text,expected",
    [
        ("normal", 'Opening "quoted words" closing prose', [False, True, True, False, False]),
        ("quote", "quoted words remain quoted", [True] * 4),
        (None, "historical text", None),
        ("normal", 'Opening "unclosed words', None),
        ("normal", "A student's own prose", [False] * 4),
        (
            "normal",
            "instructors' manuals describe ordinary prose beside students' records",
            [False] * 8,
        ),
    ],
)
def test_source_bound_quote_membership(style, text, expected):
    block = TextBlock(paragraph=2, text=text, max_font_size=12, bold_ratio=0, paragraph_style=style)
    source = AnalyticalSource("complete", None, ((block,),), (), ())
    found = located_word_quotation(source, text, Settings(_env_file=None))
    assert (None if found is None else [word[1] for word in found]) == expected


def test_balanced_multiblock_quote_protects_a_middle_passage():
    blocks = [
        TextBlock(
            paragraph=index, text=text, max_font_size=12, bold_ratio=0, paragraph_style="normal"
        )
        for index, text in enumerate(
            ['"quotation begins here', "the middle contains shared words", 'quotation ends here"']
        )
    ]
    source = AnalyticalSource("complete", None, (tuple(blocks),), (), ())
    found = located_word_quotation(source, blocks[1].text, Settings(_env_file=None))
    assert found == [(1, True, None)] * 5


def test_context_reference_membership_keeps_native_fragments_and_unknown_state():
    from dataclasses import replace

    blocks = [
        TextBlock(paragraph=i, text=text, paragraph_style="normal", max_font_size=12, bold_ratio=0)
        for i, text in enumerate(["body words", "References", "Author (2024). source entry words"])
    ]
    source = AnalyticalSource(
        "complete",
        None,
        (tuple(blocks),),
        (),
        (),
        reference_block_spans=((id(blocks[1]), 0, 10), (id(blocks[2]), 15, len(blocks[2].text))),
    )
    found = located_word_reference(
        source, " ".join(b.text for b in blocks), Settings(_env_file=None)
    )
    assert [word[1] for word in found] == [False, False, True, False, False, True, True, True]
    assert (
        located_word_reference(
            replace(source, reference_block_spans=None), blocks[0].text, Settings(_env_file=None)
        )
        is None
    )


def test_reference_heading_cannot_protect_identical_words_in_another_body_block():
    body = TextBlock(
        paragraph=0,
        text="References ordinary body words",
        paragraph_style="normal",
        max_font_size=12,
        bold_ratio=0,
    )
    heading = body.model_copy(update={"paragraph": 1, "text": "References"})
    source = AnalyticalSource(
        "complete", None, ((body, heading),), (), (), reference_block_spans=((id(heading), 0, 10),)
    )
    found = located_word_reference(source, body.text, Settings(_env_file=None))
    assert found == [(0, False, None)] * 4


def test_a_shared_heading_alone_cannot_support_a_passage_warning():
    from app.checks.reuse.source import selected_passage_supported

    block = TextBlock(
        paragraph=0,
        text="SECTION C PROCEDURE",
        paragraph_style="heading 1",
        max_font_size=14,
        bold_ratio=1,
    )
    body = block.model_copy(
        update={"paragraph": 1, "text": "Independent ordinary manuscript wording"}
    )
    source = AnalyticalSource(
        "complete", None, ((block, body),), (), (), heading_block_ids=frozenset({id(block)})
    )
    assert not selected_passage_supported(source, text=block.text, context=block.text, paragraph=0)
    assert selected_passage_supported(source, text=body.text, context=body.text, paragraph=1)


def test_pdf_span_retains_its_own_page_and_known_quotation_provenance():
    blocks = [
        TextBlock(page=page, text=text, max_font_size=12, bold_ratio=0)
        for page, text in [(1, "earlier context"), (2, "the actual shared span")]
    ]
    source = AnalyticalSource(
        "not_applicable", None, (tuple(blocks),), (), (), quotation_block_ids=frozenset()
    )
    found = located_word_quotation(source, blocks[1].text, Settings(_env_file=None))
    assert found == [(None, False, 2)] * 4


def test_oversize_raw_source_is_rejected_before_json_parsing(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, storage_backend="local")
    path = raw_store_path(settings, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"invalid json that must not be parsed")
    with pytest.raises(OverflowError):
        load_raw_store(settings, 1, max_bytes=4)


def test_bounded_stream_refill_never_publishes_a_partial_source(tmp_path):
    class StreamingStorage:
        def download_to(self, key, target, *, chunk_bytes):
            target.write(b"abc")
            target.write(b"def")

    settings = Settings(_env_file=None, data_dir=tmp_path, storage_backend="local")
    path = tmp_path / "raw.json"
    with pytest.raises(OverflowError):
        ensure_local_file(settings, StreamingStorage(), str(path), max_bytes=5)
    assert not path.exists() and list(tmp_path.iterdir()) == []


async def test_oversize_literal_input_is_unassessed_before_sql():
    class NoQuery:
        async def execute(self, *args, **kwargs):
            raise AssertionError("Oversize text must not reach SQL")

    rows, issues = await literal_candidates(
        NoQuery(),
        SimpleNamespace(text="x" * 11),
        exclude={1},
        include_reference_list=False,
        include_block_quote=False,
        settings=Settings(_env_file=None, reuse_literal_passage_max_chars=10),
    )
    assert rows == [] and issues == ["passage_text_limit"]


def test_limit_summary_counts_passages_instead_of_repeated_reasons():
    assert (
        literal_unassessed_count(
            {
                "method": "casefold_whitespace_tokens_v1",
                "unassessed": [
                    {"passage_index": 2, "reason": "probe_limit"},
                    {"passage_index": 2, "reason": "time_budget"},
                    {"passage_index": 8, "reason": "candidate_limit"},
                ],
            }
        )
        == 2
    )
    assert literal_unassessed_count(None) is None
