"""Scores the stored output of `grading_repeats.py`: stability across repeats,
the support check and quote grounding. No database, no model, no network.

A unit is one criterion of one thesis. A repeat that was cut short (kept
under `stopped/`) is never scored, and a thesis missing from any repeat is
listed as incomplete rather than dropped silently.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from tools.accuracy.core import rate_row
from tools.accuracy.lanes.grading import (
    DECIDED,
    LANE,
    NOT_FOUND,
    ON_ANCHOR_PAGE,
    OTHER_PAGE,
    Grade,
    classify_quote,
    stability,
    tally,
)

from app.checks.support import locatable_subclaim


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _scale(levels: list[dict] | None) -> tuple[str, ...]:
    ordered = sorted(
        levels or [], key=lambda level: (level.get("points") is None, level.get("points"))
    )
    return tuple(level["name"] for level in ordered)


def _ai_level(detail: dict) -> str | None:
    source = detail.get("original_result") or detail
    return (source.get("level") or {}).get("name") or source.get("verdict")


def _proposed_level(detail: dict) -> str | None:
    """The level the grader put forward, kept or escalated."""
    source = detail.get("original_result") or detail
    return (
        (source.get("level") or source.get("proposed_level") or {}).get("name")
        or source.get("verdict")
        or source.get("proposed_verdict")
    )


SINGLE_PASSAGE = "single passage"
LOCATABLE = "locatable sub-claim"
WHOLE_DOCUMENT = "whole-document or external"


def strategy(criterion: dict, detail: dict) -> str:
    """Which kind of evidence the claimed level needs, decided by the
    product's own rules: a sub-claim one passage shows or not (V-087), an
    assessment no single quote can confirm (the support check's scope
    guard), or neither."""
    source = detail.get("original_result") or detail
    claimed = {**source, "verdict": source.get("verdict") or source.get("proposed_verdict")}
    shape = SimpleNamespace(text=criterion["text"], levels=criterion["levels"])
    if claimed["verdict"] and locatable_subclaim(shape, claimed):
        return LOCATABLE
    if (source.get("support_check") or {}).get("scope_guard"):
        return WHOLE_DOCUMENT
    return SINGLE_PASSAGE


def decision_basis(detail: dict) -> str:
    """How the vote settled the level. The product records a basis only for
    its fallback rules (lower of two, single verified pass); otherwise the
    votes show whether the two passes agreed or a tie-break decided."""
    source = detail.get("original_result") or detail
    if source.get("decision_basis"):
        return source["decision_basis"]
    votes = [vote for vote in source.get("votes") or [] if vote]
    if len(votes) == 2 and votes[0] == votes[1]:
        return "passes agreed"
    if len(votes) >= 3:
        return "tie-break majority"
    return "unrecorded"


GRADING_PROMPT = "semantic_grading"


def graded_by_the_model(record: dict) -> bool:
    return any(
        event["event_type"] == "llm_call"
        and (event.get("payload") or {}).get("prompt_type") == GRADING_PROMPT
        for event in record.get("llm_events") or []
    )


def _ungraded_reason(record: dict) -> str:
    reasons = {
        (r.get("detail") or {}).get("coverage_reason") or r["outcome"]
        for r in record["results"]
        if r["kind"] == "semantic"
    }
    return ", ".join(sorted(str(reason) for reason in reasons)) or "no criterion result"


def common_scale(criteria) -> tuple[str, ...]:
    """The one level scale every levelled criterion shares. Run it straight
    after setup: a format with mixed scales has to be scored per scale, and
    that is better known before any repeat is paid for."""
    scales = {_scale(c["levels"]) for c in criteria if c["levels"]}
    if len(scales) != 1:
        raise ValueError(f"criteria use {len(scales)} level scales; score them separately")
    (scale,) = scales
    return scale


def score_repeats(folder: Path, pages_dir: Path, *, image_page_max_chars: int, targets: dict):
    setup = _load(folder / "setup.json")
    criteria = {c["id"]: c for c in setup["criteria"]}
    repeat_dirs = sorted(p for p in (folder / "repeats").glob("r*") if p.is_dir())
    grades: list[Grade] = []
    decisions, quotes = [], []
    not_graded: dict[str, set[str]] = {}
    pages_cache: dict[str, dict[int, str]] = {}
    for repeat_dir in repeat_dirs:
        number = int(repeat_dir.name[1:])
        for record_file in sorted(repeat_dir.glob("*.json")):
            record = _load(record_file)
            document = record["document"]
            if not graded_by_the_model(record):
                # Every criterion was settled before any grading call (a
                # source-reading limitation, say). That is the product
                # failing closed, not the grader agreeing with itself, so it
                # is listed and kept out of every grader rate.
                not_graded.setdefault(document, set()).add(_ungraded_reason(record))
                continue
            for result in record["results"]:
                if result["kind"] != "semantic" or result["criterion_id"] not in criteria:
                    continue
                detail = result["detail"] or {}
                decided = result["outcome"] in DECIDED
                level = _ai_level(detail) if decided else None
                criterion = criteria[result["criterion_id"]]
                unit = (document, criterion["text"])
                grades.append(Grade(unit, number, level, decided, _proposed_level(detail)))
                decisions.append(
                    {
                        "repeat": number,
                        "document": document,
                        "criterion": unit[1],
                        "outcome": result["outcome"],
                        "level": level,
                        "support": (detail.get("support_check") or {}).get("status"),
                        "strategy": strategy(criterion, detail),
                        "decision_basis": decision_basis(detail),
                    }
                )
                if document not in pages_cache:
                    stored = _load(pages_dir / f"{document}.json")
                    pages_cache[document] = {int(k): v for k, v in stored["pages"].items()}
                for evidence in detail.get("evidence") or []:
                    quotes.append(
                        {
                            "repeat": number,
                            "document": document,
                            "criterion": unit[1],
                            "anchor": evidence.get("anchor"),
                            "quote": evidence.get("quote", ""),
                            "where": classify_quote(
                                evidence.get("quote", ""),
                                evidence.get("anchor"),
                                pages_cache[document],
                                image_page_max_chars=image_page_max_chars,
                            ),
                        }
                    )
    scale = common_scale(criteria.values())
    summary = stability(grades, scale, repeats=len(repeat_dirs))
    # Units are criteria of a handful of theses, so the theses are the
    # independent clusters D-016's floor applies to.
    clusters = len({d["document"] for d in decisions})
    automatic = [d for d in decisions if d["outcome"] in DECIDED]
    supported = [d for d in automatic if d["support"] == "supported"]
    checkable = [q for q in quotes if q["where"] in (ON_ANCHOR_PAGE, OTHER_PAGE, NOT_FOUND)]
    rows = [
        rate_row(
            LANE,
            "every repeat gave the same level",
            "criterion x document",
            summary.all_equal,
            summary.units,
            note=f"{len(repeat_dirs)} cache-bypassed repeats; units every repeat decided",
            clusters=clusters,
            extra={
                "target": targets.get("repeat_all_equal"),
                "fleiss_kappa": summary.fleiss_kappa,
                "gwet_ac1": summary.ac1,
                "level_prevalence": summary.prevalence,
                "specific_agreement_by_level": summary.specific_agreement,
                "pair_confusion": summary.confusion,
                "decided_escalated_flips": [list(u) for u in summary.decided_flips],
                "off_scale_units": [list(u) for u in summary.off_scale_units],
                "incomplete_units": [list(u) for u in summary.incomplete_units],
            },
        ),
        rate_row(
            LANE,
            "every repeat gave the same result (an escalation flip counts as a change)",
            "criterion x document",
            summary.strict_all_equal,
            summary.strict_units,
            note="The result an instructor sees: the level, or escalated.",
            clusters=clusters,
        ),
        rate_row(
            LANE,
            "every repeat proposed the same level (kept or escalated)",
            "criterion x document",
            summary.proposed_all_equal,
            summary.proposed_units,
            note="The grader's own proposal, before the support gate.",
            clusters=clusters,
        ),
        rate_row(
            LANE,
            "pairs of repeats gave the same level",
            "repeat pair",
            summary.pairs_exact,
            summary.pairs,
            clusters=clusters,
            extra={"within_one_level": summary.pairs_within_one},
        ),
        rate_row(
            LANE,
            "automatic grades whose support check passed (repeats)",
            "criterion x document x repeat",
            len(supported),
            len(automatic),
            clusters=clusters,
            extra={
                "target": targets.get("support_passed"),
                "support_status": tally(automatic, lambda d: d["support"]),
                "decision_basis": tally(automatic, lambda d: d["decision_basis"]),
            },
        ),
    ]
    for name in (LOCATABLE, WHOLE_DOCUMENT, SINGLE_PASSAGE):
        group = [d for d in automatic if d["strategy"] == name]
        rows.append(
            rate_row(
                LANE,
                f"automatic grades whose support check passed: {name}",
                "criterion x document x repeat",
                sum(d["support"] == "supported" for d in group),
                len(group),
                clusters=len({d["document"] for d in group}),
                extra={"criteria": tally(group, lambda d: d["criterion"])},
            )
        )
    rows.append(
        rate_row(
            LANE,
            "quotes found on the cited page (repeats)",
            "quote",
            sum(q["where"] == ON_ANCHOR_PAGE for q in checkable),
            len(checkable),
            clusters=clusters,
            extra={
                "target": targets.get("quotes_on_cited_page"),
                "where": tally(quotes, lambda q: q["where"]),
            },
        )
    )
    details = {
        "repeats": [p.name for p in repeat_dirs],
        "stopped": sorted(
            p.relative_to(folder).as_posix() for p in folder.glob("repeats/r*/stopped/*.json")
        ),
        "not_graded_by_the_model": {doc: sorted(why) for doc, why in sorted(not_graded.items())},
        "outcomes": tally(decisions, lambda d: f"r{d['repeat']} {d['outcome']}"),
        "decisions": decisions,
        "quotes_needing_a_person": [q for q in quotes if q["where"] != ON_ANCHOR_PAGE],
    }
    return rows, details
