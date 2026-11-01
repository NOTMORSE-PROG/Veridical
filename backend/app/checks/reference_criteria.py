"""Criteria a format states about the reference list itself (V-098 a, d; F3, F5).

A bibliography's size and a source list's quality are facts about the whole
list, so a single quoted passage can never establish them. That is why the
quote-based support check rejected these grades by construction. After the
citation check (F5) has looked every entry up, this step judges them from the
list instead:

- reference inventory (the format counts major references and internet
  sites): the counts are worked out from F5's lookups, and the format's own
  descriptors say which counts each level allows. The grader's level still
  speaks to the format errors; the counts can only lower it. An entry no
  catalogue confirmed is typed from its own citation details where they say
  what it is (a journal with volume and pages, proceedings, a publisher, a
  preprint server); the rest could be either kind, so the level is decided
  only when it is the same however they turn out.
- reference quality (the format asks how far sources are peer-reviewed):
  the same lookups give a profile of the list (journal and conference
  papers, books, other identified works, web sources, unidentified entries).
  The model reads the profile against the format's descriptors twice,
  separately; two matching answers decide, anything else goes to the
  instructor. A format may instead set share ranges per level in its
  settings, and then no model is asked.

Nothing here is decided without the evidence being shown: the counts or the
profile become the row's reasoning, and the grader's own result is kept
beside the decision.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_event
from app.checks.levels import match_level, outcome_and_score
from app.models.enums import CheckKind, ResultOutcome
from app.models.rubric import Criterion
from app.models.run import CheckResult

INVENTORY = "reference_inventory"
QUALITY = "reference_quality"

# Outcomes a reference strategy may replace. Rows the product already could
# not assess (an outage, a source it could not read) keep their own reason.
_REPLACEABLE = {ResultOutcome.passed, ResultOutcome.failed, ResultOutcome.escalated}


@dataclass(frozen=True)
class Strategy:
    name: str
    patterns: tuple[re.Pattern[str], ...]
    min_levels: int


@dataclass(frozen=True)
class ReferenceData:
    strategies: tuple[Strategy, ...]
    count_conditions: tuple[tuple[str, re.Pattern[str]], ...]
    number_words: dict[str, int]
    type_classes: dict[str, frozenset[str]]
    major_classes: frozenset[str]
    confirmed_statuses: frozenset[str]
    web_cues: tuple[re.Pattern[str], ...]
    # Typing an unconfirmed entry from its own citation text, in order.
    text_types: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = ()
    web_scholarly_cues: tuple[re.Pattern[str], ...] = ()
    web_address: re.Pattern[str] | None = None
    # (pattern, min_share, max_share) a quality level's own words imply.
    quality_quantifiers: tuple[tuple[re.Pattern[str], float | None, float | None], ...] = ()


@lru_cache
def load_reference_data(path: Path) -> ReferenceData:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return ReferenceData(
        strategies=tuple(
            Strategy(
                spec["name"],
                tuple(re.compile(p, re.I) for p in spec["level_patterns"]),
                int(spec["min_levels"]),
            )
            for spec in raw["strategies"]
        ),
        count_conditions=tuple(
            (item["kind"], re.compile(item["pattern"], re.I)) for item in raw["count_conditions"]
        ),
        number_words={k.lower(): int(v) for k, v in raw["number_words"].items()},
        type_classes={k: frozenset(v) for k, v in raw["type_classes"].items()},
        major_classes=frozenset(raw["major_classes"]),
        confirmed_statuses=frozenset(raw["confirmed_statuses"]),
        web_cues=tuple(re.compile(p, re.I) for p in raw["web_entry_cues"]),
        text_types=tuple(
            (rule["class"], tuple(re.compile(p, re.I) for p in rule["patterns"]))
            for rule in raw.get("entry_text_types", [])
        ),
        web_scholarly_cues=tuple(re.compile(p, re.I) for p in raw.get("web_scholarly_cues", [])),
        web_address=re.compile(raw["web_address"], re.I) if raw.get("web_address") else None,
        quality_quantifiers=tuple(
            (re.compile(item["pattern"], re.I), item.get("min_share"), item.get("max_share"))
            for item in raw.get("quality_quantifiers", [])
        ),
    )


def _flat(text: str) -> str:
    return " ".join((text or "").split())


def _levels(criterion: Any) -> list[dict]:
    return sorted(getattr(criterion, "levels", None) or [], key=lambda level: int(level["level"]))


def strategy_for(criterion: Any, data: ReferenceData) -> str | None:
    """The reference strategy a criterion's own descriptors call for, if any."""
    levels = _levels(criterion)
    if not levels:
        return None
    for strategy in data.strategies:
        hits = sum(
            any(pattern.search(_flat(level["descriptor"])) for pattern in strategy.patterns)
            for level in levels
        )
        if hits >= strategy.min_levels:
            return strategy.name
    return None


