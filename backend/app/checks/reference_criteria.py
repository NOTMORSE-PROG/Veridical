"""Criteria a format states about the reference list itself (V-098 a, d; F3, F5).

A bibliography's size and a source list's quality are facts about the whole
list, so a single quoted passage can never establish them. That is why the
quote-based support check rejected these grades by construction. After the
citation check (F5) has looked every entry up, this step judges them from the
list instead:

- reference inventory (the format counts major references and internet
  sites): the counts are worked out from F5's lookups, and the format's own
  descriptors say which counts each level allows. The grader's level still
  speaks to the format errors; the counts can only lower it. Entries no
  catalogue identified could be either kind, so the level is decided only
  when it is the same however they turn out.
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
from app.errors import ApiDownError, QuotaExhaustedError
from app.llm.base import LLMClient, LLMError
from app.models.enums import CheckKind, ResultOutcome
from app.models.rubric import Criterion
from app.models.run import CheckResult

INVENTORY = "reference_inventory"
QUALITY = "reference_quality"
QUALITY_PROMPT_TYPE = "reference_quality"
QUALITY_PROMPT_VERSION = "reference_quality_v1"
_PROMPT = Path(__file__).parent / "prompts" / "reference_quality_v1.txt"
CANNOT_DETERMINE = "cannot_determine"

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
    quality_readings: int


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
        quality_readings=int(raw["quality_readings"]),
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
    """Every entry of the list in one class. `unclassified` entries are ones
    no catalogue identified and whose own text does not say what they are."""

    entries: int = 0
    peer_reviewed: int = 0
    book: int = 0
    other_identified: int = 0
    web: int = 0
    unclassified: int = 0
    unclassified_entries: list[int] = field(default_factory=list)
    # Unidentified because a catalogue could not be reached, not because none
    # knew the entry: said so, since a later check may identify them.
    lookup_unavailable: int = 0

    @property
    def major(self) -> int:
        return self.peer_reviewed + self.book

    def as_detail(self) -> dict:
        return {
            "entries": self.entries,
            "journal_or_conference_papers": self.peer_reviewed,
            "books": self.book,
            "other_identified_works": self.other_identified,
            "web_sources": self.web,
            "unidentified": self.unclassified,
            "unidentified_entry_numbers": self.unclassified_entries,
            "unidentified_because_a_catalogue_was_unreachable": self.lookup_unavailable,
        }


def classify_entry(record: dict, raw_text: str, url: str | None, data: ReferenceData) -> str:
    if record.get("status") in data.confirmed_statuses:
        tags = set(record.get("work_types") or [])
        for name in ("peer_reviewed", "book"):
            if tags & data.type_classes.get(name, frozenset()):
                return name
        if tags:
            return "other_identified"
    if url and any(cue.search(raw_text or "") for cue in data.web_cues):
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


# --- reference quality: two separate readings of the profile ------------------


def quality_from_ranges(
    criterion: Any, profile: Profile, ranges: dict | None
) -> tuple[int | None, str] | None:
    """A level from share ranges the format itself sets, or None when it sets
    none. Ranges give, per level number, [low, high] for the share of the
    list that is journal or conference papers. Unidentified entries could be
    either, so a level is decided only when both extremes fall in its range."""
    if not ranges or not profile.entries:
        return None
    low_share = profile.peer_reviewed / profile.entries
    high_share = (profile.peer_reviewed + profile.unclassified) / profile.entries

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


def _quality_prompt(criterion: Any, profile: Profile, reading: int, total: int) -> str:
    task = {
        "reading": f"{reading} of {total}",
        "criterion": criterion.text,
        "levels": [
            {"name": level["name"], "descriptor": level["descriptor"]}
            for level in _levels(criterion)
        ],
        "reference_list_profile": profile.as_detail(),
    }
    # One replacement keeps profile strings from being read as template slots.
    return _PROMPT.read_text(encoding="utf-8").replace(
        "{task_json}", json.dumps(task, ensure_ascii=False)
    )


async def quality_readings(
    llm: LLMClient, criterion: Any, profile: Profile, data: ReferenceData, check_run_id: int
) -> list[dict]:
    """Separate, numbered readings, so no answer can be served from another's
    cache entry. A reading that fails or cannot be read is kept as such."""
    readings = []
    total = data.quality_readings
    for reading in range(1, total + 1):
        try:
            response = await llm.complete(
                QUALITY_PROMPT_TYPE,
                _quality_prompt(criterion, profile, reading, total),
                prompt_version=QUALITY_PROMPT_VERSION,
                check_run_id=check_run_id,
                reference_reading=reading,
            )
        except (ApiDownError, QuotaExhaustedError, LLMError) as exc:
            readings.append({"reading": reading, "level": None, "error": type(exc).__name__})
            continue
        level = response.get("level") if isinstance(response, dict) else None
        reason = response.get("reason") if isinstance(response, dict) else None
        readings.append(
            {
                "reading": reading,
                "level": level if isinstance(level, str) else None,
                "reason": reason if isinstance(reason, str) else None,
            }
        )
    return readings


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
    sentence = (
        f"The reference list has {profile.entries} entries: "
        f"{profile.major} major references (journal or conference papers and books), "
        f"{profile.web} web sources, {profile.other_identified} other identified works "
        f"and {profile.unclassified} entries no catalogue identified."
    )
    if profile.lookup_unavailable:
        sentence += (
            f" {profile.lookup_unavailable} of those could not be looked up because a "
            "catalogue was unreachable; a later check may identify them."
        )
    return sentence


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
            f"{counts} No level of this format allows these counts, so VERIDICAL did not "
            "choose one. Please check the reference list.",
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


def decide_quality_from(
    row: CheckResult,
    criterion: Any,
    profile: Profile,
    readings: list[dict] | None,
    ranges_result: tuple[int | None, str] | None,
) -> None:
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
    record["readings"] = readings or []
    names = {level["name"] for level in _levels(criterion)}
    chosen = [reading.get("level") for reading in readings or []]
    if any(r.get("error") for r in readings or []):
        _escalate(
            row,
            f"{counts} The reading of this profile could not be completed, so VERIDICAL did "
            "not choose a level.",
            record,
        )
        return
    if not chosen or any(level not in names for level in chosen):
        _escalate(
            row,
            f"{counts} The unidentified entries could change which level fits, so VERIDICAL "
            "did not choose one. Please check the reference list.",
            record,
        )
        return
    if len(set(chosen)) != 1:
        _escalate(
            row,
            f"{counts} Two separate readings of this profile chose different levels "
            f"({', '.join(chosen)}), so VERIDICAL did not choose one.",
            record,
        )
        return
    ordinal = next(int(lv["level"]) for lv in _levels(criterion) if lv["name"] == chosen[0])
    record["decision_basis"] = "reference_profile"
    reason = next((r.get("reason") for r in readings if r.get("reason")), None)
    _decide(
        row,
        criterion,
        ordinal,
        f"{counts} Both readings of this profile chose this level."
        + (f" {reason}" if reason else ""),
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
    llm: LLMClient,
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
            readings = (
                None
                if ranges is not None
                else await quality_readings(llm, criterion, profile, data, check_run_id)
            )
            decide_quality_from(row, criterion, profile, readings, ranges)
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
