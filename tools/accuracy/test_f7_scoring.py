"""F7 scoring: a plant is found only through a passage finding that quotes it
and shows the copied text from the right Library record; controls count any
finding on them, including a coarse finding naming their source record."""

import random

from tools.accuracy.lanes.f7 import (
    Plant,
    banned_hits,
    containment,
    flag_key,
    light_paraphrase,
    longest_common_run,
    overlap,
    page_of,
    score_copy,
    touches,
)

MATCH = {"page_window": 1, "shingle": 5, "containment_min": 0.5, "max_anchor_lag": 15}
TEXT = (
    "The proposed system records attendance through a mobile application. "
    "Each instructor scans the class code at the start of the session. "
    "The records are stored on a central server and can be exported as a report."
)
OTHER = "An unrelated paragraph that says something else about the survey and its respondents."


def _flag(page, excerpt, matched_id, matched_text=TEXT, kind="reuse_high_similarity_passage"):
    return {
        "severity": "med",
        "evidence_excerpt": excerpt,
        "page_anchor": f"p. {page}" if page is not None else "whole document",
        "detail": {"kind": kind, "matched_manuscript_id": matched_id, "matched_text": matched_text},
    }


def _plant(kind="verbatim", page=20, donor="mit"):
    return Plant(type=kind, page=page, text=TEXT, donor=donor, donor_pages=[40, 41])


def test_shingle_measures_ignore_case_and_punctuation():
    assert containment(TEXT, TEXT.upper().replace(".", ""), 5) == 1.0
    assert containment(TEXT, OTHER, 5) == 0.0
    first_words = " ".join(TEXT.split()[:15])
    assert overlap(first_words, TEXT, 5) == 1.0, "a part lying inside the whole overlaps fully"
    assert longest_common_run("a b c d e", "x b c d y") == 3


def test_a_plant_is_detected_only_with_the_right_record_and_copied_text():
    ids = {"mit": 5}
    right = _flag(20, "Some host words before it. " + TEXT, 5)
    wrong_record = _flag(20, TEXT, 9)
    wrong_passage = _flag(20, TEXT, 5, matched_text=OTHER)
    records, explained = score_copy([_plant()], [right], ids, MATCH)
    assert records[0]["detected"] and explained == {0}
    records, explained = score_copy([_plant()], [wrong_record], ids, MATCH)
    assert not records[0]["detected"] and records[0]["any_flag"] and explained == set()
    records, explained = score_copy([_plant()], [wrong_passage], ids, MATCH)
    assert records[0]["right_source"] and not records[0]["detected"]
    assert explained == {0}, "a finding naming the donor is explained even off the copied text"


def test_the_anchor_page_is_reported_not_required():
    records, _ = score_copy([_plant(page=95)], [_flag(87, TEXT, 5)], {"mit": 5}, MATCH)
    assert records[0]["detected"] and records[0]["anchor_offsets"] == [8]


def test_a_finding_far_from_the_plant_does_not_touch_it_even_when_the_text_matches():
    # Two plants cut from overlapping donor text would otherwise claim each
    # other's findings across the document.
    assert not touches(_plant(page=106), _flag(67, TEXT, 5), MATCH)
    assert not touches(_plant(page=20), _flag(23, TEXT, 5), MATCH), "after the plant"


def test_a_finding_not_quoting_the_plant_does_not_touch_it():
    assert not touches(_plant(), _flag(20, OTHER, 5), MATCH)
    assert not touches(_plant(), _flag(20, TEXT, 5, kind="reuse_high_similarity"), MATCH)


def test_a_control_counts_a_passage_finding_and_explains_nothing():
    control = _plant(kind="quoted_inline")
    records, explained = score_copy([control], [_flag(20, TEXT, 5)], {"mit": 5}, MATCH)
    assert records[0]["any_flag"] and explained == set()


def _chapter(matched_id, supporting_text=None):
    flag = _flag(None, "templated sentence", matched_id, kind="reuse_high_similarity_chapter")
    if supporting_text is not None:
        flag["detail"]["supporting_passage"] = {"own_text": supporting_text}
    return flag


def test_a_chapter_finding_is_held_against_the_plant_its_supporting_passage_shows():
    boilerplate = Plant(type="boilerplate", page=29, text=TEXT, donor=None)
    records, explained = score_copy([boilerplate], [_chapter(10, TEXT)], {"mit": 5}, MATCH)
    assert records[0]["any_flag"] and records[0]["coarse_alarm"] and explained == set()


def test_a_chapter_finding_without_a_supporting_passage_is_charged_to_no_plant():
    # A plant can push a section that already sat near the threshold over it,
    # whatever the plant says; the named record does not show which plant did.
    boilerplate = Plant(type="boilerplate", page=29, text=TEXT, donor=None)
    records, explained = score_copy([boilerplate], [_chapter(10)], {"mit": 5}, MATCH)
    assert not records[0]["any_flag"] and explained == set()