# --- the reference list as classified entries ---------------------------------


@dataclass
class Profile:
    """Every entry of the list in one class. Catalogue-confirmed classes come
    first; an entry no catalogue confirmed is typed from its own citation text
    where that text says what it is (`*_text`), and is `unclassified` where it
    does not."""

    entries: int = 0
    peer_reviewed: int = 0
    book: int = 0
    other_identified: int = 0
    journal_or_conference_text: int = 0
    book_text: int = 0
    repository_text: int = 0
    web: int = 0
    unclassified: int = 0
    unclassified_entries: list[int] = field(default_factory=list)
    text_typed_entries: dict[str, list[int]] = field(default_factory=dict)
    # Unidentified because a catalogue could not be reached, not because none
    # knew the entry: said so, since a later check may identify them.
    lookup_unavailable: int = 0

    @property
    def major(self) -> int:
        return self.peer_reviewed + self.book + self.major_from_text

    @property
    def major_from_text(self) -> int:
        return self.journal_or_conference_text + self.book_text

    def as_detail(self) -> dict:
        return {
            "entries": self.entries,
            "journal_or_conference_papers": self.peer_reviewed,
            "books": self.book,
            "other_identified_works": self.other_identified,
            "journal_or_conference_papers_by_citation_text": self.journal_or_conference_text,
            "books_by_citation_text": self.book_text,
            "preprints_repositories_or_theses_by_citation_text": self.repository_text,
            "web_sources": self.web,
            "unidentified": self.unclassified,
            "unidentified_entry_numbers": self.unclassified_entries,
            "entry_numbers_typed_by_citation_text": self.text_typed_entries,
            "unidentified_because_a_catalogue_was_unreachable": self.lookup_unavailable,
        }


def _text_type(raw_text: str, isbn: str | None, data: ReferenceData) -> str | None:
    """What an unconfirmed entry's own citation text says it is, if anything.
    The first class with a matching cue wins; a repository cue outranks an
    ISBN, which outranks the journal and book cues."""
    text = _flat(raw_text)
    matched = [name for name, patterns in data.text_types if any(p.search(text) for p in patterns)]
    if "repository_text" in matched:
        return "repository_text"
    if isbn:
        return "book_text"
    return matched[0] if matched else None


def _web_entry(raw_text: str, url: str | None, data: ReferenceData) -> bool:
    """A web source: the entry says it was retrieved or accessed online and
    gives an address. The parser can lose an address that wraps across lines,
    so the entry's own text counts as well. An entry with a DOI or a publisher
    address may be a journal article retrieved online, so it is not called a
    web source: counting it as one could lower a grade."""
    text = _flat(raw_text)
    if not any(cue.search(text) for cue in data.web_cues):
        return False
    if not (url or (data.web_address is not None and data.web_address.search(text))):
        return False
    return not any(cue.search(text) for cue in data.web_scholarly_cues)


def classify_entry(
    record: dict,
    raw_text: str,
    url: str | None,
    data: ReferenceData,
    isbn: str | None = None,
) -> str:
    if record.get("status") in data.confirmed_statuses:
        tags = set(record.get("work_types") or [])
        for name in ("peer_reviewed", "book"):
            if tags & data.type_classes.get(name, frozenset()):
                return name
        if tags:
            return "other_identified"
    by_text = _text_type(raw_text, isbn, data)
    if by_text is not None:
        return by_text
    if _web_entry(raw_text, url, data):
        return "web"
    return "unclassified"


