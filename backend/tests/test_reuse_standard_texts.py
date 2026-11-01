"""BUG-637: wording inside a listed standard text does not confirm an
originality match, and the match names the standard instead of quoting it.

Committed tests use freely shareable texts (a Likert legend, a school's
title-page statement) and a made-up standard; the bundled list holds only
hashes, so no standard's wording is needed here."""

import json

from app.checks.reuse.lexical import words
from app.checks.reuse.set_aside import pair_wording
from app.checks.reuse.standard_texts import (
    load_standard_texts,
    shingle_hash,
    standard_text_spans,
)
from app.config import Settings

MADE_UP_STANDARD = (
    "Degree to which a campus system lets enrolled students finish a registration task "
    "without asking the registrar staff for help in the specified context of use"
)
LIKERT = (
    "Strongly disagree Disagree Neither agree nor disagree Agree Strongly agree "
    "Never Rarely Sometimes Often Always"
)
TITLE_PAGE = (
    "A Capstone Project Presented to the Faculty of the Technological Institute of the "
    "Philippines In Partial Fulfillment of the Requirements for the Degree of Bachelor of "
    "Science in Information Technology"
)
COPIED = (
    "Students who registered online spent less time waiting at the registrar, and the staff "
    "said the queue was shorter during enrolment week than in the previous term, when every "
    "form was filed by hand at a single window."
)


def _list_file(tmp_path, texts: dict[str, str], min_run_words: int = 8):
    entries = []
    for name, text in texts.items():
        tokens = words(text)
        entries.append(
            {
                "name": name,
                "source": "test",
                "shingles": sorted(
                    {shingle_hash(tokens[i : i + 5]) for i in range(len(tokens) - 4)}
                ),
            }
        )
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "standard_texts.json"
    path.write_text(
        json.dumps({"shingle_words": 5, "min_run_words": min_run_words, "entries": entries}),
        encoding="utf-8",
    )
    return path


def _settings(tmp_path, texts=None) -> Settings:
    texts = texts or {
        "Made-up usability definition": MADE_UP_STANDARD,
        "Likert agreement scale legend": LIKERT,
        "School title page statement": TITLE_PAGE,
    }
    # The made-up list stands in for the public list, so the bundled entries
    # do not mix into these cases.
    return Settings(reuse_public_standard_texts_file=_list_file(tmp_path, texts))


def test_the_bundled_list_loads_and_names_every_entry():
    standards = load_standard_texts(Settings().reuse_public_standard_texts_file)
    assert standards.names and all(standards.names)
    assert standards.entries_by_hash
    assert standards.shingle_words == 5


def test_a_standard_definition_alone_does_not_confirm_and_is_named(tmp_path):
    settings = _settings(tmp_path)
    own = f"Usability in this study follows the definition: {MADE_UP_STANDARD}."
    other = f"We adopt the following definition. {MADE_UP_STANDARD}, as the standard states."
    evidence = pair_wording(own, own, other, other, settings)
    assert not evidence["confirmed"]
    assert evidence["set_aside"] == "standard_text"
    assert evidence["standard_texts"] == ["Made-up usability definition"]


def test_a_likert_legend_and_a_title_page_statement_do_not_confirm(tmp_path):
    settings = _settings(tmp_path)
    for text in (LIKERT, TITLE_PAGE):
        own = f"The respondents used this. {text}. Results follow in the next section."
        other = f"Table 3. {text}. Source: the researchers."
        assert not pair_wording(own, own, other, other, settings)["confirmed"], text


def test_copied_prose_around_a_standard_definition_still_confirms(tmp_path):
    settings = _settings(tmp_path)
    own = f"{MADE_UP_STANDARD}. {COPIED}"
    other = f"{MADE_UP_STANDARD}. {COPIED} The study then turned to cost."
    evidence = pair_wording(own, own, other, other, settings)
    assert evidence["confirmed"]
    assert evidence["standard_texts"] == ["Made-up usability definition"]


def test_a_short_common_phrase_is_not_a_standard_text(tmp_path):
    """Fewer than `min_run_words` covered words is a phrase the text happens
    to share, not the standard text itself."""
    standards = load_standard_texts(_settings(tmp_path).reuse_public_standard_texts_file)
    text = "The study was done in partial fulfillment of the course requirement."
    assert standard_text_spans(text, standards) == []


def test_a_pair_only_both_kinds_explain_says_both(tmp_path):
    """The pair shares a standard definition and a cited quotation: leaving
    out either alone still confirms it, so the reason names both."""
    settings = _settings(tmp_path)
    own = (
        f'{MADE_UP_STANDARD}. Reyes (2019) wrote that "{COPIED}" (p. 4). '
        "Our own analysis looked at the evening sections."
    )
    other = f"{MADE_UP_STANDARD}. {COPIED}"
    evidence = pair_wording(own, own, other, other, settings)
    assert not evidence["confirmed"]
    assert evidence["set_aside"] == "cited_quotation_and_standard_text"


def test_the_reason_is_the_standard_when_it_alone_explains_the_match(tmp_path):
    settings = _settings(tmp_path)
    own = f'{MADE_UP_STANDARD}. Reyes (2019) wrote that "the queue moved faster online" (p. 4).'
    other = f"{MADE_UP_STANDARD}. The queue moved faster online, the clinic said."
    assert pair_wording(own, own, other, other, settings)["set_aside"] == "standard_text"


