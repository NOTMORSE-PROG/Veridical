"""BUG-590: an unreadable line withholds only the linkage findings it could change.

Before this fix one reference entry the reader could not parse, or one in-text
citation the scanner could not read, withheld every orphan and uncited finding
in an author-year thesis. These controls pin the narrower rule in both
directions: what the unreadable line could affect stays unassessed and
counted, and what it cannot affect is still reported.
"""

from dataclasses import asdict
from unittest.mock import AsyncMock

from app.checks.citations import verify
from app.checks.citations.extract import ScanGapSite, extract_in_text_citations
from app.checks.citations.linkage_guard import (
    WITHHELD_ENTRY_TEXT_NAMES_IT,
    WITHHELD_POSSIBLE_SOURCE,
    WITHHELD_POSSIBLE_TARGET,
    WITHHELD_SCAN_INCOMPLETE,
    WITHHELD_UNREAD_CITATION,
    WITHHELD_UNRECOGNIZED_MENTION,
    reportable_orphans,
    scoped_absence_candidates,
)
from app.config import Settings
from app.ingest.patterns import load_patterns
from app.ingest.references import extract_references
from app.ingest.schemas import TextBlock
from app.models.citation import Citation
from app.models.enums import CitationParseStatus, ResultOutcome
from app.models.run import Flag
from tests.test_stabilization_integrity_controls import MemorySession, source

SETTINGS = Settings(_env_file=None)
LINKED = "Reyes, J. (2024). A linked control. Journal of Controls, 1(1), 1-2."
UNUSED = (
    "Black, S. (2017). How much time does it take to plan a vacation. Travel Journal, 4(1), 1-9."
)
# Parsed with its author and year by the reference reader, but not as a whole entry.
UNPARSED_LOUKIL = (
    "Loukil, M., et al. (2023). Face shape classification based on deep face. 2023 IEEE "
    "Afro-Mediterranean Conference on Artificial Intelligence (AMCAI), 1-7."
)
NO_AUTHOR_NO_DATE = (
    "Systems Analysis and Design, Global Edition. (n.d.). Google Books. https://books.google.com/x"
)
NO_AUTHOR_2024 = (
    "Analysis of Recommender Systems: A Systematic Literature review. (2024b). IEEE Xplore. "
    "https://ieeexplore.ieee.org/x"
)
# Too many capitalised words before the year: the scanner cannot tell where
# the author list starts, so this mark is unread.
UNREAD_MARK = (
    "As Shown By The Garcia Regional Development Planning Research And Statistics "
    "Consortium Members (2020), the design works."
)


def persisted(extraction):
    return [
        Citation(manuscript_id=1, **asdict(draft))
        for draft in extract_references(extraction, load_patterns())
    ]


async def run(monkeypatch, body, refs, settings=SETTINGS):
    extraction = source(body, refs)
    citations = persisted(extraction)
    session = MemorySession()
    confirmed = verify.CitationVerdict(verify.VerdictKind.existence_confirmed)
    monkeypatch.setattr(verify, "verify_citation", AsyncMock(return_value=confirmed))
    monkeypatch.setattr(verify, "write_audit_event", AsyncMock())
    monkeypatch.setattr("app.checks.source_quality.write_audit_event", AsyncMock())
    result = await verify.run_citation_integrity_check(
        session, None, 1, citations, extraction, load_patterns(), settings, None
    )
    flags = [row for row in session.rows if isinstance(row, Flag)]
    return result, flags, citations


def linkage(flags):
    out = set()
    for flag in flags:
        kind = flag.detail.get("kind")
        if kind == "orphan_in_text_citation":
            out.add(("orphan", tuple(flag.detail["surnames"]), flag.detail["year"]))
        elif kind == "uncited_reference":
            out.add(("uncited", flag.evidence_excerpt.split(",")[0]))
    return out


def test_fixture_entries_parse_the_way_the_rules_assume():
    extraction = source("Body (Reyes, 2024).", [LINKED, UNUSED, UNPARSED_LOUKIL])
    status = {c.raw_text.split(",")[0]: c for c in persisted(extraction)}
    assert status["Reyes"].parse_status == CitationParseStatus.parsed
    assert status["Black"].parse_status == CitationParseStatus.parsed
    assert status["Loukil"].parse_status != CitationParseStatus.parsed
    assert status["Loukil"].authors and status["Loukil"].year


async def test_unparsed_entry_does_not_withhold_an_orphan_it_cannot_be(monkeypatch):
    result, flags, _ = await run(
        monkeypatch,
        "Prior work (Reyes, 2024) and a ghost source (Nakamura, 2021) are discussed.",
        [LINKED, UNPARSED_LOUKIL],
    )
    assert ("orphan", ("Nakamura",), "2021") in linkage(flags)
    assert result.detail["n_orphans"] == 1
    assert result.detail["n_orphans_unassessed"] == 0
    # The unparsed entry itself stays unassessed: nothing cites it, but a
    # citation of it may be the one the reader could not match.
    assert result.outcome == ResultOutcome.escalated
    assert result.detail["n_linkage_unassessed"] >= 1


