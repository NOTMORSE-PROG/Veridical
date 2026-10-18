"""F1/F5 punctuation controls; synthetic shapes, not accuracy labels."""

import json
from pathlib import Path

import pytest

from app.checks.citations.extract import cross_match, extract_in_text_citations
from app.config import Settings
from app.ingest import references
from app.ingest.references import parse_reference
from app.ingest.schemas import TextBlock
from app.models.enums import CitationParseStatus


def body(text):
    return [TextBlock(paragraph=4, text=text, max_font_size=11, bold_ratio=0)]


@pytest.mark.parametrize("suffix", ["et al.", "et al.,", "et al,", "et\nal.,"])
def test_narrative_et_al_terminal_comma_retains_source_and_matches(suffix):
    text = f"Reyes {suffix} (2021) reported a measured result."
    references = [parse_reference("Reyes, A. (2021). An example. Example Journal.", 0)]
    citations = extract_in_text_citations(body(text))
    assert len(citations) == 1
    assert citations[0].surnames == ["Reyes"]
    assert citations[0].claim_sentence == text
    matches = cross_match(citations, references)
    assert matches.linked == [(citations[0], references)]
    assert matches.orphans == matches.confidently_uncited == []


@pytest.mark.parametrize("author", ["Reyes M.D.", "Reyes A. B.", "Dela Cruz M.D."])
def test_missing_author_comma_creates_only_possible_link(author):
    family = author.rsplit(" ", 1)[0] if author.startswith("Dela") else author.split()[0]
    reference = parse_reference(f"{author} (2021). An example. Example Journal.", 0)
    citations = extract_in_text_citations(body(f"{family} (2021) reported a result."))
    matches = cross_match(citations, [reference])
    assert matches.linked == matches.orphans == matches.confidently_uncited == []
    assert matches.ambiguous == citations
    assert matches.possibly_cited == [reference]


def test_initials_alias_does_not_hide_different_year_or_organization_prefix():
    references = [
        parse_reference("Reyes M.D. (2022). An example. Example Journal.", 0),
        parse_reference("Research Advisory Council (2021). Guidance. Example Press.", 1),
    ]
    citations = extract_in_text_citations(
        body("Reyes (2021) and Research (2021) reported results.")
    )
    matches = cross_match(citations, references)
    assert len(matches.orphans) == 2
    assert matches.possibly_cited == []


@pytest.mark.parametrize("label", ["URL", "URL:", "url:"])
def test_separate_labelled_locator_does_not_look_like_doi_continuation(label):
    raw = (
        "[1] Alice Morgan and Bob Reyes. An example study. Example Journal, 2024. "
        f"doi:10.1234/example. {label} https://example.test/article."
    )
    reference = parse_reference(raw, 0)
    assert reference.parse_status == CitationParseStatus.parsed
    assert reference.authors == ["Alice Morgan", "Bob Reyes"]
    assert reference.title == "An example study"
    assert reference.year == 2024
    assert reference.venue == "Example Journal"
    assert reference.raw_text == raw


def test_locator_label_without_a_locator_does_not_establish_a_boundary():
    raw = "[1] Alice Morgan. An example. Example Journal, 2024. doi:10.1234/item URL tail."
    assert parse_reference(raw, 0).parse_status == CitationParseStatus.parse_failed


def test_natural_name_with_trailing_initials_stays_a_possible_surname_match():
    reference = parse_reference("Alice Reyes M.D. (2021). An example. Example Journal.", 0)
    citations = extract_in_text_citations(body("Reyes (2021) reported a result."))
    matches = cross_match(citations, [reference])
    assert matches.linked == matches.orphans == matches.confidently_uncited == []
    assert matches.ambiguous == citations
    assert matches.possibly_cited == [reference]


@pytest.mark.parametrize("labels", [None, []])
def test_preexisting_pattern_overrides_remain_usable_without_locator_labels(
    tmp_path, monkeypatch, labels
):
    original = Path(references.__file__).parent / "data" / "reference_metadata_patterns.json"
    patterns = json.loads(original.read_text(encoding="utf-8"))
    if labels is None:
        patterns.pop("locator_labels")
    else:
        patterns["locator_labels"] = labels
    override = tmp_path / "patterns.json"
    override.write_text(json.dumps(patterns), encoding="utf-8")
    settings = Settings(_env_file=None, reference_metadata_patterns_file=override)
    monkeypatch.setattr(references, "get_settings", lambda: settings)
    draft = parse_reference("[1] Alice Morgan. Example work. Example Journal, 2024.", 0)
    assert draft.parse_status == CitationParseStatus.parsed
    assert draft.authors == ["Alice Morgan"]
    assert draft.title == "Example work"
    assert draft.year == 2024
