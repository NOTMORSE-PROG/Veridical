"""Grading proxies on stored production check runs (read-only inputs).

Reads JSON already exported from production in read-only transactions:
  runs.json         check_run rows (id, manuscript, rubric, band, content hash)
  results.json      check_result rows with their stored detail
  llm_events.json   audit_log counts per run, event type and prompt type
and independent page texts (one JSON per document, `pages` keyed by page
number) made with a PDF tool other than the product's own extractor.

`docs.json` maps a manuscript content hash to its pages file stem. Run it
through the shared runner (`--lane grading`). No database, no model and no
network.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from tools.accuracy.core import rate_row
from tools.accuracy.lanes.grading import (
    DECIDED,
    IMAGE_PAGE,
    LABEL,
    LANE,
    NOT_FOUND,
    ON_ANCHOR_PAGE,
    OTHER_PAGE,
    classify_quote,
    tally,
)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _detail(row: dict) -> dict:
    value = row["detail"]
    return json.loads(value) if isinstance(value, str) else (value or {})


def _level(detail: dict) -> str | None:
    return (detail.get("level") or {}).get("name") or detail.get("verdict")


def _both_voted(votes: list) -> bool:
    return len(votes) >= 2 and all(vote is not None for vote in votes)


def _grading_source(counter: Counter) -> str:
    fresh = counter["llm_call:semantic_grading"]
    cached = counter["llm_cache_hit:semantic_grading"]
    if fresh and cached:
        return "mixed"
    return "cache" if cached else "fresh" if fresh else "none"


def _rerun_agreement(decisions: list[dict], runs: dict, first_run: dict) -> list[dict]:
    """Each rerun's levels against the same document's first run, labelled by
    where the rerun's grading came from. Agreement on a cache replay is true
    by construction and is shown only so nobody mistakes it for stability."""
    by_run = defaultdict(dict)
    for d in decisions:
        by_run[d["run"]][d["criterion"]] = d
    rows = []
    for run_id, run in sorted(runs.items()):
        first = first_run[run["content_hash"]]
        if run_id == first or run_id not in by_run:
            continue
        shared = sorted(set(by_run[run_id]) & set(by_run[first]))
        same = sum(by_run[run_id][c]["level"] == by_run[first][c]["level"] for c in shared)
        rows.append(
            {
                "run": run_id,
                "first_run": first,
                "criteria": len(shared),
                "same_level": same,
                "grading": next(iter(by_run[run_id].values()))["grading"],
            }
        )
    return rows


def analyse(
    live: Path,
    pages_dir: Path,
    docs: dict[str, str],
    *,
    image_page_max_chars: int,
    targets: dict[str, float],
) -> tuple[list, dict]:
    runs = {run["id"]: run for run in _load(live / "runs.json")}
    events = _load(live / "llm_events.json")
    calls = defaultdict(Counter)
    for event in events:
        calls[event["check_run_id"]][f"{event['event_type']}:{event['prompt_type']}"] += event["n"]
    pages = {}
    decisions, quotes = [], []
    for result in _load(live / "results.json"):
        run = runs[result["check_run_id"]]
        detail = _detail(result)
        support = (detail.get("support_check") or {}).get("status")
        votes = detail.get("votes") or []
        decisions.append(
            {
                "run": run["id"],
                "document": docs.get(run["content_hash"], run["original_filename"]),
                "criterion": result["criterion"],
                "outcome": result["outcome"],
                # The grader's own level. An instructor's change of grade is kept in
                # `resolution` with the AI result in `original_result`; it is the
                # instructor's judgment, not the grader's, so it is reported apart.
                "level": _level(detail.get("original_result") or detail),
                "instructor_level": _level(detail) if detail.get("resolution") else None,
                "votes": votes,
                "passes_agree": _both_voted(votes) and len(set(votes)) == 1,
                "support": support,
                "context": detail.get("context_label"),
                # A grading replay is a run whose grading came wholly from the
                # response cache; its agreement with the first run is true by
                # construction. A run with both fresh and cached grading calls is
                # mixed and reported as such.
                "grading": _grading_source(calls[run["id"]]),
                "cache_replay": _grading_source(calls[run["id"]]) == "cache",
                "instructor_resolution": bool(detail.get("resolution")),
            }
        )
        stem = docs.get(run["content_hash"])
        if stem is None:
            continue
        if stem not in pages:
            stored = _load(pages_dir / f"{stem}.json")
            pages[stem] = {int(k): v for k, v in stored["pages"].items()}
        for evidence in detail.get("evidence") or []:
            quotes.append(
                {
                    "run": run["id"],
                    "document": stem,
                    "criterion": result["criterion"],
                    "anchor": evidence.get("anchor"),
                    "quote": evidence.get("quote", ""),
                    "where": classify_quote(
                        evidence.get("quote", ""),
                        evidence.get("anchor"),
                        pages[stem],
                        image_page_max_chars=image_page_max_chars,
                    ),
                }
            )
    # The population is each document's first check: later runs of the same
    # file are reruns, scored below against that first run.
    first_run = {}
    for run in sorted(runs.values(), key=lambda r: r["id"]):
        first_run.setdefault(run["content_hash"], run["id"])
    primary = set(first_run.values()) & {d["run"] for d in decisions}
    first = [d for d in decisions if d["run"] in primary]
    rerun_rows = _rerun_agreement(decisions, runs, first_run)
    first_quotes = [q for q in quotes if q["run"] in {d["run"] for d in first}]
    automatic = [d for d in first if d["outcome"] in DECIDED]
    supported = [d for d in automatic if d["support"] == "supported"]
    both_votes = [d for d in first if _both_voted(d["votes"])]
    checkable = [q for q in first_quotes if q["where"] in (ON_ANCHOR_PAGE, OTHER_PAGE, NOT_FOUND)]
    rows = [
        rate_row(
            LANE,
            "automatic grades whose support check passed",
            "criterion x document",
            len(supported),
            len(automatic),
            note="first run of each document; automatic = outcome passed",
            extra={
                "target": targets.get("support_passed"),
                "support_status": tally(automatic, lambda d: d["support"]),
                "by_context": tally(automatic, lambda d: f"{d['context']} / {d['support']}"),
            },
        ),
        rate_row(
            LANE,
            "pass 1 and pass 2 gave the same level",
            "criterion x document",
            sum(d["passes_agree"] for d in both_votes),
            len(both_votes),
            note="two differently-stanced passes in one run; not repeat stability",
        ),
        rate_row(
            LANE,
            "quotes found on the cited page",
            "quote",
            sum(q["where"] == ON_ANCHOR_PAGE for q in checkable),
            len(checkable),
            note="independent extraction; image pages excluded and listed",
            extra={
                "target": targets.get("quotes_on_cited_page"),
                "where": tally(first_quotes, lambda q: q["where"]),
            },
        ),
        rate_row(
            LANE,
            "quotes found anywhere in the document",
            "quote",
            sum(q["where"] in (ON_ANCHOR_PAGE, OTHER_PAGE) for q in checkable),
            len(checkable),
            extra={"target": targets.get("quotes_in_document")},
        ),
    ]
    replays = sorted({d["run"] for d in decisions if d["cache_replay"]})
    sources = {d["run"]: d["grading"] for d in decisions}
    return rows, {
        "label": LABEL,
        "runs": sorted(runs),
        "first_runs": sorted({d["run"] for d in first}),
        "cache_replay_runs": replays,
        "primary_runs": sorted(primary),
        "reruns": rerun_rows,
        "grading_source_by_run": dict(sorted(sources.items())),
        "calls_by_run": {run: dict(counter) for run, counter in sorted(calls.items())},
        "outcomes": tally(first, lambda d: d["outcome"]),
        "levels": tally(first, lambda d: d["level"]),
        "instructor_resolutions": sum(d["instructor_resolution"] for d in decisions),
        "quotes_needing_a_person": [
            q for q in first_quotes if q["where"] in (IMAGE_PAGE, NOT_FOUND, OTHER_PAGE)
        ],
        "decisions": decisions,
        "quotes": quotes,
    }