def reference_profile(
    records: list[dict], citations: dict[int, Any], data: ReferenceData
) -> Profile:
    profile = Profile()
    for record in sorted(records, key=lambda r: r["reference_order_index"]):
        index = record["reference_order_index"]
        citation = citations.get(index)
        kind = classify_entry(
            record,
            getattr(citation, "raw_text", "") or "",
            getattr(citation, "url", None),
            data,
            getattr(citation, "isbn", None),
        )
        profile.entries += 1
        if kind == "unclassified":
            profile.unclassified += 1
            profile.unclassified_entries.append(index + 1)
            profile.lookup_unavailable += record.get("status") == "api_down"
        elif kind == "other_identified":
            profile.other_identified += 1
        else:
            setattr(profile, kind, getattr(profile, kind) + 1)
            if kind.endswith("_text"):
                profile.text_typed_entries.setdefault(kind, []).append(index + 1)
    return profile


# --- reference inventory: counts against the format's own numbers --------------


def _number(word: str, data: ReferenceData) -> int | None:
    return int(word) if word.isdigit() else data.number_words.get(word.lower())


def count_conditions(
    criterion: Any, data: ReferenceData
) -> dict[int, list[tuple[str, int]]] | None:
    """Each level's count conditions, read from its descriptor. None unless
    every level states at least one: a scale that only counts on some levels
    cannot be read as counts at all."""
    result: dict[int, list[tuple[str, int]]] = {}
    for level in _levels(criterion):
        text = _flat(level["descriptor"])
        found: list[tuple[str, int]] = []
        for kind, pattern in data.count_conditions:
            for match in pattern.finditer(text):
                number = _number(match.group("n"), data)
                if number is None:
                    return None
                found.append((kind, number))
        if not found:
            return None
        result[int(level["level"])] = found
    return result


def _holds(conditions: list[tuple[str, int]], major: int, internet: int) -> bool:
    for kind, number in conditions:
        if kind == "major_at_least" and major < number:
            return False
        if kind == "major_more_than" and major <= number:
            return False
        if kind == "internet_at_most" and internet > number:
            return False
    return True


def count_level(
    conditions: dict[int, list[tuple[str, int]]], major: int, internet: int
) -> int | None:
    """The highest level whose counts hold, or None if none does."""
    fitting = [ordinal for ordinal, conds in conditions.items() if _holds(conds, major, internet)]
    return max(fitting) if fitting else None


def inventory_bounds(
    conditions: dict[int, list[tuple[str, int]]], profile: Profile
) -> tuple[int | None, int | None]:
    """The count level if every unidentified entry is the worst case for the
    grade (an internet site) and if every one is the best case (a major
    reference). Fewer major references and more internet sites never raise
    a level, so the truth lies between the two."""
    worst = count_level(conditions, profile.major, profile.web + profile.unclassified)
    best = count_level(conditions, profile.major + profile.unclassified, profile.web)
    return worst, best


# --- reference quality: what the counts prove about the levels -----------------


def quality_from_ranges(
    criterion: Any, profile: Profile, ranges: dict | None
) -> tuple[int | None, str] | None:
    """A level from share ranges the format itself sets, or None when it sets
    none. Ranges give, per level number, [low, high] for the share of the
    list that is journal or conference papers. Unidentified entries could be
    either, so a level is decided only when both extremes fall in its range."""
    if not ranges or not profile.entries:
        return None
    papers = profile.peer_reviewed + profile.journal_or_conference_text
    low_share = papers / profile.entries
    high_share = (papers + profile.unclassified) / profile.entries

    def level_for(share: float) -> int | None:
        for ordinal, bounds in ranges.items():
            low, high = bounds
            if low <= share <= high:
                return int(ordinal)
        return None

    at_low, at_high = level_for(low_share), level_for(high_share)
    if at_low is not None and at_low == at_high:
        return at_low, "ranges"
    return None, "ranges_inconclusive"


