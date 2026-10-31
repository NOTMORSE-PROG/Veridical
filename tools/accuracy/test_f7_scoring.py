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

MATCH = {"page_window": 1, "shingle": 5, "containment_min": 0.5}
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


def test_a_finding_not_quoting_the_plant_does_not_touch_it():
    assert not touches(_plant(), _flag(20, OTHER, 5), MATCH)
    assert not touches(_plant(), _flag(20, TEXT, 5, kind="reuse_high_similarity"), MATCH)


def test_a_control_counts_a_passage_finding_and_explains_nothing():
    control = _plant(kind="quoted_inline")
    records, explained = score_copy([control], [_flag(20, TEXT, 5)], {"mit": 5}, MATCH)
    assert records[0]["any_flag"] and explained == set()


def test_a_chapter_finding_naming_the_carrier_is_a_boilerplate_alarm():
    boilerplate = Plant(type="boilerplate", page=29, text=TEXT, donor=None)
    chapter = _flag(None, "templated sentence", 10, kind="reuse_high_similarity_chapter")
    ids = {"carrier": 10, "mit": 5}
    records, explained = score_copy([boilerplate], [chapter], ids, MATCH, carrier="carrier")
    assert records[0]["any_flag"] and records[0]["coarse_alarm"] and explained == set()


def test_a_chapter_finding_naming_a_recall_donor_is_explained_not_an_alarm():
    recall, control = _plant(), _plant(kind="block_quote", page=40)
    chapter = _flag(None, "templated sentence", 5, kind="reuse_high_similarity_chapter")
    records, explained = score_copy([recall, control], [chapter], {"mit": 5}, MATCH)
    assert explained == {0} and not records[1]["any_flag"]


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
