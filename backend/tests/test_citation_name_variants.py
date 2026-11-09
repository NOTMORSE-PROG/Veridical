"""F5: a citation whose surname is spelled differently from the entry's is
linked, not left without an entry (BUG-696)."""

import json
import logging

import pytest

from app.checks.citations.cross_match import (
    _NAME_VARIANTS_FILE,
    _link_name_variants,
    load_name_variant_policy,
)
from app.checks.citations.extract import cross_match, extract_in_text_citations
from app.config import Settings, get_settings
from app.ingest.references import parse_reference
from app.ingest.schemas import TextBlock

XIONG = (
    "Xiong-Bin, W., Guan, H., Han, Y., & Ma, J. (2017). A tour route planning model for tourism "
    "experience utility maximization. Advances in Mechanical Engineering, 9(10), 1-12."
)
XIONG_TEXT = "According to Xiongbin (2017), travel time and travel cost shaped tour route planning."
STATISTA = (
    "Statista. (2023). Number of international tourist arrivals worldwide 1950-2022. "
    "https://www.statista.com/statistics/209334/"
)
# Every document also carries one citation the main matching links, as a real
# thesis does: with nothing linked at all, variant linking stays off.
ANCHOR_TEXT = "Black (2019) described mobile travel planning. "
ANCHOR_ENTRY = "Black, A. (2019). Mobile travel planning. Tourism Review, 3, 1-5."


def block(text):
    return TextBlock(page=1, text=text, max_font_size=11, bold_ratio=0)


def match(text, entries, anchor=True):
    if anchor:
        text, entries = ANCHOR_TEXT + text, [ANCHOR_ENTRY, *entries]
    references = [parse_reference(entry, i) for i, entry in enumerate(entries)]
    return cross_match(extract_in_text_citations([block(text)]), references)


def variant_links(matched):
    return [cite for cite, _refs in matched.linked if cite.surnames[0] != "Black"]


def test_a_surname_written_without_its_hyphen_is_linked_and_said_so():
    matched = match(XIONG_TEXT, [XIONG])
    assert len(variant_links(matched)) == 1 and matched.orphans == []
    assert matched.uncited == []
    assert [(c.surnames[0], r.order_index) for c, r in matched.linked_by_spelling] == [
        ("Xiongbin", 1)
    ]


def test_an_exact_match_is_not_reported_as_a_link_by_spelling():
    matched = match("Xiong-Bin et al. (2017) built a model.", [XIONG])
    assert len(variant_links(matched)) == 1 and matched.linked_by_spelling == []


@pytest.mark.parametrize(
    "text",
    [
        # An organisation's long form is not linked to its short form: no name
        # rule tells that apart from two offices of one body.
        "According to Statista Research Department (2023), the number of international "
        "tourist arrivals worldwide bounced back in 2022.",
        "According to Statista (2023), the number of international tourist arrivals rose.",
    ],
)
def test_an_organisation_written_another_way_stays_an_orphan(text):
    entry = STATISTA if "Research" in text else STATISTA.replace("Statista.", "Statista Group.")
    matched = match(text, [entry])
    assert variant_links(matched) == [] and len(matched.orphans) == 1


def test_a_person_with_a_longer_name_is_not_linked():
    entry = "Garcia Lopez, M. (2020). Tour route planning for tourism experience. Journal, 1, 1-9."
    matched = match("Garcia (2020) studied tour route planning for tourism experience.", [entry])
    assert variant_links(matched) == [] and len(matched.orphans) == 1


def test_a_different_year_or_a_different_name_stays_an_orphan():
    assert len(match("Xiongbin (2018) studied tour route planning.", [XIONG]).orphans) == 1
    assert len(match("Xiongbian (2017) studied tour route planning.", [XIONG]).orphans) == 1


def test_two_fitting_entries_link_neither():
    second = XIONG.replace("A tour route planning model", "A second tour route model")
    matched = match("According to Xiongbin (2017), tour routes matter.", [XIONG, second])
    assert variant_links(matched) == [] and len(matched.orphans) == 1


def test_the_same_surname_respelt_links_even_to_an_entry_already_cited():
    text = "Xiong-Bin et al. (2017) built a model. Xiongbin (2017) reported route planning."
    matched = match(text, [XIONG])
    assert len(variant_links(matched)) == 2 and matched.orphans == []


def test_the_same_citation_twice_is_linked_both_times():
    matched = match(XIONG_TEXT + " " + XIONG_TEXT, [XIONG])
    assert len(variant_links(matched)) == 2 and matched.orphans == []


def test_nothing_is_linked_by_spelling_when_nothing_else_links():
    matched = match(XIONG_TEXT, [XIONG], anchor=False)
    assert matched.linked == [] and len(matched.orphans) == 1


def test_a_citation_the_main_matching_left_with_its_own_name_is_not_respelt():
    cites = extract_in_text_citations([block("Black (2019) described mobile travel planning.")])
    references = [parse_reference(ANCHOR_ENTRY, 0)]
    linked = [("already linked", [])]
    remaining, made = _link_name_variants(cites, references, linked, set())
    assert remaining == cites and made == [] and len(linked) == 1


def _with_policy(tmp_path, monkeypatch, policy):
    custom = tmp_path / "variants.json"
    custom.write_text(json.dumps(policy), encoding="utf-8")
    monkeypatch.setenv("CITATION_NAME_VARIANT_POLICY_FILE", str(custom))
    get_settings.cache_clear()
    load_name_variant_policy.cache_clear()


def _reset(monkeypatch):
    monkeypatch.delenv("CITATION_NAME_VARIANT_POLICY_FILE")
    get_settings.cache_clear()
    load_name_variant_policy.cache_clear()


def test_the_rule_is_data(tmp_path, monkeypatch):
    packaged = json.loads(_NAME_VARIANTS_FILE.read_text(encoding="utf-8"))
    _with_policy(tmp_path, monkeypatch, {**packaged, "enabled": False})
    try:
        matched = match(XIONG_TEXT, [XIONG])
        assert variant_links(matched) == [] and len(matched.orphans) == 1
    finally:
        _reset(monkeypatch)
    assert Settings(_env_file=None).citation_name_variant_policy_file is None


@pytest.mark.parametrize(
    "change", [{"fold_pattern": None}, {"fold_pattern": 5}, {"enabled": "false"}]
)
def test_an_unusable_policy_skips_variant_linking_and_says_so(
    tmp_path, monkeypatch, caplog, change
):
    packaged = json.loads(_NAME_VARIANTS_FILE.read_text(encoding="utf-8"))
    policy = {**packaged, **change}
    if change.get("fold_pattern", "") is None:
        del policy["fold_pattern"]
    _with_policy(tmp_path, monkeypatch, policy)
    try:
        with caplog.at_level(logging.WARNING):
            matched = match(XIONG_TEXT, [XIONG])
        assert variant_links(matched) == [] and len(matched.orphans) == 1
        assert "variant linking skipped" in caplog.text
    finally:
        _reset(monkeypatch)