def scholarly_share_bounds(profile: Profile) -> tuple[float, float]:
    """The lowest and highest share of the list that can be journal or
    conference papers or books: unidentified entries and other identified
    works (datasets, reports, standards) could go either way."""
    low = profile.major / profile.entries
    high = (profile.major + profile.unclassified + profile.other_identified) / profile.entries
    return low, high


def quality_level_fits(criterion: Any, ordinal: int, profile: Profile, data: ReferenceData) -> dict:
    """Whether the counts show a quality level, from its own words ("most
    ... not peer-reviewed", "primarily peer-reviewed"), however the
    unidentified entries turn out. A level whose words imply no share cannot
    be checked, so it does not fit."""
    descriptor = _flat(
        next(lv["descriptor"] for lv in _levels(criterion) if int(lv["level"]) == ordinal)
    )
    low, high = scholarly_share_bounds(profile)
    implied = [
        (min_share, max_share)
        for pattern, min_share, max_share in data.quality_quantifiers
        if pattern.search(descriptor)
    ]
    fits = bool(implied) and all(
        (min_share is None or low > min_share) and (max_share is None or high < max_share)
        for min_share, max_share in implied
    )
    return {
        "fits": fits,
        "share_bounds": [round(low, 3), round(high, 3)],
        "implied_shares": implied,
    }


# --- applying a decision to the stored criterion row ---------------------------


def _grader_result(row: CheckResult) -> dict:
    detail = row.detail or {}
    keep = (
        "level",
        "verdict",
        "reasoning",
        "proposed_level",
        "proposed_verdict",
        "support_check",
        "votes",
        "agreement",
        "decision_basis",
    )
    return {
        "outcome": row.outcome.value,
        "score": row.score,
        **{key: detail[key] for key in keep if key in detail},
    }


def _escalate(row: CheckResult, reason: str, record: dict) -> None:
    detail = dict(row.detail or {})
    for key in ("verdict", "level", "reasoning"):
        if key in detail:
            detail[f"proposed_{key}"] = detail.pop(key)
    detail.pop("support_caution", None)
    detail["reason"] = reason
    detail["reference_strategy"] = record
    row.outcome, row.score, row.detail = ResultOutcome.escalated, None, detail


def _decide(row: CheckResult, criterion: Any, ordinal: int, reasoning: str, record: dict) -> None:
    name = next(level["name"] for level in _levels(criterion) if int(level["level"]) == ordinal)
    outcome, score, level = outcome_and_score(criterion, name)
    detail = dict(row.detail or {})
    for key in ("proposed_verdict", "proposed_level", "proposed_reasoning", "reason"):
        detail.pop(key, None)
    detail.pop("support_withheld", None)
    detail.pop("support_caution", None)
    # The passes did not choose this level, so the screen must not list their
    # votes beside it as if they had; they stay in the kept grader result.
    detail.pop("votes", None)
    detail.pop("agreement", None)
    detail.update(
        verdict=name,
        level=level.as_detail() if level else None,
        reasoning=reasoning,
        decision_basis=record["decision_basis"],
        reference_strategy=record,
    )
    row.outcome, row.score, row.detail = outcome, score, detail


def _grader_ordinal(criterion: Any, row: CheckResult) -> int | None:
    if row.outcome not in (ResultOutcome.passed, ResultOutcome.failed):
        return None
    verdict = (row.detail or {}).get("verdict")
    match = match_level(criterion, verdict) if verdict else None
    return match.ordinal if match else None


def _counts_sentence(profile: Profile) -> str:
    from_text = (
        f"; {profile.major_from_text} of them typed from the entry's own citation details "
        "because no catalogue confirmed them"
        if profile.major_from_text
        else ""
    )
    repositories = (
        f"{profile.repository_text} preprints, repository copies or theses, "
        if profile.repository_text
        else ""
    )
    sentence = (
        f"The reference list has {profile.entries} entries: "
        f"{profile.major} major references (journal or conference papers and books{from_text}), "
        f"{profile.web} web sources, {repositories}{profile.other_identified} other identified "
        f"works and {profile.unclassified} entries that could not be identified."
    )
    if profile.lookup_unavailable:
        sentence += (
            f" {profile.lookup_unavailable} of those could not be looked up because a "
            "catalogue was unreachable; a later check may identify them."
        )
    return sentence


