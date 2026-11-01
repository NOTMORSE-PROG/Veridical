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
  each level's own words imply a share of the list that is journal or
  conference papers or books ("most ... not peer-reviewed", "primarily
  peer-reviewed"). A level is decided only when exactly one level's share
  holds however the unconfirmed entries turn out; otherwise the instructor
  decides, told which levels fit. No model is asked.

Only a catalogue's confirmation may count against a student. An entry typed
from its citation text as a paper or book counts as major (which can only
help); every other unconfirmed entry is treated as either kind.

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
    confirmed_statuses: frozenset[str]
    web_cues: tuple[re.Pattern[str], ...]
    # Typing an unconfirmed entry from its own citation text, in order.
    text_types: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = ()
    web_scholarly_cues: tuple[re.Pattern[str], ...] = ()
    web_address: re.Pattern[str] | None = None
    # (pattern, min_share, max_share) a quality level's own words imply.
    quality_quantifiers: tuple[tuple[re.Pattern[str], float | None, float | None], ...] = ()
    # Every sentence an instructor reads from this step.
    wording: dict[str, str] = field(default_factory=dict)


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
        wording=dict(raw["wording"]),
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
    # Entry numbers no catalogue ruled out as major references: what an
    # instructor checks when the counts cannot settle a level.
    uncertain_entries: list[int] = field(default_factory=list)
    # Unidentified because a catalogue could not be reached, not because none
    # knew the entry: said so, since a later check may identify them.
    lookup_unavailable: int = 0

    @property
    def major(self) -> int:
        return self.peer_reviewed + self.book + self.major_from_text

    @property
    def major_from_text(self) -> int:
        return self.journal_or_conference_text + self.book_text

    @property
    def uncertain(self) -> int:
        """Entries that may or may not be major references: nothing a
        catalogue confirmed says they are not."""
        return self.web + self.repository_text + self.other_identified + self.unclassified

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
            "uncertain_entry_numbers": self.uncertain_entries,
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
        if kind in ("web", "repository_text", "other_identified", "unclassified"):
            profile.uncertain_entries.append(index + 1)
        if kind == "unclassified":
            profile.unclassified += 1
            profile.unclassified_entries.append(index + 1)
            profile.lookup_unavailable += record.get("status") == "api_down"
        elif kind == "other_identified":
            profile.other_identified += 1
        else:
            setattr(profile, kind, getattr(profile, kind) + 1)
            if kind.endswith("_text") or kind == "web":
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
    # A level whose major-reference wording was not read ("at least five",
    # "5-7") would otherwise fit on its internet limit alone; when any level
    # states a major count, every level must.
    kinds = [{kind for kind, _ in conds} for conds in result.values()]
    major_kinds = {"major_at_least", "major_more_than"}
    if any(k & major_kinds for k in kinds) and not all(k & major_kinds for k in kinds):
        return None
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
    """The count level at the worst and at the best reading of every entry a
    catalogue did not confirm. Only a catalogue's confirmation may count
    against a student: an entry typed from its citation text as a web page,
    a preprint or repository copy, a confirmed work of another kind, or an
    unidentified entry could each still be a major reference, so each one is
    an internet site at the worst and a major reference at the best. Entries
    typed as journal papers or books count as major either way, which can
    only help. Fewer major references and more internet sites never raise a
    level, so the truth lies between the two."""
    uncertain = profile.uncertain
    worst = count_level(conditions, profile.major, uncertain)
    best = count_level(conditions, profile.major + uncertain, 0)
    return worst, best


# --- reference quality: what the counts prove about the levels -----------------


def scholarly_share_bounds(profile: Profile) -> tuple[float, float]:
    """The lowest and highest share of the list that can be journal or
    conference papers or books. As for the counts, only a catalogue's
    confirmation counts against the list: every entry not typed as a paper or
    book could still be one."""
    low = profile.major / profile.entries
    high = (profile.major + profile.uncertain) / profile.entries
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
        "reason",
        "reason_codes",
        "proposed_level",
        "proposed_verdict",
        "support_check",
        "support_caution",
        "votes",
        "agreement",
        "decision_basis",
    )
    # A stored row's score is a Decimal, which a JSON column cannot hold.
    score = float(row.score) if row.score is not None else None
    return {
        "outcome": row.outcome.value,
        "score": score,
        **{key: detail[key] for key in keep if key in detail},
    }