def test_only_standards_both_passages_carry_are_named(tmp_path):
    settings = _settings(tmp_path)
    own = f"{MADE_UP_STANDARD}. {TITLE_PAGE}."
    other = f"{TITLE_PAGE}. The study then turned to cost."
    assert pair_wording(own, own, other, other, settings)["standard_texts"] == [
        "School title page statement"
    ]


def test_a_second_standard_is_named_only_for_enough_words_of_its_own(tmp_path):
    """Two made-up standards printed together are both named; a standard that
    only shares a short phrase with the run is not."""
    second = (
        "Degree to which the library records of enrolled students stay complete after "
        "every semester of the programme"
    )
    phrase_only = "students finish a registration task without help from anyone else today"
    settings = _settings(
        tmp_path,
        {"First": MADE_UP_STANDARD, "Second": second, "Phrase only": phrase_only},
    )
    standards = load_standard_texts(settings.reuse_public_standard_texts_file)
    spans = standard_text_spans(f"Both: {MADE_UP_STANDARD} {second}.", standards)
    assert [s.names for s in spans] == [("First", "Second")]
    # Five more covered words, from a phrase of "Phrase only", are too few to name it.
    tail = standard_text_spans(f"{MADE_UP_STANDARD} without help from anyone else.", standards)
    assert [s.names for s in tail] == [("First",)]


def test_a_standard_text_span_ends_at_its_last_word(tmp_path):
    """Words right after a standard text are not left out with it."""
    standards = load_standard_texts(_settings(tmp_path).reuse_public_standard_texts_file)
    text = f"Intro. {MADE_UP_STANDARD} copied words follow here."
    (span,) = standard_text_spans(text, standards)
    assert text[span.start : span.end] == MADE_UP_STANDARD


def test_a_phrase_two_standards_share_names_only_the_one_quoted(tmp_path):
    other_standard = (
        "Degree to which a campus system keeps the records of enrolled students safe "
        "from changes that nobody authorised"
    )
    settings = _settings(
        tmp_path,
        {"Made-up usability definition": MADE_UP_STANDARD, "Made-up integrity": other_standard},
    )
    standards = load_standard_texts(settings.reuse_public_standard_texts_file)
    spans = standard_text_spans(f"We follow it: {MADE_UP_STANDARD}.", standards)
    assert [s.names for s in spans] == [("Made-up usability definition",)]


def test_a_deployment_list_adds_to_the_public_list(tmp_path):
    public = _list_file(tmp_path / "a", {"Likert agreement scale legend": LIKERT})
    deployment = _list_file(tmp_path / "b", {"Made-up usability definition": MADE_UP_STANDARD})
    lists = load_standard_texts(public, deployment)
    assert (lists.public_entries, lists.deployment_entries) == (1, 1)
    assert lists.deployment_status == "loaded"
    text = f"{LIKERT}. Then: {MADE_UP_STANDARD}."
    names = {name for span in standard_text_spans(text, lists) for name in span.names}
    assert names == {"Likert agreement scale legend", "Made-up usability definition"}


def test_an_unreadable_deployment_list_is_skipped_not_fatal(tmp_path):
    public = _list_file(tmp_path / "a", {"Likert agreement scale legend": LIKERT})
    missing = tmp_path / "no-such-list.json"
    lists = load_standard_texts(public, missing)
    assert (lists.public_entries, lists.deployment_entries) == (1, 0)
    assert lists.deployment_status == "unreadable"


def test_a_deployment_list_built_another_way_is_not_mixed_in(tmp_path):
    public = _list_file(tmp_path / "a", {"Likert agreement scale legend": LIKERT})
    other = _list_file(tmp_path / "b", {"Made-up": MADE_UP_STANDARD}, min_run_words=12)
    lists = load_standard_texts(public, other)
    assert (lists.deployment_entries, lists.deployment_status) == (0, "incompatible")


def test_a_blank_setting_means_no_deployment_list():
    assert Settings(reuse_standard_texts_file="").reuse_standard_texts_file is None


def test_the_bundled_public_list_holds_no_iso_definitions():
    """ISO wording may not be published, and overlapping hashes can be
    chained back into it, so every ISO definition goes in the deployment
    list, including ones quoted in openly licensed papers: the paper's
    licence does not cover the sentence it quotes."""
    entries = json.loads(Settings().reuse_public_standard_texts_file.read_text("utf-8"))["entries"]
    assert not [e["name"] for e in entries if "ISO" in e["name"] or "ISO" in e["source"]]
    # Nor a journal's definitions quoted under fair use (Davis 1989).
    assert not [e["name"] for e in entries if "Technology Acceptance" in e["name"]]


def test_a_malformed_deployment_list_is_skipped_not_fatal(tmp_path):
    public = _list_file(tmp_path / "a", {"Likert agreement scale legend": LIKERT})
    header = {"shingle_words": 5, "min_run_words": 8}
    for i, broken in enumerate(
        (
            header,
            {**header, "entries": None},
            {**header, "entries": [{"name": "No shingles"}]},
            {**header, "entries": [{"shingles": ["0" * 16]}]},
            {**header, "entries": [{"name": "Text as shingles", "shingles": "0123456789abcdef"}]},
        )
    ):
        path = tmp_path / f"broken{i}.json"
        path.write_text(json.dumps(broken), encoding="utf-8")
        lists = load_standard_texts(public, path)
        assert (lists.public_entries, lists.deployment_entries) == (1, 0), broken
        assert lists.deployment_status == "malformed", broken
        assert standard_text_spans(f"{LIKERT}.", lists)