def _no_level_reason(conditions: dict[int, list[tuple[str, int]]], profile: Profile) -> str:
    """Which of the format's own limits the list breaks at every level, even
    counting every unidentified entry the way that helps the grade most."""
    internet_limits = [
        n for conds in conditions.values() for k, n in conds if k == "internet_at_most"
    ]
    if internet_limits and profile.web > max(internet_limits):
        return (
            f"Every level of this format allows at most {max(internet_limits)} internet "
            f"sites, and {profile.web} entries are web sources,"
        )
    return "No level of this format allows these counts,"


def decide_inventory(
    row: CheckResult, criterion: Any, profile: Profile, data: ReferenceData
) -> None:
    conditions = count_conditions(criterion, data)
    record = {"strategy": INVENTORY, "profile": profile.as_detail(), "grader": _grader_result(row)}
    if conditions is None:
        record["skipped"] = "the format's levels do not all state reference counts"
        row.detail = {**(row.detail or {}), "reference_strategy": record}
        return
    record["count_conditions"] = {str(k): v for k, v in conditions.items()}
    worst, best = inventory_bounds(conditions, profile)
    record["count_level_bounds"] = [worst, best]
    counts = _counts_sentence(profile)
    if worst is None and best is None:
        _escalate(
            row,
            f"{counts} {_no_level_reason(conditions, profile)} so VERIDICAL did not choose "
            "one. Please check the reference list.",
            record,
        )
        return
    if worst != best:
        _escalate(
            row,
            f"{counts} Depending on what the unidentified entries are, the counts fit "
            "different levels, so VERIDICAL did not choose one. Please check those entries.",
            record,
        )
        return
    grader = _grader_ordinal(criterion, row)
    if grader is None:
        # The grader did not settle the format part; the counts alone are not
        # the whole descriptor, so the instructor decides.
        _escalate(
            row,
            f"{counts} The counts fit one level, but the formatting part of this criterion "
            "was not graded automatically. Please check the reference list.",
            record,
        )
        return
    final = min(grader, worst)
    record["decision_basis"] = "reference_counts"
    if grader > worst:
        reasoning = (
            f"{counts} These counts allow no higher level than this one, so the grader's "
            "higher level for the formatting was lowered to it."
        )
    elif grader == worst:
        reasoning = (
            f"{counts} These counts allow this level, and the formatting was graded the same."
        )
    else:
        reasoning = (
            f"{counts} The counts would allow a higher level; the formatting grade sets this one."
        )
    _decide(row, criterion, final, reasoning, record)


