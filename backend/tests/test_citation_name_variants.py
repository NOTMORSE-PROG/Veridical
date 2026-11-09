"""F5: a citation that spells its source differently from the entry is linked,
not reported missing from the reference list (BUG-696)."""

import json
import logging

import pytest

from app.checks.citations.cross_match import _NAME_VARIANTS_FILE, load_name_variant_policy
from app.checks.citations.extract import cross_match, extract_in_text_citations
from app.config import Settings, get_settings
from app.ingest.references import parse_reference
from app.ingest.schemas import TextBlock

XIONG = (
    "Xiong-Bin, W., Guan, H., Han, Y., & Ma, J. (2017). A tour route planning model for tourism "
    "experience utility maximization. Advances in Mechanical Engineering, 9(10), 1-12."
)
STATISTA = (
    "Statista. (2023). Number of international tourist arrivals worldwide 1950-2022. "
    "https://www.statista.com/statistics/209334/"
)
STATISTA_SENTENCE = (
    "According to Statista Research Department (2023), the number of international "
    "tourist arrivals worldwide bounced back in 2022."
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


@pytest.mark.parametrize(
    "text,entry",
    [
        # The surname written without its hyphen.
        (
            "According to Xiongbin (2017), travel time and travel cost shaped tour route planning.",
            XIONG,
        ),
        # An organisation's long form, with the entry's title in the citing sentence.
        (STATISTA_SENTENCE, STATISTA),
    ],
)
def test_a_citation_naming_its_source_in_another_spelling_is_linked(text, entry):
    matched = match(text, [entry])
    assert len(variant_links(matched)) == 1 and matched.orphans == []
    assert matched.uncited == []


def test_an_organisation_form_without_the_title_in_the_sentence_stays_an_orphan():
    matched = match("Statista Research Department (2023) reported a rise in bookings.", [STATISTA])
    assert variant_links(matched) == [] and len(matched.orphans) == 1


def test_the_names_themselves_are_not_title_evidence():
    # The title shares "global", "tourism" and "council" with the sentence only
    # because they are the organisation's name; "annual report" is not enough.
    entry = "Global Tourism Council Asia. (2023). Global tourism council annual report. Manila."
    text = "According to Global Tourism Council (2023), the annual report was released."
    matched = match(text, [entry])
    assert variant_links(matched) == [] and len(matched.orphans) == 1


@pytest.mark.parametrize(
    "entry",
    [
        "Garcia Lopez, M. (2020). Tour route planning for tourism experience. Journal, 1, 1-9.",
        "Garcia Lopez M. (2020). Tour route planning for tourism experience. Journal, 1, 1-9.",
    ],
)
def test_a_person_is_never_matched_as_an_organisation_form(entry):
    # "Garcia" is the leading word of "Garcia Lopez", but neither name is an
    # organisation, comma or not, title or not.
    matched = match("Garcia (2020) studied tour route planning for tourism experience.", [entry])
    assert variant_links(matched) == [] and len(matched.orphans) == 1


def test_a_short_form_the_acronym_table_gives_to_another_body_is_not_linked():
    entry = (
        "NASA Spaceflight Association. (2020). Launch cadence statistics worldwide. "
        "https://example.org/launch"
    )
    text = "According to NASA (2020), launch cadence statistics rose sharply worldwide."
    matched = match(text, [entry])
    assert variant_links(matched) == [] and len(matched.orphans) == 1


def test_a_short_form_under_four_letters_is_not_linked():
    entry = "ABC Research Department. (2021). Rural broadband adoption survey results. Manila."
    text = "According to ABC (2021), the rural broadband adoption survey results improved."
    matched = match(text, [entry])
    assert variant_links(matched) == [] and len(matched.orphans) == 1


def test_a_different_year_or_a_different_name_stays_an_orphan():
    assert len(match("Xiongbin (2018) studied tour route planning.", [XIONG]).orphans) == 1
    assert len(match("Xiongbian (2017) studied tour route planning.", [XIONG]).orphans) == 1


def test_two_fitting_entries_link_neither():
    second = XIONG.replace("A tour route planning model", "A second tour route model")
    matched = match("According to Xiongbin (2017), tour routes matter.", [XIONG, second])
    assert variant_links(matched) == [] and len(matched.orphans) == 1


def test_two_organisations_fitting_by_name_link_neither_even_when_one_title_is_cited():
    first = "Statista Research Department. (2023). International tourist arrivals worldwide."
    second = "Statista Consumer Department. (2023). Online travel booking behaviour."
    text = "According to Statista (2023), international tourist arrivals worldwide rose."
    matched = match(text, [first, second])
    assert variant_links(matched) == [] and len(matched.orphans) == 1


def test_the_same_surname_respelt_links_even_to_an_entry_already_cited():
    text = "Xiong-Bin et al. (2017) built a model. Xiongbin (2017) reported route planning."
    matched = match(text, [XIONG])
    assert len(variant_links(matched)) == 2 and matched.orphans == []


def test_an_organisation_form_never_takes_an_entry_already_cited():
    text = "Statista (2023) counted arrivals. " + STATISTA_SENTENCE
    matched = match(text, [STATISTA])
    assert len(variant_links(matched)) == 1 and len(matched.orphans) == 1


def test_the_same_citation_twice_is_linked_both_times():
    matched = match(STATISTA_SENTENCE + " " + STATISTA_SENTENCE, [STATISTA])
    assert len(variant_links(matched)) == 2 and matched.orphans == []


def test_nothing_is_linked_by_spelling_when_nothing_else_links():
    matched = match("According to Xiongbin (2017), tour route planning.", [XIONG], anchor=False)
    assert matched.linked == [] and len(matched.orphans) == 1


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
        matched = match("According to Xiongbin (2017), tour route planning.", [XIONG])
        assert variant_links(matched) == [] and len(matched.orphans) == 1
    finally:
        _reset(monkeypatch)
    assert Settings(_env_file=None).citation_name_variant_policy_file is None


def test_an_incomplete_policy_skips_variant_linking_and_says_so(tmp_path, monkeypatch, caplog):
    packaged = json.loads(_NAME_VARIANTS_FILE.read_text(encoding="utf-8"))
    del packaged["organisation_prefix_min_chars"]
    _with_policy(tmp_path, monkeypatch, packaged)
    try:
        with caplog.at_level(logging.WARNING):
            matched = match("According to Xiongbin (2017), tour route planning.", [XIONG])
        assert variant_links(matched) == [] and len(matched.orphans) == 1
        assert "organisation_prefix_min_chars" in caplog.text
    finally:
        _reset(monkeypatch)


def test_a_few_title_words_in_a_long_title_are_not_enough():
    entry = (
        "Statista. (2023). Number of international tourist arrivals worldwide by region, "
        "purpose and transport mode. https://www.statista.com/statistics/209334/"
    )
    text = "According to Statista Research Department (2023), international tourist arrivals rose."
    matched = match(text, [entry])
    assert variant_links(matched) == [] and len(matched.orphans) == 1


def test_a_citation_the_main_matching_left_with_its_own_name_is_not_respelt():
    from app.checks.citations.cross_match import _link_name_variants

    cites = extract_in_text_citations([block("Black (2019) described mobile travel planning.")])
    references = [parse_reference(ANCHOR_ENTRY, 0)]
    linked = [("already linked", [])]
    remaining = _link_name_variants(cites, references, linked, set(), set())
    assert remaining == cites and len(linked) == 1