def _escalate(row: CheckResult, reason: str, record: dict) -> None:
    detail = dict(row.detail or {})
    for key in ("verdict", "level", "reasoning"):
        if key in detail:
            detail[f"proposed_{key}"] = detail.pop(key)
    detail.pop("support_caution", None)
    # The grader's own reason, codes and passes stay in the kept grader
    # result; on the row they would describe a different decision from this
    # one, and passes that agreed would read as a grade.
    for key in ("reason_codes", "votes", "agreement"):
        detail.pop(key, None)
    detail["reason"] = reason
    detail["reference_strategy"] = record
    row.outcome, row.score, row.detail = ResultOutcome.escalated, None, detail


def _decide(
    row: CheckResult,
    criterion: Any,
    ordinal: int,
    reasoning: str,
    record: dict,
    *,
    keep_caution: bool = False,
) -> None:
    name = next(level["name"] for level in _levels(criterion) if int(level["level"]) == ordinal)
    outcome, score, level = outcome_and_score(criterion, name)
    detail = dict(row.detail or {})
    for key in ("proposed_verdict", "proposed_level", "proposed_reasoning", "reason"):
        detail.pop(key, None)
    detail.pop("support_withheld", None)
    detail.pop("reason_codes", None)
    if not keep_caution:
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
    verdict = (row.detail or {}).get("verdict")
    if row.outcome not in (ResultOutcome.passed, ResultOutcome.failed) or not verdict:
        return None
    level = match_level(criterion, verdict)
    return level.ordinal if level else None


def _numbers(values: list[int]) -> str:
    """Entry numbers as short runs: 3, 5 to 9, 12."""
    runs: list[list[int]] = []
    for value in sorted(values):
        if runs and value == runs[-1][-1] + 1:
            runs[-1].append(value)
        else:
            runs.append([value])
    return ", ".join(str(run[0]) if len(run) == 1 else f"{run[0]} to {run[-1]}" for run in runs)


def _counts_sentence(profile: Profile, data: ReferenceData) -> str:
    words = data.wording
    from_text = (
        words["counts_from_text"].format(n=profile.major_from_text)
        if profile.major_from_text
        else ""
    )
    repositories = (
        words["counts_repositories"].format(n=profile.repository_text)
        if profile.repository_text
        else ""
    )
    other = (
        words["counts_other"].format(n=profile.other_identified) if profile.other_identified else ""
    )
    sentence = words["counts"].format(
        entries=profile.entries,
        major=profile.major,
        from_text=from_text,
        web=profile.web,
        repositories=repositories,
        other=other,
        unclassified=profile.unclassified,
    )
    if profile.lookup_unavailable:
        sentence += " " + words["counts_lookup_unavailable"].format(n=profile.lookup_unavailable)
    return sentence


def _web_note(conditions: dict[int, list[tuple[str, int]]], profile: Profile, data) -> str:
    """Information only: entries that look like web pages, beyond what every
    level allows. They never decide a level (they may still be journal
    articles retrieved online), so this only tells the instructor where to
    look."""
    limits = [n for conds in conditions.values() for kind, n in conds if kind == "internet_at_most"]
    if limits and profile.web > max(limits):
        return " " + data.wording["web_note"].format(web=profile.web, limit=max(limits))
    return ""


def _entries_to_check(profile: Profile, data: ReferenceData) -> str:
    if not profile.uncertain_entries:
        return ""
    return " " + data.wording["entries_to_check"].format(
        numbers=_numbers(profile.uncertain_entries)
    )


def _share_sentence(profile: Profile, data: ReferenceData) -> str:
    low = profile.major
    high = profile.major + profile.uncertain
    return data.wording["quality_share"].format(low=low, high=high, entries=profile.entries)


