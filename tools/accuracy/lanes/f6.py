"""F6 statistical forensics on planted known-answer cases.

Each planted copy of a host carries at most three plants, five or more pages
apart, and is compared with the unplanted host (its twin). Findings on the
copy that the twin does not have are the effect of the plants. Error plants
should be found; control plants are correct and must stay silent.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict

from scipy import stats as scipy_stats
from tools.accuracy.core import (
    Context,
    MetricRow,
    body_pages,
    load_extraction,
    rate_row,
    real_precision_rows,
    spaced_pages,
    split_for,
)

from app.checks.forensics.grim import grim_check
from app.checks.forensics.service import _compute_forensics_findings
from app.ingest.schemas import TableBlock, TextBlock

LANE = "f6"
SCOPE = (
    "Known-answer cases planted into stored extractions of the permitted theses: "
    "tests the checks, not PDF extraction. Not instructor-labelled."
)
ERROR_TYPES = ("p_nongross", "p_gross", "grim_impossible", "percent_sum_off")
CONTROL_TYPES = ("p_correct", "grim_possible", "percent_sum_ok")
PER_TYPE = 20
PLANTS_PER_COPY = 3
PAGE_GAP = 5
FRAMES = (
    "An independent-samples comparison of the two groups showed a difference, {stat}, {p}.",
    "The respondents' ratings differed between the pilot and final versions, {stat}, {p}.",
    "Testing the hypothesis on the survey responses gave {stat}, {p}.",
    "The difference in task completion time was examined, {stat}, {p}.",
)


def _p_text(p: float) -> str:
    return "p < .001" if p < 0.001 else f"p = {p:.3f}".replace("0.", ".", 1)


def _test(rng: random.Random, low: float, high: float) -> tuple[str, float]:
    """A test statistic whose two-tailed p lies in [low, high], in APA form."""
    while True:
        kind, df = rng.choice(("t", "F", "r")), rng.randint(20, 180)
        if kind == "t":
            value = round(rng.uniform(0.3, 4.0), 2)
            p, text = 2 * scipy_stats.t.sf(value, df), f"t({df}) = {value:.2f}"
        elif kind == "F":
            value = round(rng.uniform(0.1, 16.0), 2)
            p, text = scipy_stats.f.sf(value, 1, df), f"F(1, {df}) = {value:.2f}"
        else:
            value = round(rng.uniform(0.02, 0.6), 2)
            t = value * math.sqrt(df / (1 - value**2))
            p, text = 2 * scipy_stats.t.sf(t, df), f"r({df}) = {str(value)[1:]}"
        if low <= p <= high:
            return text, p


def _inferential(rng, plant_type):
    if plant_type == "p_correct":
        text, p = _test(rng, 0.002, 0.6)
        reported = _p_text(p)
    elif plant_type == "p_nongross":
        if rng.random() < 0.5:
            text, p = _test(rng, 0.012, 0.045)
            reported = _p_text(max(0.002, p / 3))
        else:
            text, p = _test(rng, 0.12, 0.5)
            reported = _p_text(min(0.95, p * 1.6))
    else:  # p_gross: the reported p lands on the other side of .05
        if rng.random() < 0.5:
            text, p = _test(rng, 0.07, 0.35)
            reported = "p = .030"
        else:
            text, p = _test(rng, 0.004, 0.03)
            reported = "p = .090"
    return rng.choice(FRAMES).format(stat=text, p=reported), text


def _grim_table(rng, plant_type):
    while True:
        n = rng.randint(11, 60)
        if plant_type == "grim_possible":
            mean = round(rng.randint(n * 2, n * 4) / n, 2)
        else:
            mean = round(rng.uniform(2.0, 4.6), 2)
        if grim_check(n, mean) == (plant_type == "grim_possible"):
            break
    filler_n = rng.randint(11, 60)
    filler = round(rng.randint(filler_n * 2, filler_n * 4) / filler_n, 2)
    label = rng.choice(("Students", "Faculty", "Staff", "Customers", "Users"))
    rows = [
        ["Respondents", "N", "Mean"],
        [label, str(n), f"{mean:.2f}"],
        ["Others", str(filler_n), f"{filler:.2f}"],
    ]
    return rows, f"M={mean:.2f}"


def _percent_table(rng, plant_type):
    total = rng.randint(40, 120)
    cuts = sorted(rng.sample(range(1, total), 3))
    counts = [b - a for a, b in zip([0, *cuts], [*cuts, total], strict=True)]
    percents = [round(100 * c / total) for c in counts]
    percents[-1] += 100 - sum(percents)
    if plant_type == "percent_sum_off":
        percents[0] += 4
    labels = ["Strongly agree", "Agree", "Disagree", "Strongly disagree"]
    rows = [
        ["Response", "f", "%"],
        *[[lab, str(c), f"{p}%"] for lab, c, p in zip(labels, counts, percents, strict=True)],
    ]
    return rows, labels[0]


def build_cases(ctx: Context):
    rng = random.Random(ctx.seed)
    hosts = ctx.hosts()
    queue = [t for t in (*ERROR_TYPES, *CONTROL_TYPES) for _ in range(PER_TYPE)]
    rng.shuffle(queue)
    copies, host_names = [], list(hosts)
    while queue:
        host = host_names[len(copies) % len(host_names)]
        types = [queue.pop() for _ in range(min(PLANTS_PER_COPY, len(queue)))]
        copies.append({"host": host, "types": types, "seed": rng.randrange(1 << 30)})
    return copies


def _plant(extraction, case):
    rng = random.Random(case["seed"])
    pages = spaced_pages(rng, body_pages(extraction), len(case["types"]), PAGE_GAP)
    plants = []
    for page, plant_type in zip(pages, case["types"], strict=True):
        split = split_for(rng)
        if plant_type.startswith("p_"):
            sentence, token = _inferential(rng, plant_type)
            index = max(
                i for i, b in enumerate(extraction.blocks) if b.page == page and not b.is_furniture
            )
            host = extraction.blocks[index]
            extraction.blocks.insert(
                index + 1,
                TextBlock(
                    page=page,
                    bbox=host.bbox,
                    text=sentence,
                    max_font_size=host.max_font_size,
                    bold_ratio=0.0,
                ),
            )
        else:
            build = _grim_table if plant_type.startswith("grim") else _percent_table
            rows, token = build(rng, plant_type)
            extraction.tables.append(TableBlock(page=page, rows=rows))
        plants.append({"type": plant_type, "page": page, "token": token, "split": split})
    return plants


def _key(flag):
    return ((flag.detail or {}).get("kind"), flag.page_anchor, flag.evidence_excerpt)


def _page(anchor):
    try:
        return int(str(anchor).split()[-1])
    except (ValueError, IndexError):
        return None


def run(ctx: Context) -> tuple[list[MetricRow], dict]:
    hosts = ctx.hosts()
    twins = {
        name: {_key(f) for f in _compute_forensics_findings(load_extraction(path)).flag_drafts}
        for name, path in hosts.items()
    }
    tally = defaultdict(lambda: [0, 0])  # (split, type) -> [detected, planted]
    new_flags, matched_flags, unmatched, records = 0, 0, Counter(), []
    for case in build_cases(ctx):
        extraction = load_extraction(hosts[case["host"]])
        plants = _plant(extraction, case)
        fresh = [
            f
            for f in _compute_forensics_findings(extraction).flag_drafts
            if _key(f) not in twins[case["host"]]
        ]
        new_flags += len(fresh)
        claimed = set()
        for plant in plants:
            hits = [
                i
                for i, f in enumerate(fresh)
                if _page(f.page_anchor) is not None
                and abs(_page(f.page_anchor) - plant["page"]) <= 1
                and plant["token"].replace(" ", "") in (f.evidence_excerpt or "").replace(" ", "")
            ]
            claimed.update(hits)
            tally[(plant["split"], plant["type"])][1] += 1
            if hits:
                tally[(plant["split"], plant["type"])][0] += 1
            records.append(
                {
                    **plant,
                    "host": case["host"],
                    "detected": bool(hits),
                    "kinds": sorted({(fresh[i].detail or {}).get("kind") for i in hits}),
                    "severities": sorted({str(fresh[i].severity) for i in hits}),
                }
            )
        for i, flag in enumerate(fresh):
            plant = next((p for p in plants if i in claimed and p["type"] in ERROR_TYPES), None)
            if i in claimed and plant:
                matched_flags += 1
            else:
                unmatched[(flag.detail or {}).get("kind")] += 1
    rows = []
    for split in ("dev", "holdout"):
        for plant_type in ERROR_TYPES:
            found, planted = tally[(split, plant_type)]
            rows.append(
                rate_row(LANE, f"recall {plant_type}", "plant", found, planted, split=split)
            )
        errors = [tally[(split, t)] for t in ERROR_TYPES]
        rows.append(
            rate_row(
                LANE,
                "recall (all error plants)",
                "plant",
                sum(e[0] for e in errors),
                sum(e[1] for e in errors),
                split=split,
            )
        )
        controls = [tally[(split, t)] for t in CONTROL_TYPES]
        alarms, planted = sum(c[0] for c in controls), sum(c[1] for c in controls)
        rows.append(
            rate_row(
                LANE,
                "controls kept silent",
                "control plant",
                planted - alarms,
                planted,
                split=split,
                note="A control is a correct statistic; any finding on it is a false alarm.",
            )
        )
    rows.append(
        rate_row(
            LANE,
            "precision of plant-induced findings",
            "new finding",
            matched_flags,
            new_flags,
            note="New findings that name an error plant, over all findings the plants caused.",
        )
    )
    rows += real_precision_rows(ctx.golden, LANE)
    return rows, {
        "plants": records,
        "unmatched_new_findings_by_kind": dict(unmatched),
        "twin_natural_findings": {k: len(v) for k, v in twins.items()},
    }