def decide_quality(
    row: CheckResult,
    criterion: Any,
    profile: Profile,
    ranges_result: tuple[int | None, str] | None,
    data: ReferenceData,
) -> None:
    """Decide a quality level only where the counts prove it: the one level
    whose own words ("most ... not peer-reviewed", "primarily peer-reviewed")
    hold however the unidentified entries turn out. Where the counts fit
    more than one level, what separates them ("a few are questionable",
    "approved sources") is not something counts show, so the instructor
    decides, told which levels fit."""
    record = {"strategy": QUALITY, "profile": profile.as_detail(), "grader": _grader_result(row)}
    counts = _counts_sentence(profile)
    if not profile.entries:
        _escalate(row, "No reference entries were available to judge.", record)
        return
    if ranges_result is not None:
        ordinal, how = ranges_result
        record["ranges"] = how
        if ordinal is None:
            _escalate(
                row,
                f"{counts} With the unidentified entries either way, the share of journal and "
                "conference papers falls in more than one of this format's ranges, so "
                "VERIDICAL did not choose a level.",
                record,
            )
            return
        record["decision_basis"] = "reference_ranges"
        _decide(
            row, criterion, ordinal, f"{counts} This share falls in this level's range.", record
        )
        return
    checks = {
        int(level["level"]): quality_level_fits(criterion, int(level["level"]), profile, data)
        for level in _levels(criterion)
    }
    record["level_checks"] = {str(ordinal): check for ordinal, check in checks.items()}
    fitting = [ordinal for ordinal, check in checks.items() if check["fits"]]
    names = {int(level["level"]): level["name"] for level in _levels(criterion)}
    if len(fitting) != 1:
        which = (
            f"These counts fit more than one level ({' and '.join(names[o] for o in fitting)}); "
            "what separates them is not something the counts show"
            if fitting
            else "These counts do not confirm any one level's wording however the "
            "unidentified entries turn out"
        )
        _escalate(
            row,
            f"{counts} {which}, so VERIDICAL did not choose one. Please check the reference list.",
            record,
        )
        return
    record["decision_basis"] = "reference_profile"
    _decide(
        row,
        criterion,
        fitting[0],
        f"{counts} Only this level's wording fits these counts, however the unidentified "
        "entries turn out.",
        record,
    )


def reference_list_problem(citation_detail: dict | None) -> str | None:
    """Why the list cannot be judged at all, in the instructor's words, or None."""
    if citation_detail is None:
        return "The citation check did not finish, so the reference list could not be judged."
    if citation_detail.get("reference_selection_issues"):
        return (
            "VERIDICAL could not separate the reference list reliably, so it could not be "
            "counted or judged. Please check the reference list."
        )
    if not citation_detail.get("reference_metadata_coverage"):
        return "No reference list was found to count or judge. Please check the manuscript."
    return None


async def apply_reference_strategies(
    session: AsyncSession,
    check_run_id: int,
    criteria: list[Criterion],
    citation_result: CheckResult | None,
    citations: list[Any],
    data: ReferenceData,
    *,
    quality_ranges: dict | None = None,
) -> int:
    """Judge every reference-strategy criterion of the run from the list.
    Safe to repeat: a row already judged this way is left alone. Returns how
    many rows changed."""
    targets = {
        criterion.id: (criterion, name)
        for criterion in criteria
        if (name := strategy_for(criterion, data)) is not None
    }
    if not targets:
        return 0
    rows = (
        await session.scalars(
            select(CheckResult).where(
                CheckResult.check_run_id == check_run_id,
                CheckResult.kind == CheckKind.semantic,
                CheckResult.criterion_id.in_(targets),
            )
        )
    ).all()
    pending = [
        row
        for row in rows
        if row.outcome in _REPLACEABLE
        and "reference_strategy" not in (row.detail or {})
        and not (row.detail or {}).get("coverage_reason")
        and not (row.detail or {}).get("injection_suspected")
    ]
    if not pending:
        return 0
    # The check's own outcome is not the test: it reads "api_down" when only
    # its claim-support calls failed, though every catalogue lookup worked.
    # An entry whose own lookup failed is simply unidentified below.
    citation_detail = citation_result.detail if citation_result is not None else None
    problem = reference_list_problem(citation_detail)
    by_index = {c.order_index: c for c in citations}
    profile = (
        reference_profile(citation_detail["reference_metadata_coverage"], by_index, data)
        if problem is None
        else None
    )
    for row in pending:
        criterion, name = targets[row.criterion_id]
        if problem is not None:
            _escalate(row, problem, {"strategy": name, "grader": _grader_result(row)})
        elif name == INVENTORY:
            decide_inventory(row, criterion, profile, data)
        else:
            ranges = quality_from_ranges(criterion, profile, quality_ranges)
            decide_quality(row, criterion, profile, ranges, data)
        await write_audit_event(
            session,
            event_type="reference_criterion_decided",
            check_run_id=check_run_id,
            payload={
                "criterion_id": row.criterion_id,
                "outcome": row.outcome.value,
                "reference_strategy": (row.detail or {}).get("reference_strategy"),
            },
        )
    await session.commit()
    return len(pending)