async def test_orphan_is_withheld_when_an_unparsed_entry_could_be_its_source(monkeypatch):
    shouted = "LOUKIL, M. AND BENALI, S. 2023. Face shape classification. IEEE AMCAI."
    result, flags, citations = await run(
        monkeypatch,
        "Prior work (Reyes, 2024) and face shapes (Loukil, 2023) are discussed.",
        [LINKED, shouted],
    )
    assert not any(kind == "orphan" for kind, *_ in linkage(flags))
    assert result.detail["n_orphans_unassessed"] == 1
    withheld = result.detail["orphans_unassessed"][0]
    assert withheld["reason"] == WITHHELD_POSSIBLE_SOURCE
    loukil = next(c for c in citations if c.raw_text.startswith("LOUKIL"))
    assert loukil.parse_status != CitationParseStatus.parsed
    assert withheld["possible_reference_order_indexes"] == [loukil.order_index]
    assert result.outcome == ResultOutcome.escalated


async def test_entry_with_no_author_and_no_date_could_be_the_source_of_any_orphan(monkeypatch):
    result, flags, _ = await run(
        monkeypatch,
        "Prior work (Reyes, 2024) and a design text (Kendall & Kendall, 2020) are discussed.",
        [LINKED, NO_AUTHOR_NO_DATE],
    )
    assert not any(kind == "orphan" for kind, *_ in linkage(flags))
    assert result.detail["n_orphans_unassessed"] == 1


async def test_entry_with_no_author_withholds_only_orphans_of_its_year(monkeypatch):
    result, flags, _ = await run(
        monkeypatch,
        "Prior work (Reyes, 2024), a review (Ayemowa et al., 2024) and a ghost "
        "(Nakamura, 2021) are discussed.",
        [LINKED, NO_AUTHOR_2024],
    )
    found = linkage(flags)
    assert ("orphan", ("Nakamura",), "2021") in found
    assert not any(kind == "orphan" and names == ("Ayemowa",) for kind, names, *_ in found)
    assert result.detail["n_orphans_unassessed"] == 1


async def test_orphan_naming_a_parsed_entrys_publisher_and_year_is_withheld(monkeypatch):
    lena = "Lena, P. (2023). Heritage sites still the best draw. Philippine News Agency. https://pna.gov.ph/x"
    result, flags, _ = await run(
        monkeypatch,
        "Prior work (Reyes, 2024; Lena, 2023) agrees, as Philippine News Agency (2023) reports.",
        [LINKED, lena],
    )
    assert not any(kind == "orphan" for kind, *_ in linkage(flags))
    assert result.detail["orphans_unassessed"][0]["reason"] == WITHHELD_ENTRY_TEXT_NAMES_IT
    assert result.outcome == ResultOutcome.escalated
    # Nothing else is unassessed here: the withheld orphan alone must escalate.
    assert result.detail["n_linkage_unassessed"] == 0
    assert result.detail["coverage_reason"] == "author_year_citation_parse_unassessed"


async def test_orphan_naming_one_word_of_an_entrys_author_is_withheld(monkeypatch):
    statista = "Statista. (2023). Number of international tourist arrivals. Retrieved from https://www.statista.com/x"
    result, flags, _ = await run(
        monkeypatch,
        "Prior work (Reyes, 2024) agrees, and Statista Research Department (2023) counts arrivals.",
        [LINKED, statista],
    )
    assert linkage(flags) == set()
    assert result.detail["n_orphans_unassessed"] == 1


async def test_name_differing_only_by_accents_is_not_a_finding_either_way(monkeypatch):
    martinez = "Martínez, L. (2023). Travel app use continuance. Tourism Journal, 2(1), 1-9."
    result, flags, _ = await run(
        monkeypatch,
        "Prior work (Reyes, 2024) is discussed. Martinez (2023) studied continuance.",
        [LINKED, martinez],
    )
    assert linkage(flags) == set()
    assert result.detail["n_orphans_unassessed"] == 1


async def test_unread_mark_naming_one_word_of_an_entrys_author_withholds_it(monkeypatch):
    statista = "Statista Research Department. (2023). Tourist arrivals. Statista Portal, 1(1), 1-2."
    unread = (
        "As Reported By The Statista Regional Development Planning Board Of Many Member "
        "Groups (2023), arrivals grew."
    )
    result, flags, citations = await run(
        monkeypatch, f"Prior work (Reyes, 2024) is discussed. {unread}", [LINKED, statista, UNUSED]
    )
    assert result.detail["citation_parse_issues"]
    found = linkage(flags)
    assert ("uncited", "Black") in found
    assert not any(kind == "uncited" and name.startswith("Statista") for kind, name in found)