def decide_inventory(
    row: CheckResult, criterion: Any, profile: Profile, data: ReferenceData
) -> None:
    words = data.wording
    conditions = count_conditions(criterion, data)
    record = {"strategy": INVENTORY, "profile": profile.as_detail(), "grader": _grader_result(row)}
    if conditions is None:
        record["skipped"] = "the format's levels do not all state counts VERIDICAL can read"
        row.detail = {**(row.detail or {}), "reference_strategy": record}
        return
    record["count_conditions"] = {str(k): v for k, v in conditions.items()}
    worst, best = inventory_bounds(conditions, profile)
    record["count_level_bounds"] = [worst, best]
    counts = _counts_sentence(profile, data) + _web_note(conditions, profile, data)
    names = {int(level["level"]): level["name"] for level in _levels(criterion)}
    if best is None:
        _escalate(row, f"{counts} {words['inventory_short']}", record)
        return
    if worst != best:
        low = names[worst] if worst is not None else words["no_level"]
        reason = words["inventory_open"].format(low=low, high=names[best])
        _escalate(row, f"{counts} {reason}{_entries_to_check(profile, data)}", record)
        return
    grader = _grader_ordinal(criterion, row)
    if grader is None:
        # The grader did not settle the format part; the counts alone are not
        # the whole descriptor, so the instructor decides.
        _escalate(row, f"{counts} {words['inventory_ungraded']}", record)
        return
    final = min(grader, worst)
    record["decision_basis"] = "reference_counts"
    if grader > worst:
        reasoning = f"{counts} {words['inventory_lowered']}"
    elif grader == worst:
        reasoning = f"{counts} {words['inventory_same']}"
    else:
        reasoning = f"{counts} {words['inventory_formatting']}"
    # Where the formatting grade sets the level, its own evidence caution
    # still applies to it.
    _decide(row, criterion, final, reasoning, record, keep_caution=final == grader)


def decide_quality(
    row: CheckResult,
    criterion: Any,
    profile: Profile,
    data: ReferenceData,
) -> None:
    """Decide a quality level only where the counts prove it: the one level
    whose own words ("most ... not peer-reviewed", "primarily peer-reviewed")
    hold however the entries a catalogue did not confirm turn out. Where the
    counts fit more than one level, what separates them ("a few are
    questionable", "approved sources") is not something counts show, so the
    instructor decides, told which levels fit."""
    words = data.wording
    record = {"strategy": QUALITY, "profile": profile.as_detail(), "grader": _grader_result(row)}
    if not profile.entries:
        _escalate(row, words["no_entries"], record)
        return
    counts = f"{_counts_sentence(profile, data)} {_share_sentence(profile, data)}"
    checks = {
        int(level["level"]): quality_level_fits(criterion, int(level["level"]), profile, data)
        for level in _levels(criterion)
    }
    record["level_checks"] = {str(ordinal): check for ordinal, check in checks.items()}
    fitting = [ordinal for ordinal, check in checks.items() if check["fits"]]
    names = {int(level["level"]): level["name"] for level in _levels(criterion)}
    if not fitting:
        _escalate(row, f"{counts} {words['quality_none']}", record)
        return
    if len(fitting) > 1:
        levels = " and ".join(names[ordinal] for ordinal in fitting)
        _escalate(row, f"{counts} {words['quality_several'].format(levels=levels)}", record)
        return
    record["decision_basis"] = "reference_profile"
    _decide(row, criterion, fitting[0], f"{counts} {words['quality_decided']}", record)


def reference_list_problem(citation_detail: dict | None, data: ReferenceData) -> str | None:
    """Why the list cannot be counted at all, in the instructor's words, or
    None. A list the citation check could not read in full is never counted:
    entries on an unread page, or past a boundary it could not confirm, would
    be missing from the counts and could lower a grade."""
    words = data.wording
    if citation_detail is None:
        return words["problem_not_finished"]
    if citation_detail.get("reference_selection_issues"):
        return words["problem_selection"]
    if not citation_detail.get("reference_metadata_coverage"):
        return words["problem_empty"]
    if citation_detail.get("unread_pages"):
        pages = ", ".join(str(page) for page in citation_detail["unread_pages"])
        return words["problem_unread_pages"].format(pages=pages)
    if citation_detail.get("reference_membership_status") not in (None, "complete"):
        return words["problem_boundary"]
    if citation_detail.get("coverage_reason"):
        return words["problem_partial"]
    return None


async def apply_reference_strategies(
    session: AsyncSession,
    check_run_id: int,
    criteria: list[Criterion],
    citation_result: CheckResult | None,
    citations: list[Any],
    data: ReferenceData,
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
    problem = reference_list_problem(citation_detail, data)
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
            decide_quality(row, criterion, profile, data)
        skipped = "skipped" in ((row.detail or {}).get("reference_strategy") or {})
        await write_audit_event(
            session,
            event_type="reference_criterion_skipped" if skipped else "reference_criterion_decided",
            check_run_id=check_run_id,
            payload={
                "criterion_id": row.criterion_id,
                "outcome": row.outcome.value,
                "reference_strategy": (row.detail or {}).get("reference_strategy"),
            },
        )
    await session.commit()
    return len(pending)
