"""Bare objective recognition keeps section, source and quotation bounds (F4)."""

from dataclasses import replace

import pytest
from docx import Document
from docx.oxml import OxmlElement

from app.checks.agreement.cues import load_cues
from app.checks.agreement.extract import extract_statements
from app.config import Settings
from app.ingest.docx import extract_document

AIM = "To implement signed route manifests in the cargo inspection prototype."


def _extract(
    tmp_path,
    *,
    heading="Objectives",
    text=AIM,
    style="Normal",
    chapter=None,
    omitted=False,
    result_text=None,
    result_heading="Results",
):
    document = Document()
    if chapter:
        document.add_heading(chapter, 1)
    document.add_heading(heading, 2 if chapter else 1)
    if omitted:
        paragraph = document.add_paragraph()
        expression = OxmlElement("m:oMath")
        run = OxmlElement("m:r")
        content = OxmlElement("m:t")
        content.text = "x = 6"
        run.append(content)
        expression.append(run)
        paragraph._p.append(expression)
    document.add_paragraph(text, style=style)
    document.add_heading(result_heading, 1)
    document.add_paragraph(
        result_text
        or "The prototype implemented signed route manifests during the cargo inspection trial."
    )
    path = tmp_path / "objectives.docx"
    document.save(path)
    return extract_document(str(path), Settings(_env_file=None))


@pytest.mark.parametrize("style", ["Normal", "List Bullet", "List Number"])
def test_bare_objective_and_native_word_lists_retain_exact_text_and_anchor(tmp_path, style):
    extraction = _extract(tmp_path, style=style)
    [intent] = extract_statements(extraction, settings=Settings(_env_file=None)).intents
    assert intent.text == AIM
    assert intent.anchor == "¶1" and intent.paragraph == 1
    assert intent.cue == "intent_section_prose"


@pytest.mark.parametrize(
    "heading",
    [
        "Observations",
        "Objectives of Prior Work",
        "Future Objectives",
        "Excluded Objectives",
        "Objectives of Other Studies",
        "Out of Scope Objectives",
    ],
)
def test_bare_infinitive_is_not_promoted_outside_own_objective_heading(tmp_path, heading):
    extraction = _extract(tmp_path, heading=heading)
    assert extract_statements(extraction, settings=Settings(_env_file=None)).intents == []


@pytest.mark.parametrize(
    "chapter", ["Chapter 2 Review of Related Literature", "Chapter 3 Methodology"]
)
def test_nested_objectives_in_excluded_chapters_do_not_gain_a_weaker_intent_cue(tmp_path, chapter):
    extraction = _extract(tmp_path, chapter=chapter)
    assert extract_statements(extraction, settings=Settings(_env_file=None)).intents == []


@pytest.mark.parametrize(
    "text",
    [
        "To avoid overheating, operators disconnected the pump before recording the voltage.",
        "To advise future researchers on the next inspection step.",
        "To implement a feature that is outside the scope of this study.",
        '"To implement signed route manifests in the cargo inspection prototype.',
        "To date the prototype has not implemented tidal sensor alerts.",
        "To the investigators these results demonstrate a limitation of the prototype.",
        "To this end the researchers reviewed earlier sensor deployments.",
    ],
)
def test_purpose_clauses_future_scope_and_unknown_quotes_are_not_objectives(tmp_path, text):
    extraction = _extract(tmp_path, text=text)
    assert extract_statements(extraction, settings=Settings(_env_file=None)).intents == []


def test_a_quotation_style_does_not_supply_the_thesis_own_objective(tmp_path):
    extraction = _extract(tmp_path, style="Quote")
    assert extract_statements(extraction, settings=Settings(_env_file=None)).intents == []


def test_missing_legacy_style_provenance_cannot_establish_an_unquoted_aim(tmp_path):
    extraction = _extract(tmp_path)
    blocks = [block.model_copy(update={"paragraph_style": None}) for block in extraction.blocks]
    extraction = extraction.model_copy(update={"blocks": blocks})
    assert extract_statements(extraction, settings=Settings(_env_file=None)).intents == []