async def test_unread_dotted_acronym_withholds_the_entry_it_abbreviates(monkeypatch):
    dti = "Department of Trade and Industry. (2021). Export roadmap. DTI Reports, 1(1), 1-2."
    result, flags, _ = await run(
        monkeypatch,
        "Prior work (Reyes, 2024) is discussed. Exports rose (D.T.I., 2021, see Annex B).",
        [LINKED, dti, UNUSED],
    )
    assert result.detail["citation_parse_issues"]
    found = linkage(flags)
    assert ("uncited", "Black") in found
    assert not any(kind == "uncited" and name.startswith("Department") for kind, name in found)


async def test_numbered_list_keeps_the_whole_list_rule_for_scan_gaps(monkeypatch):
    refs = [
        "[1] Reyes, J. (2024). A linked control. Journal of Controls, 1(1), 1-2.",
        "[2] " + UNUSED,
    ]
    result, flags, citations = await run(
        monkeypatch, f"Prior work is reported in [1]. {UNREAD_MARK}", refs
    )
    assert result.detail["citation_parse_issues"]
    assert not any(kind == "uncited" for kind, *_ in linkage(flags))
    assert result.detail["n_linkage_unassessed"] == len(citations)


async def test_entry_a_withheld_orphan_may_cite_is_not_called_unused(monkeypatch):
    lena = "Lena, P. (2023). Heritage sites still the best draw. Philippine News Agency. https://pna.gov.ph/x"
    result, flags, citations = await run(
        monkeypatch,
        "Prior work (Reyes, 2024) agrees, as Philippine News Agency (2023) reports.",
        [LINKED, lena],
    )
    assert linkage(flags) == set()
    lena_row = next(c for c in citations if c.raw_text.startswith("Lena"))
    assert result.detail["uncited_unassessed"] == [
        {"reference_order_index": lena_row.order_index, "reason": WITHHELD_POSSIBLE_TARGET}
    ]


async def test_hyphen_dropped_from_a_surname_withholds_both_sides(monkeypatch):
    xiong = (
        "Xiong-Bin, W., Guan, H., & Ma, J. (2017). A tour route planning model. "
        "Journal of Tours, 3(2), 1-9."
    )
    result, flags, _ = await run(
        monkeypatch,
        "Prior work (Reyes, 2024) is discussed. Xiongbin (2017) planned tour routes.",
        [LINKED, xiong],
    )
    assert linkage(flags) == set()
    assert result.detail["n_orphans_unassessed"] == 1
    # The entry is both the orphan's possible source and named beside its year.
    assert {row["reason"] for row in result.detail["uncited_unassessed"]} == {
        WITHHELD_POSSIBLE_TARGET
    }


async def test_unread_citation_withholds_only_entries_it_could_name(monkeypatch):
    garcia = "Garcia, M. (2020). Regional planning statistics. Planning Journal, 2(1), 3-4."
    result, flags, citations = await run(
        monkeypatch,
        f"Prior work (Reyes, 2024) is discussed. {UNREAD_MARK}",
        [LINKED, garcia, UNUSED],
    )
    assert "author_limit" in result.detail["citation_parse_issues"]
    found = linkage(flags)
    assert ("uncited", "Black") in found
    assert ("uncited", "Garcia") not in found
    garcia_row = next(c for c in citations if c.raw_text.startswith("Garcia"))
    assert result.detail["uncited_unassessed"] == [
        {"reference_order_index": garcia_row.order_index, "reason": WITHHELD_UNREAD_CITATION}
    ]
    assert result.detail["n_linkage_unassessed"] == 1
    assert result.detail["unread_citation_sites"][0]["reasons"] == ["author_limit"]
    assert result.outcome == ResultOutcome.escalated
    assert result.detail["coverage_reason"] == "author_year_citation_parse_unassessed"


async def test_a_scan_that_stopped_early_still_withholds_every_absence(monkeypatch):
    settings = Settings(_env_file=None, citation_in_text_max_matches=1)
    result, flags, citations = await run(
        monkeypatch,
        "Prior work (Reyes, 2024) and more work (Reyes, 2024) are discussed.",
        [LINKED, UNUSED],
        settings,
    )
    assert "citation_count_limit" in result.detail["citation_parse_issues"]
    assert not any(kind == "uncited" for kind, *_ in linkage(flags))
    assert result.detail["n_linkage_unassessed"] == len(citations)


async def test_reference_named_beside_its_year_in_an_unrecognised_style_is_not_called_unused(
    monkeypatch,
):
    result, flags, _ = await run(
        monkeypatch,
        "Prior work (Reyes, 2024) is discussed. In 2017, Black's survey timed vacation planning.",
        [LINKED, UNUSED],
    )
    assert ("uncited", "Black") not in linkage(flags)
    assert result.detail["uncited_unassessed"][0]["reason"] == WITHHELD_UNRECOGNIZED_MENTION


