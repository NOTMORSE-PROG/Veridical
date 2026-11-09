"""F5: a citation that spells its source differently from the entry is linked,
not reported missing from the reference list (BUG-696)."""

import json

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


def block(text):
    return TextBlock(page=1, text=text, max_font_size=11, bold_ratio=0)


def match(text, entries):
    references = [parse_reference(entry, i) for i, entry in enumerate(entries)]
    return cross_match(extract_in_text_citations([block(text)]), references)


@pytest.mark.parametrize(
    "text,entry",
    [
        # The surname written without its hyphen.
        (
            "According to Xiongbin (2017), travel time and travel cost shaped tour route planning.",
            XIONG,
        ),
        # An organisation's long form, with the entry's title in the citing sentence.
        (
            "According to Statista Research Department (2023), the number of international "
            "tourist arrivals worldwide bounced back in 2022.",
            STATISTA,
        ),
    ],
)
def test_a_citation_naming_its_source_in_another_spelling_is_linked(text, entry):
    matched = match(text, [entry])
    assert len(matched.linked) == 1 and matched.orphans == []
    assert matched.uncited == []


def test_an_organisation_form_without_the_title_in_the_sentence_stays_an_orphan():
    matched = match("Statista Research Department (2023) reported a rise in bookings.", [STATISTA])
    assert matched.linked == [] and len(matched.orphans) == 1


def test_a_personal_surname_never_matches_as_an_organisation_form():
    # "Garcia" is the leading word of "Garcia Lopez", but a person written
    # "Surname, Initials" is never matched by name form, title or not.
    entry = "Garcia Lopez, M. (2020). Tour route planning for tourism experience. Journal, 1, 1-9."
    matched = match("Garcia (2020) studied tour route planning for tourism experience.", [entry])
    assert matched.linked == [] and len(matched.orphans) == 1


def test_a_different_year_or_a_different_name_stays_an_orphan():
    assert len(match("Xiongbin (2018) studied tour route planning.", [XIONG]).orphans) == 1
    assert len(match("Xiongbian (2017) studied tour route planning.", [XIONG]).orphans) == 1


def test_two_fitting_entries_link_neither():
    second = XIONG.replace("A tour route planning model", "A second tour route model")
    matched = match("According to Xiongbin (2017), tour routes matter.", [XIONG, second])
    assert matched.linked == [] and len(matched.orphans) == 1


def test_the_same_surname_respelt_links_even_to_an_entry_already_cited():
    text = "Xiong-Bin et al. (2017) built a model. Xiongbin (2017) reported route planning."
    matched = match(text, [XIONG])
    assert len(matched.linked) == 2 and matched.orphans == []


def test_an_organisation_form_never_takes_an_entry_already_cited():
    text = (
        "Statista (2023) counted arrivals. According to Statista Research Department (2023), "
        "the number of international tourist arrivals worldwide bounced back in 2022."
    )
    matched = match(text, [STATISTA])
    assert len(matched.linked) == 1 and len(matched.orphans) == 1


def test_the_rule_is_data(tmp_path, monkeypatch):
    packaged = json.loads(_NAME_VARIANTS_FILE.read_text(encoding="utf-8"))
    custom = tmp_path / "variants.json"
    custom.write_text(json.dumps({**packaged, "enabled": False}), encoding="utf-8")
    monkeypatch.setenv("CITATION_NAME_VARIANT_POLICY_FILE", str(custom))
    get_settings.cache_clear()
    load_name_variant_policy.cache_clear()
    try:
        matched = match("According to Xiongbin (2017), tour route planning.", [XIONG])
        assert matched.linked == [] and len(matched.orphans) == 1
    finally:
        monkeypatch.delenv("CITATION_NAME_VARIANT_POLICY_FILE")
        get_settings.cache_clear()
        load_name_variant_policy.cache_clear()
    assert Settings(_env_file=None).citation_name_variant_policy_file is None
