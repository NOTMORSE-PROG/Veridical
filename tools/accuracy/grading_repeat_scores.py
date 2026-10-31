"""Scores the stored output of `grading_repeats.py`: stability across repeats,
the support check and quote grounding. No database, no model, no network.

A unit is one criterion of one thesis. A repeat that was cut short (kept
under `stopped/`) is never scored, and a thesis missing from any repeat is
listed as incomplete rather than dropped silently.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.accuracy.core import rate_row
from tools.accuracy.lanes.grading import (
    LANE,
    NOT_FOUND,
    ON_ANCHOR_PAGE,
    OTHER_PAGE,
    Grade,
    classify_quote,
    stability,
    tally,
)

# A level was decided automatically only on these outcomes; anything else
# (escalated, unverifiable, not applicable) went to the instructor or did not
# apply.
DECIDED = {"passed", "failed"}


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


def score_repeats(folder: Path, pages_dir: Path, *, image_page_max_chars: int, targets: dict):
    setup = _load(folder / "setup.json")
    criteria = {c["id"]: c for c in setup["criteria"]}
    repeat_dirs = sorted(p for p in (folder / "repeats").glob("r*") if p.is_dir())
    grades: list[Grade] = []
    decisions, quotes = [], []
    pages_cache: dict[str, dict[int, str]] = {}
    for repeat_dir in repeat_dirs:
        number = int(repeat_dir.name[1:])
        for record_file in sorted(repeat_dir.glob("*.json")):
            record = _load(record_file)
            document = record["document"]
            for result in record["results"]:
                if result["kind"] != "semantic" or result["criterion_id"] not in criteria:
                    continue
                detail = result["detail"] or {}
                decided = result["outcome"] in DECIDED
                level = _ai_level(detail) if decided else None
                unit = (document, criteria[result["criterion_id"]]["text"])
                grades.append(Grade(unit, number, level, decided))
                decisions.append(
                    {
                        "repeat": number,
                        "document": document,
                        "criterion": unit[1],
                        "outcome": result["outcome"],
                        "level": level,
                        "support": (detail.get("support_check") or {}).get("status"),
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
    scales = {_scale(c["levels"]) for c in criteria.values() if c["levels"]}
    if len(scales) != 1:
        raise ValueError("criteria use different level scales; score them separately")
    (scale,) = scales
    summary = stability(grades, scale, repeats=len(repeat_dirs))
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
            extra={
                "target": targets.get("repeat_all_equal"),
                "fleiss_kappa": summary.fleiss_kappa,
                "gwet_ac1": summary.ac1,
                "level_prevalence": summary.prevalence,
                "decided_escalated_flips": [list(u) for u in summary.decided_flips],
                "incomplete_units": [list(u) for u in summary.incomplete_units],
            },
        ),
        rate_row(
            LANE,
            "pairs of repeats gave the same level",
            "repeat pair",
            summary.pairs_exact,
            summary.pairs,
            extra={"within_one_level": summary.pairs_within_one},
        ),
        rate_row(
            LANE,
            "automatic grades whose support check passed (repeats)",
            "criterion x document x repeat",
            len(supported),
            len(automatic),
            extra={
                "target": targets.get("support_passed"),
                "support_status": tally(automatic, lambda d: d["support"]),
            },
        ),
        rate_row(
            LANE,
            "quotes found on the cited page (repeats)",
            "quote",
            sum(q["where"] == ON_ANCHOR_PAGE for q in checkable),
            len(checkable),
            extra={
                "target": targets.get("quotes_on_cited_page"),
                "where": tally(quotes, lambda q: q["where"]),
            },
        ),
    ]
    details = {
        "repeats": [p.name for p in repeat_dirs],
        "stopped": sorted(
            p.relative_to(folder).as_posix() for p in folder.glob("repeats/r*/stopped/*.json")
        ),
        "outcomes": tally(decisions, lambda d: f"r{d['repeat']} {d['outcome']}"),
        "decisions": decisions,
        "quotes_needing_a_person": [q for q in quotes if q["where"] != ON_ANCHOR_PAGE],
    }
    return rows, details