def test_a_chapter_finding_backed_by_a_recall_plant_is_explained():
    recall, control = _plant(), Plant(type="block_quote", page=40, text=OTHER, donor="mit")
    records, explained = score_copy([recall, control], [_chapter(5, TEXT)], {"mit": 5}, MATCH)
    assert explained == {0} and not records[1]["any_flag"]


def test_one_shared_shingle_does_not_make_a_finding_about_the_plant():
    lone = "records attendance through a mobile application " + OTHER
    assert not touches(_plant(), _flag(20, lone, 5), MATCH)


def test_twin_keys_survive_a_shifted_passage_text():
    a = _flag(30, "first wording", 5)
    b = _flag(30, "first wording plus a planted sentence", 5)
    assert flag_key(a) == flag_key(b)
    assert flag_key(a) != flag_key(_flag(31, "first wording", 5))
    assert page_of("p. 12") == 12 and page_of("whole document") is None


def test_light_paraphrase_is_reproducible_and_reports_its_share():
    table = {"system": "platform", "records": "logs", "mobile": "handheld", "stored": "kept"}
    first = light_paraphrase(TEXT, table, 0.15, random.Random(3))
    assert first == light_paraphrase(TEXT, table, 0.15, random.Random(3))
    text, share = first
    assert text != TEXT and 0 < share <= 0.15
    assert sorted(text.split()) != sorted(TEXT.split())


def test_banned_terms_are_found_in_any_case():
    terms = ["plagiar", "fake"]
    assert banned_hits("Possible PLAGIARISM here", terms) == ["plagiar"]
    assert banned_hits("Comparison lead against Library record #12.", terms) == []


def test_a_plant_whose_page_is_not_read_is_kept_and_marked(monkeypatch):
    from types import SimpleNamespace

    from tools.accuracy.lanes import f7

    extraction = SimpleNamespace(blocks=[SimpleNamespace(page=3, is_furniture=False, bbox=None)])
    monkeypatch.setattr(f7, "_body_margin", lambda *_: 72.0)
    case = {"plants": [{"type": "verbatim", "page": 9, "text": TEXT, "donor": "mit"}]}
    [plant] = f7.place_plants(extraction, case, {"block_quote_indent_points": 36})
    assert plant.extra["not_placed"] and len(extraction.blocks) == 1


def test_an_excerpt_is_a_contiguous_run_of_the_donor_cut_at_sentence_ends():
    from types import SimpleNamespace

    from tools.accuracy.lanes.f7 import make_excerpt, tokens

    lines = [
        "Short line one of the intro.",
        "The system was built over two terms by a team of three students working",
        "with the registrar.",
        "It was tested with forty users and their",
        "feedback shaped the second version.",
        "A third sentence closes the paragraph here.",
    ]
    blocks = [SimpleNamespace(page=5, is_furniture=False, text=t) for t in lines]
    extraction = SimpleNamespace(blocks=blocks)
    config = {"excerpt": {"slack_words": 20, "attempts": 50}}
    excerpt = make_excerpt("doc", extraction, {5}, 10, 30, random.Random(1), config)
    whole = " ".join(tokens(" ".join(lines)))
    assert " ".join(tokens(excerpt.text)) in whole, "a run of the donor, short lines included"
    assert excerpt.text[0].isupper() and excerpt.text[-1] == "."


def test_dev_and_holdout_plants_come_from_different_page_buckets():
    from tools.accuracy.lanes.f7 import page_split

    config = {"seed": 7, "split": {"holdout_share": 0.5, "bucket_pages": 10}}
    sides = {page_split("mit", page, config) for page in range(40, 50)}
    assert len(sides) == 1, "one bucket, one side"
    assert {page_split("mit", page, config) for page in range(0, 400, 10)} == {"dev", "holdout"}


def test_sealed_holdout_records_never_reach_the_rows():
    from tools.accuracy.lanes.f7 import metric_rows

    record = {
        "type": "verbatim",
        "host": "mit",
        "cluster": "c",
        "detected": True,
        "any_flag": True,
        "right_source": True,
        "anchor_offsets": [0],
        "comparable": True,
    }
    measured = {
        "records": [{**record, "split": "dev"}, {**record, "split": "holdout", "host": "ksa"}],
        "explained_total": 0,
        "fresh_total": 0,
        "unexplained": {},
        "audited": 0,
        "banned": [],
        "coverage": {"mit": {"body_pages": 1, "not_compared": 0, "refused": False}},
    }
    config = {"holdout_sealed": True, "match": {"page_window": 1}, "banned_terms": []}
    rows = metric_rows(measured, config)
    assert {row.split for row in rows} <= {"dev", "all"}
    assert all(row.numerator <= 1 for row in rows if row.metric.startswith("recall verbatim"))