async def test_clean_author_year_document_keeps_reporting_both_kinds(monkeypatch):
    result, flags, _ = await run(
        monkeypatch,
        "Prior work (Reyes, 2024) and a ghost source (Nakamura, 2021) are discussed.",
        [LINKED, UNUSED],
    )
    assert linkage(flags) == {("orphan", ("Nakamura",), "2021"), ("uncited", "Black")}
    assert result.detail["n_orphans_unassessed"] == 0
    assert result.detail["uncited_unassessed"] == []


async def test_withheld_findings_keep_honest_wording(monkeypatch):
    _, flags, _ = await run(
        monkeypatch,
        "Prior work (Reyes, 2024) and a ghost source (Nakamura, 2021) are discussed.",
        [LINKED, UNUSED, UNPARSED_LOUKIL],
    )
    for flag in flags:
        reason = (flag.detail.get("reason") or "").casefold()
        assert "possible" in reason
        assert not any(word in reason for word in ("fake", "fabricat", "on purpose", "dishonest"))


def test_gap_reason_without_a_site_withholds_every_candidate():
    ref = Citation(
        manuscript_id=1,
        order_index=0,
        raw_text=UNUSED,
        authors=["Black, S."],
        year=2017,
        parse_status=CitationParseStatus.parsed,
    )
    kept, withheld = scoped_absence_candidates([ref], [], [], {"a_new_gap"}, SETTINGS)
    assert kept == [] and withheld == {0: WITHHELD_SCAN_INCOMPLETE}
    site = ScanGapSite(frozenset({"a_new_gap"}), "Nothing about it here.", 1, 1)
    kept, withheld = scoped_absence_candidates([ref], [], [site], {"a_new_gap"}, SETTINGS)
    assert kept == [ref] and withheld == {}


def test_scan_records_the_paragraph_of_each_unread_mark():
    sites: list[ScanGapSite] = []
    gaps: set[str] = set()
    blocks = [
        TextBlock(
            page=3, paragraph=1, text="Clean work (Reyes, 2024).", max_font_size=11, bold_ratio=0
        ),
        TextBlock(page=4, paragraph=2, text=UNREAD_MARK, max_font_size=11, bold_ratio=0),
    ]
    found = extract_in_text_citations(
        blocks, settings=SETTINGS, coverage_gaps=gaps, gap_sites=sites
    )
    assert [c.surnames for c in found] == [["Reyes"]]
    assert gaps == {"author_limit"}
    assert [(s.page, s.paragraph, set(s.reasons)) for s in sites] == [(4, 2, {"author_limit"})]
    assert "Garcia" in sites[0].text


def test_orphan_rule_needs_both_a_possible_name_and_a_possible_year():
    entry = Citation(
        manuscript_id=1,
        order_index=0,
        raw_text="Smith, J. (2019). An earlier study. Journal, 1(1), 1-2.",
        authors=["Smith, J."],
        year=2019,
        parse_status=CitationParseStatus.parse_failed,
    )

    class Cite:
        def __init__(self, surnames, year):
            self.surnames, self.year = surnames, year

    other_year, other_name, both = (
        Cite(["Smith"], "2020"),
        Cite(["Jones"], "2019"),
        Cite(["Smith"], "2019"),
    )
    kept, withheld = reportable_orphans([other_year, other_name, both], [entry], SETTINGS)
    assert kept == [other_year, other_name]
    assert [w.citation for w in withheld] == [both]


def test_orphan_written_as_the_entrys_short_form_is_withheld():
    entry = Citation(
        manuscript_id=1,
        order_index=0,
        raw_text="Technological Institute of the Philippines. (2020). Student handbook. Manila.",
        authors=["Technological Institute of the Philippines"],
        year=2020,
        parse_status=CitationParseStatus.parsed,
    )

    class Cite:
        surnames, year = ["TIP"], "2020"

    kept, withheld = reportable_orphans([Cite()], [entry], SETTINGS)
    assert kept == [] and withheld[0].possible_reference_order_indexes == (0,)


def test_scan_limit_is_recorded_as_a_whole_document_gap():
    sites: list[ScanGapSite] = []
    text = "Work (Reyes, 2024) and (Black, 2017)."
    blocks = [TextBlock(page=1, paragraph=1, text=text, max_font_size=11, bold_ratio=0)]
    settings = Settings(_env_file=None, citation_in_text_max_matches=1)
    extract_in_text_citations(blocks, settings=settings, coverage_gaps=set(), gap_sites=sites)
    assert [(s.text, set(s.reasons)) for s in sites] == [(None, {"citation_count_limit"})]