def test_omitted_native_content_stops_the_objective_heading_scope(tmp_path):
    extraction = _extract(tmp_path, omitted=True)
    assert extract_statements(extraction, settings=Settings(_env_file=None)).intents == []


def test_the_new_cue_and_heading_are_configurable_without_changing_existing_modal_cues(tmp_path):
    extraction = _extract(
        tmp_path,
        heading="Intended Contributions",
        text="Deliver signed manifests for the inspection prototype.",
    )
    cues = replace(
        load_cues(),
        objective_heading_synonyms=frozenset({"intended contributions"}),
        objective_prose_patterns=(r"^deliver\s+[^,;:]+[.!?]?$",),
    )
    assert (
        len(extract_statements(extraction, cues=cues, settings=Settings(_env_file=None)).intents)
        == 1
    )
    assert (
        extract_statements(
            extraction,
            cues=replace(cues, objective_prose_enabled=False),
            settings=Settings(_env_file=None),
        ).intents
        == []
    )


@pytest.mark.parametrize(
    "text", ["1. " + AIM, "This study aims to implement signed route manifests."]
)
def test_existing_list_and_modal_cues_remain_available(tmp_path, text):
    extraction = _extract(tmp_path, text=text)
    intents = extract_statements(extraction, settings=Settings(_env_file=None)).intents
    assert len(intents) == 1
    assert intents[0].cue != "intent_section_prose"


def test_many_aims_share_one_full_scope_quote_parse(tmp_path, monkeypatch):
    from app.ingest import quotations

    document = Document()
    document.add_heading("Objectives", 1)
    for index in range(80):
        document.add_paragraph(
            f"To implement route manifest validation for inspection zone {index}."
        )
    path = tmp_path / "many.docx"
    document.save(path)
    extraction = extract_document(str(path), Settings(_env_file=None))
    calls = []
    original = quotations.inline_quote_spans

    def tracked(text, policy):
        calls.append(len(text))
        return original(text, policy)

    monkeypatch.setattr(quotations, "inline_quote_spans", tracked)
    assert extract_statements(extraction, settings=Settings(_env_file=None)).intents
    assert len(calls) == 1


def test_scope_cache_keeps_cross_paragraph_quotes_and_independent_known_aims(tmp_path):
    document = Document()
    document.add_paragraph("Historical context without captured style metadata.")
    document.add_heading("Objectives", 1)
    document.add_paragraph('"The borrowed list begins here')
    document.add_paragraph(AIM)
    document.add_paragraph('and the borrowed list ends here."')
    own = "To verify cargo seals against the independent inspection ledger."
    document.add_paragraph(own)
    path = tmp_path / "spanning.docx"
    document.save(path)
    extraction = extract_document(str(path), Settings(_env_file=None))
    extraction.blocks[0].paragraph_style = None
    intents = extract_statements(extraction, settings=Settings(_env_file=None)).intents
    assert [intent.text for intent in intents] == [own]


@pytest.mark.parametrize(
    "outcome",
    [
        "The prototype did not develop signed evidence bundles during the reported evaluation.",
        "The prototype evaluated valve settling time during the controlled measurement trial.",
        "The prototype retained complete interval records from the reported laboratory experiment.",
    ],
)
def test_supported_denied_and_past_actions_are_results_only_inside_results(tmp_path, outcome):
    for heading, expected in [("Results", 1), ("Observations", 0)]:
        extraction = _extract(tmp_path, result_heading=heading, result_text=outcome)
        outcomes = extract_statements(extraction, settings=Settings(_env_file=None)).outcomes
        assert len(outcomes) == expected
        if outcomes:
            assert outcomes[0].text == outcome and outcomes[0].anchor == "¶3"


def test_new_result_verbs_keep_the_future_work_guard(tmp_path):
    extraction = _extract(
        tmp_path,
        result_text=(
            "Future researchers could later evaluate the method "
            "that retained the old instrument records."
        ),
    )
    assert extract_statements(extraction, settings=Settings(_env_file=None)).outcomes == []
