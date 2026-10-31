"""F5 citation integrity on planted known-answer cases.

Part A (linkage, offline) plants a missing reference entry or removes every
in-text marker of one entry, in copies of two thesis extractions, and diffs
the copy against the unplanted twin. Part B (existence and retraction) puts a
non-existent DOI, a retracted work or a real work into a reference entry and
runs the real verification path against the public registries. Registry
answers are cached under `<golden>/f5/cache`, so a rerun makes no requests.

No language-model call is made anywhere: claim support is skipped.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import random
import re
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
from tools.accuracy.core import (
    Context,
    MetricRow,
    load_extraction,
    rate_row,
    split_for,
)

from app.checks.citations import verify
from app.checks.citations.cross_match import _reference_surnames, cross_match
from app.checks.citations.extract import extract_in_text_citations
from app.checks.citations.linkage_guard import guarded_absence_candidates
from app.checks.citations.numbered import analyze_numbered_citations, reference_anchor
from app.config import Settings
from app.ingest.patterns import load_patterns
from app.ingest.references import extract_references, non_reference_blocks, select_references
from app.ingest.schemas import SourceLineSpan, TextBlock
from app.ingest.source_slices import line_inventory_valid
from app.models.citation import Citation
from app.models.enums import CitationParseStatus
from app.models.run import Flag

LANE = "f5"
SCOPE = (
    "Known-answer cases planted into stored extractions of the permitted theses; "
    "existence and retraction checked live against public registries: tests the checks, "
    "not PDF extraction. Not instructor-labelled."
)
LINKAGE_TYPES = ("orphan_citation", "uncited_reference")
EXISTENCE_TYPES = ("nonexistent_doi", "retracted_doi")
CONTROL_TYPES = ("intact_entry", "valid_doi")

# Every value here can be overridden in <golden>/f5/config.json.
DEFAULTS = {
    "per_type": 20,
    "plants_per_copy": 3,
    "numbered_tail_entries": 20,
    "crossref_list_rps": 1.0,
    "crossref_single_rps": 3.0,
    "other_rps": 3.0,
    "retraction_feed_rows": 200,
    "valid_pool_rows": 100,
    "valid_pool_from_year": 2018,
    "valid_pool_until_year": 2023,
    "handle_not_found_code": 100,
    "handle_found_code": 1,
    "host_for_entries": "destamp",
    "notice_title_pattern": r"notice of|retraction|withdrawn|expression of concern",
    "accusation_words": r"fake|fabricat|fraud|forg|plagiar|dishonest|false",
}
LINKAGE_KINDS = {
    "orphan_in_text_citation": "orphan_citation",
    "uncited_reference": "uncited_reference",
}
# Line ids for planted entries start above any id a real extraction uses.
_PLANT_LINE_BASE = 100000
_PLANT_GAP_LINES = 0.4
USER_AGENT = "veridical-accuracy-harness"
APA_MAX_AUTHORS = 20
LIMITATIONS = (
    "Retracted plants are drawn from works whose Crossref record already carries a "
    "Retraction Watch relation, the field the check reads, so retraction recall measures "
    "reading that field, not finding retractions recorded elsewhere.",
    "Linkage depends on the host: DESTAMP (author-year) parses, MIT's 144 numbered entries "
    "do not (BUG-534), and the other three hosts' reference lists are refused, so pooled "
    "linkage figures mostly reflect which host a plant landed on.",
    "The shipped check withholds every linkage finding on both usable hosts (BUG-590), so "
    "'intact entries kept silent [check output]' is silent by construction there.",
    "The existence case set is frozen in f5/existence_cases.json on first build; registry "
    "answers come from the cache, with the fetch dates listed in the details.",
)
FEED_FIELDS = "DOI,title,update-to,updated-by,issued,author,container-title,volume,issue,page"


class RateLimited(RuntimeError):
    """A registry answered 429. The lane stops asking and reports what it has."""


# --- registry access: paced, cached, public pool only -----------------------------------


class CachingTransport(httpx.AsyncBaseTransport):
    def __init__(self, cache_dir, config):
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.inner = httpx.AsyncHTTPTransport()
        self.last_sent: dict[str, float] = {}
        self.sent = 0
        self.hits = 0
        # Dates the registry answers used in this run were fetched on.
        self.fetched_on: set[str] = set()

    def _interval(self, url: httpx.URL) -> float:
        if url.host == "api.crossref.org":
            rate = (
                self.config["crossref_list_rps"]
                if url.path.rstrip("/") == "/works"
                else self.config["crossref_single_rps"]
            )
        else:
            rate = self.config["other_rps"]
        return 1.0 / rate

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # The public pool is used on purpose: no contact address leaves this machine.
        parts = urlsplit(str(request.url))
        query = [(k, v) for k, v in parse_qsl(parts.query) if k != "mailto"]
        url = httpx.URL(parts._replace(query=urlencode(query)).geturl())
        key = hashlib.sha256(f"{request.method} {url}".encode()).hexdigest()
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            self.hits += 1
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.fetched_on.add(stored["fetched_at"][:10])
            return httpx.Response(
                stored["status"],
                headers=stored["headers"],
                content=stored["body"].encode("utf-8"),
                request=request,
            )
        wait = self.last_sent.get(url.host, 0.0) + self._interval(url) - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        request = httpx.Request(request.method, url, headers={"User-Agent": USER_AGENT})
        response = await self.inner.handle_async_request(request)
        body = await response.aread()
        self.last_sent[url.host] = time.monotonic()
        self.sent += 1
        self.fetched_on.add(datetime.now(UTC).date().isoformat())
        if response.status_code == 429:
            raise RateLimited(f"429 from {url.host}")
        keep = {
            name: response.headers[name]
            for name in ("content-type", "location")
            if name in response.headers
        }
        path.write_text(
            json.dumps(
                {
                    "url": str(url),
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "status": response.status_code,
                    "headers": keep,
                    "body": body.decode("utf-8", errors="replace"),
                }
            ),
            encoding="utf-8",
        )
        return httpx.Response(response.status_code, headers=keep, content=body, request=request)


async def _json(client, url, params=None):
    response = await client.get(url, params=params)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


# --- Part A: linkage --------------------------------------------------------------------


class MemorySession:
    """Collects what the check adds; it never reads, so confirmations fail closed."""

    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)

    async def scalar(self, statement):
        return None

    async def flush(self):
        for row in self.rows:
            if getattr(row, "id", None) is None:
                row.id = len(self.rows)

    async def commit(self):
        pass


def _state(extraction, patterns, settings):
    drafts = extract_references(extraction, patterns)
    citations = [Citation(id=i + 1, manuscript_id=1, **asdict(d)) for i, d in enumerate(drafts)]
    body = non_reference_blocks(extraction, patterns)
    gaps: set[str] = set()
    in_text = extract_in_text_citations(body, settings=settings, coverage_gaps=gaps)
    cross = cross_match(in_text, citations)
    numeric = analyze_numbered_citations(
        body,
        citations,
        max_range_span=settings.numeric_citation_max_range_span,
        context_file=settings.numeric_citation_context_file,
        source_block_index_by_id={id(b): i for i, b in enumerate(extraction.blocks)},
    )
    return {
        "citations": citations,
        "body": body,
        "gaps": gaps,
        "cross": cross,
        "numeric": numeric,
        "numbered": numeric.n_numbered_reference_entries > 0,
        "selection": select_references(extraction, patterns),
    }


def _label(ref) -> int | None:
    label = reference_anchor(ref.raw_text, ref.order_index).removeprefix("reference #")
    return int(label) if label.isdigit() else None


def _year(value) -> str:
    match = re.search(r"\d{4}", str(value or ""))
    return match.group(0) if match else ""


def _orphan_key(surnames, year):
    return ("orphan", "ay", frozenset(s.casefold() for s in surnames), _year(year))


def _engine_findings(st, settings):
    """Candidates the linkage engine would report if no coverage rule withheld them,
    in the same normal form as `_flag_findings`."""
    found = set()
    if st["numbered"]:
        n_entries = st["numeric"].n_numbered_reference_entries
        for mark in st["numeric"].orphan_marks:
            for number in mark.numbers or ():
                if number > n_entries:
                    found.add(("orphan", "n", number))
        for ref in st["numeric"].confidently_uncited:
            found.add(("uncited", "n", _label(ref)))
    else:
        for cite in st["cross"].orphans:
            found.add(_orphan_key(cite.surnames, cite.year))
        absent, _, _ = guarded_absence_candidates(
            st["cross"].confidently_uncited, st["citations"], st["body"], settings
        )
        for ref in absent:
            found.add(("uncited", "ay", ref.raw_text))
    return found


def _flag_findings(flags):
    found = set()
    for flag in flags:
        detail = flag.detail or {}
        kind = detail.get("kind")
        if kind == "orphan_in_text_citation":
            if "number" in detail:
                found.add(("orphan", "n", detail["number"]))
            else:
                found.add(_orphan_key(detail.get("surnames") or [], detail.get("year")))
        elif kind == "uncited_reference":
            if detail.get("number") is not None:
                found.add(("uncited", "n", detail["number"]))
            else:
                found.add(("uncited", "ay", flag.evidence_excerpt))
    return found


async def _shipped(extraction, patterns, settings, *, gate: bool):
    """The real check on a stored extraction. Registry lookups answer "confirmed" so only
    linkage is exercised. With `gate` off the reading-order refusal is lifted, because
    that refusal covers the whole document on both hosts and would hide the linkage."""
    st = _state(extraction, patterns, settings)
    session = MemorySession()
    confirmed = verify.CitationVerdict(verify.VerdictKind.existence_confirmed)
    stack = [
        patch.object(verify, "verify_citation", AsyncMock(return_value=confirmed)),
        patch.object(verify, "write_audit_event", AsyncMock()),
        patch("app.checks.source_quality.write_audit_event", AsyncMock()),
    ]
    if not gate:
        stack.append(patch.object(verify, "source_limitation_detail", lambda _extraction: None))
    for item in stack:
        item.start()
    try:
        result = await verify.run_citation_integrity_check(
            session, None, 1, st["citations"], extraction, patterns, settings, None
        )
    finally:
        for item in stack:
            item.stop()
    flags = [row for row in session.rows if isinstance(row, Flag)]
    return result, _flag_findings(flags), st


def _load(path):
    """A host extraction with its reading-order ledger cleared. The ledger records the
    exact source lines of the original PDF, so any edit would trip its provenance check
    for a reason unrelated to linkage. It is cleared on the twin as well as the copy;
    the real reading-order refusal is reported separately as coverage."""
    extraction = load_extraction(path)
    extraction.reading_order = None
    return extraction


def _cut(extraction, edits):
    """Remove character ranges from blocks, keeping each block's line inventory valid.
    `edits` maps block index to a list of (start, end) ranges."""
    for index, ranges in edits.items():
        block = extraction.blocks[index]
        text, drop = block.text, [False] * len(block.text)
        for start, end in ranges:
            for i in range(start, min(end, len(text))):
                drop[i] = True
        before = [0]
        for flag in drop:
            before.append(before[-1] + (not flag))
        block.text = "".join(c for c, d in zip(text, drop, strict=True) if not d)
        if block.source_lines is not None:
            lines = []
            for line in block.source_lines:
                start, end = before[line.start], before[line.end]
                if end > start:
                    lines.append(line.model_copy(update={"start": start, "end": end}))
            block.source_lines = lines
            block.source_line_ids = [line.source_id for line in lines]
        assert line_inventory_valid(block), f"line inventory broken in block {index}"


def _drop_empty(extraction, indexes):
    for index in sorted(indexes, reverse=True):
        if not extraction.blocks[index].text.strip():
            del extraction.blocks[index]


def _entry_ranges(selection, order_indexes):
    edits = defaultdict(list)
    for order in order_indexes:
        for fragment in selection.groups[order]:
            edits[fragment.source_block_index].append((fragment.source_start, fragment.source_end))
    return edits


def _where(extraction, fragment):
    """Block index and offset of a body piece inside the extraction's blocks."""
    if hasattr(fragment, "source_block_index"):
        return fragment.source_block_index, fragment.source_start
    for index, block in enumerate(extraction.blocks):
        if block is fragment:
            return index, 0
    raise ValueError("body piece is not part of the extraction")


def _host_pool(extraction, patterns, settings, config):
    """What can be planted into this host, found on its unplanted twin."""
    st = _state(extraction, patterns, settings)
    pool = {"style": None, "reason": None, "orphan": [], "uncited": [], "intact": []}
    if not st["citations"]:
        pool["reason"] = "reference list refused: " + (
            ", ".join(st["selection"].issues) or "no entries parsed"
        )
        return pool, st
    if st["numbered"]:
        pool["style"] = "numbered"
        numeric, n_entries = st["numeric"], st["numeric"].n_numbered_reference_entries
        by_number = defaultdict(list)
        for mark in numeric.marks:
            for number in mark.numbers or ():
                by_number[number].append(mark)
        order_of = {_label(c): c.order_index for c in st["citations"]}
        for number, marks in by_number.items():
            if number not in order_of:
                continue
            strong = all(m.classification == "citation" for m in marks)
            if number > n_entries - config["numbered_tail_entries"] and strong:
                pool["orphan"].append(
                    {
                        "number": number,
                        "order": order_of[number],
                        "strong_marks": sum(m.strong_citation_context for m in marks),
                    }
                )
            if strong and all(m.raw_text == f"[{number}]" for m in marks):
                pool["uncited"].append(
                    {
                        "number": number,
                        "order": order_of[number],
                        "marks": [(m.block_index, m.start, len(m.raw_text)) for m in marks],
                    }
                )
        for ref, _ in [(r, None) for r in st["citations"]]:
            if _label(ref) in by_number:
                pool["intact"].append({"number": _label(ref), "order": ref.order_index})
        return pool, st
    pool["style"] = "author_year"
    linked = defaultdict(list)
    for mark, refs in st["cross"].linked:
        for ref in refs:
            linked[ref.order_index].append((mark, len(refs)))
    for ref in st["citations"]:
        marks = linked.get(ref.order_index)
        if not marks or ref.parse_status != CitationParseStatus.parsed or not ref.year:
            continue
        surnames = {s.casefold() for s in _reference_surnames(ref)}
        item = {"order": ref.order_index, "surnames": surnames, "year": _year(ref.year)}
        pool["orphan"].append(item)
        pool["intact"].append(item)
        if all(count == 1 for _, count in marks):
            texts = {mark.raw_text for mark, _ in marks}
            ranges = defaultdict(list)
            for fragment in st["body"]:
                index, offset = _where(extraction, fragment)
                for text in texts:
                    for found in re.finditer(re.escape(text), fragment.text):
                        ranges[index].append((offset + found.start(), offset + found.end()))
            if ranges:
                pool["uncited"].append({**item, "texts": texts, "ranges": dict(ranges)})
    return pool, st


def _residual_mention(extraction, patterns, item):
    """Independent of the extractor: does the entry's lead surname still sit near its
    year anywhere in the body?"""
    surnames = sorted(item["surnames"])
    for block in non_reference_blocks(extraction, patterns):
        text = block.text.casefold()
        if item["year"] in text and any(s in text for s in surnames):
            return True
    return False


def build_cases(ctx: Context, config, hosts):
    rng = random.Random(ctx.seed)
    queue = [t for t in (*LINKAGE_TYPES, "intact_entry") for _ in range(config["per_type"])]
    rng.shuffle(queue)
    copies = []
    while queue:
        host = hosts[len(copies) % len(hosts)]
        types = [queue.pop() for _ in range(min(config["plants_per_copy"], len(queue)))]
        copies.append({"host": host, "types": types, "seed": rng.randrange(1 << 30)})
    return copies


def _plant_linkage(extraction, patterns, pool, case, st_twin):
    """Plant into `extraction`; returns the plant records. Orphans are placed first so
    the other plants can avoid entries that are deleted."""
    rng = random.Random(case["seed"])
    plants, edits, drop_orders, taken = [], defaultdict(list), [], set()
    deleted_numbers: set[int] = set()
    order = sorted(case["types"], key=lambda t: t != "orphan_citation")
    for plant_type in order:
        split = split_for(rng)
        if plant_type == "orphan_citation":
            options = [o for o in pool["orphan"] if o["order"] not in taken]
            if not options:
                continue
            pick = rng.choice(options)
            if pool["style"] == "numbered":
                n = len(st_twin["citations"])
                gone = list(range(pick["order"], n))
                deleted_numbers = {_label(st_twin["citations"][i]) for i in gone}
                target = {
                    "number": pick["number"],
                    "deleted_numbers": sorted(deleted_numbers),
                    "strong_marks": pick["strong_marks"],
                }
            else:
                gone = [pick["order"]]
                target = {"surnames": sorted(pick["surnames"]), "year": pick["year"]}
            taken.update(gone)
            drop_orders.extend(gone)
        elif plant_type == "uncited_reference":
            options = [
                o
                for o in pool["uncited"]
                if o["order"] not in taken and o.get("number") not in deleted_numbers
            ]
            if not options:
                continue
            pick = rng.choice(options)
            taken.add(pick["order"])
            if pool["style"] == "numbered":
                for block, start, length in pick["marks"]:
                    edits[block].append((start, start + length))
                target = {"number": pick["number"]}
            else:
                for block, ranges in pick["ranges"].items():
                    edits[block].extend(ranges)
                target = {
                    "raw_text": st_twin["citations"][pick["order"]].raw_text,
                    "surnames": sorted(pick["surnames"]),
                    "year": pick["year"],
                }
        else:
            options = [
                o
                for o in pool["intact"]
                if o["order"] not in taken and o.get("number") not in deleted_numbers
            ]
            if not options:
                continue
            pick = rng.choice(options)
            taken.add(pick["order"])
            if pool["style"] == "numbered":
                target = {"number": pick["number"]}
            else:
                target = {
                    "raw_text": st_twin["citations"][pick["order"]].raw_text,
                    "surnames": sorted(pick["surnames"]),
                    "year": pick["year"],
                }
        plants.append({"type": plant_type, "split": split, "target": target, "pick": pick})
    for edit_order in (drop_orders,):
        for block, ranges in _entry_ranges(st_twin["selection"], edit_order).items():
            edits[block].extend(ranges)
    _cut(extraction, edits)
    _drop_empty(extraction, edits)
    return plants


def _names(plant, finding, style):
    """Does a finding name the planted entry?"""
    kind = "orphan" if plant["type"] == "orphan_citation" else "uncited"
    if finding[0] != kind:
        return False
    target = plant["target"]
    if style == "numbered":
        return finding[1] == "n" and finding[2] == target["number"]
    if kind == "orphan":
        return (
            finding[1] == "ay"
            and finding[3] == target["year"]
            and bool(finding[2] & set(target["surnames"]))
        )
    return finding[1] == "ay" and finding[2] == target["raw_text"]


def _true_by_construction(plant, finding, style):
    """A new orphan finding for another entry that the same plant also deleted."""
    if plant["type"] != "orphan_citation" or finding[0] != "orphan":
        return False
    return style == "numbered" and finding[2] in plant["target"]["deleted_numbers"]


async def _part_a(ctx, config, patterns, settings):
    hosts = ctx.hosts()
    pools, twins, coverage, twin_states = {}, {}, {}, {}
    usable = []
    for name, path in hosts.items():
        extraction = load_extraction(path)
        pool, st = _host_pool(extraction, patterns, settings, config)
        gated, _, _ = await _shipped(extraction, patterns, settings, gate=True)
        result, relaxed, _ = await _shipped(_load(path), patterns, settings, gate=False)
        coverage[name] = {
            "style": pool["style"],
            "refused": pool["reason"],
            "n_references": len(st["citations"]),
            "n_parse_failed": sum(
                c.parse_status != CitationParseStatus.parsed for c in st["citations"]
            ),
            "twin_check_as_shipped": {
                "outcome": str(gated.outcome),
                "coverage_reason": gated.detail.get("coverage_reason"),
                "n_flags": gated.detail.get("n_flags"),
            },
            "twin_check_reading_order_gate_lifted": {
                "outcome": str(result.outcome),
                "coverage_reason": result.detail.get("coverage_reason"),
                "n_linkage_unassessed": result.detail.get("n_linkage_unassessed"),
                "citation_parse_issues": result.detail.get("citation_parse_issues"),
                "n_flags": result.detail.get("n_flags"),
            },
            "numeric_marks": dict(
                Counter(
                    f"{m.classification}/{'strong' if m.strong_citation_context else 'weak'}"
                    for m in st["numeric"].marks
                )
            ),
            "n_orphan_candidates": len(pool["orphan"]),
            "n_uncited_candidates": len(pool["uncited"]),
        }
        if pool["style"] and pool["orphan"] and (pool["uncited"] or pool["intact"]):
            usable.append(name)
            pools[name] = pool
            twin_states[name] = st
            twins[name] = {
                "engine": _engine_findings(st, settings),
                "shipped": relaxed,
                "style": pool["style"],
            }
    tally = {
        level: defaultdict(lambda: [0, 0]) for level in ("shipped", "engine")
    }  # (split, type) -> [detected, planted]
    new_counts = {level: [0, 0] for level in tally}  # [matched, all new]
    unmatched = {level: [] for level in tally}
    records, record_of = [], {}
    if not usable:
        return {"coverage": coverage, "note": "no host supports linkage planting"}
    for case in build_cases(ctx, config, usable):
        name = case["host"]
        path = hosts[name]
        extraction = _load(path)
        plants = _plant_linkage(extraction, patterns, pools[name], case, twin_states[name])
        for plant in plants:
            if (
                plant["type"] == "uncited_reference"
                and pools[name]["style"] == "author_year"
                and _residual_mention(extraction, patterns, plant["pick"])
            ):
                plant["invalid"] = "a mention of the entry remains in the body"
        _, shipped_set, st_copy = await _shipped(extraction, patterns, settings, gate=False)
        found = {"shipped": shipped_set, "engine": _engine_findings(st_copy, settings)}
        style = twins[name]["style"]
        for level, level_found in found.items():
            fresh = level_found - twins[name][level]
            claimed = set()
            for plant in plants:
                if plant.get("invalid"):
                    continue
                hits = {f for f in fresh if _names(plant, f, style)}
                if plant["type"] == "intact_entry":
                    hits = {
                        f
                        for f in fresh
                        if (
                            f[1] == "n"
                            and f[2] == plant["target"]["number"]
                            or f[1] == "ay"
                            and (
                                f[2] == plant["target"]["raw_text"]
                                or (
                                    isinstance(f[2], frozenset)
                                    and f[3] == plant["target"]["year"]
                                    and bool(f[2] & set(plant["target"]["surnames"]))
                                )
                            )
                        )
                    }
                else:
                    claimed.update(hits)
                    claimed.update(f for f in fresh if _true_by_construction(plant, f, style))
                tally[level][(plant["split"], plant["type"])][1] += 1
                tally[level][(plant["split"], plant["type"])][0] += bool(hits)
                if level == "shipped":
                    record_of[id(plant)] = {
                        "host": name,
                        "type": plant["type"],
                        "split": plant["split"],
                        "target": {
                            k: (sorted(v) if isinstance(v, set) else v)
                            for k, v in plant["target"].items()
                        },
                    }
                    records.append(record_of[id(plant)])
                record_of[id(plant)][f"detected_{level}"] = bool(hits)
            new_counts[level][1] += len(fresh)
            new_counts[level][0] += len(fresh & claimed)
            unmatched[level] += [
                {"host": name, "finding": [list(x) if isinstance(x, frozenset) else x for x in f]}
                for f in sorted(fresh - claimed, key=str)
            ]
    return tally, new_counts, unmatched, records, coverage


# --- Part B: existence and retraction --------------------------------------------------


def _split_of(doi: str) -> str:
    """Dev or holdout from the DOI itself, so a plant keeps its side whatever
    order the cases are verified in."""
    return "dev" if int(hashlib.sha256(doi.lower().encode()).hexdigest(), 16) % 2 else "holdout"


def _clean(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text or ""))).strip()


def _apa_entry(msg, doi):
    """A reference entry in the host's APA shape, from a Crossref record."""
    names = []
    for author in msg.get("author") or []:
        family, given = _clean(author.get("family")), _clean(author.get("given"))
        initials = " ".join(f"{p[0]}." for p in re.split(r"[\s-]+", given) if p)
        if family:
            names.append(f"{family}, {initials}".rstrip(", "))
    if not names:
        return None
    # APA 7 lists up to 20 authors; beyond that, the first 19, an ellipsis and
    # the last. Dropping authors would make a correct citation look wrong.
    if len(names) > APA_MAX_AUTHORS:
        authors = ", ".join(names[: APA_MAX_AUTHORS - 1]) + ", . . . " + names[-1]
    elif len(names) == 1:
        authors = names[0]
    else:
        authors = ", ".join(names[:-1]) + ", & " + names[-1]
    parts = (msg.get("issued") or {}).get("date-parts") or [[None]]
    year, title = parts[0][0], _clean((msg.get("title") or [""])[0]).rstrip(".")
    venue = _clean((msg.get("container-title") or [""])[0])
    if not (year and title and venue):
        return None
    volume = msg.get("volume")
    issue = msg.get("issue")
    pages = _clean(msg.get("page")).replace("-", "–") if msg.get("page") else None
    where = venue + (f", {volume}" if volume else "") + (f"({issue})" if volume and issue else "")
    where += f", {pages}" if pages else ""
    return f"{authors} ({year}). {title}. {where}. https://doi.org/{doi}"


def _shaped_nonexistent(rng, doi):
    """A syntactically valid DOI shaped like `doi`, with its digit runs redrawn."""
    prefix, _, suffix = doi.partition("/")

    def redraw(match):
        digits = match.group(0)
        return "".join(rng.choice("0123456789") for _ in digits)

    return prefix + "/" + re.sub(r"\d{3,}", redraw, suffix)


async def _handle_code(client, doi):
    """The handle system's own answer. An unregistered handle comes back as a 404 that still
    carries a body with the response code, so the status is not treated as an error here."""
    response = await client.get(f"https://doi.org/api/handles/{doi}")
    if response.status_code not in (200, 404):
        response.raise_for_status()
    try:
        return response.json().get("responseCode")
    except ValueError:
        return None


def _worth_citing(msg):
    return (
        not (msg.get("update-to") or msg.get("updated-by"))
        and _apa_entry(msg, msg["DOI"]) is not None
        and "/" in msg["DOI"]
        and re.fullmatch(r"[\x21-\x7e]+", msg["DOI"]) is not None
    )


def _append_entry(extraction, patterns, text):
    selection = select_references(extraction, patterns)
    last = selection.span[-1]
    block = extraction.blocks[last.source_block_index]
    # The planted entry is one line with its own source identity, as a real
    # extracted line would have, so the reading-order checks accept it.
    # It starts at the left margin like a new entry's first line (a hanging
    # indent marks continuation lines) and sits just below the last line.
    first = selection.groups[-1][0]
    x0, _, x1, y1 = block.bbox[0], block.bbox[1], block.bbox[2], block.bbox[3]
    height = block.bbox[3] - block.bbox[1]
    left = min(x0, first.bbox[0])
    box = (
        left,
        y1 + height * _PLANT_GAP_LINES,
        max(x1, left + 1),
        y1 + height * (1 + _PLANT_GAP_LINES),
    )
    line_id = f"{block.page}:{_PLANT_LINE_BASE + len(extraction.blocks)}:0"
    extraction.blocks.insert(
        last.source_block_index + 1,
        TextBlock(
            page=block.page,
            bbox=box,
            text=text,
            max_font_size=block.max_font_size,
            bold_ratio=0.0,
            source_line_ids=[line_id],
            source_lines=[
                SourceLineSpan(
                    start=0,
                    end=len(text),
                    bbox=box,
                    max_font_size=block.max_font_size,
                    bold_ratio=0.0,
                    is_furniture=False,
                    source_id=line_id,
                )
            ],
        ),
    )


def _plant_entries(extraction, patterns, entries):
    """Append the entries to the host's reference list and return the parsed drafts."""
    # Existence checks do not depend on reading order; as in part A, the stored
    # order record is dropped because the planted lines are not in it.
    extraction.reading_order = None
    before = len(extract_references(extraction, patterns))
    for text in entries:
        _append_entry(extraction, patterns, text)
    return extract_references(extraction, patterns)[before:]


async def _verify_entries(client, settings, drafts):
    verdicts = []
    off = AsyncMock(return_value=None)
    with (
        patch.object(verify.cache, "get_cached", off),
        patch.object(verify.cache, "store_result", AsyncMock()),
        patch.object(verify.cache, "is_instructor_confirmed", AsyncMock(return_value=False)),
        patch.object(verify, "_fetch_supplementary_abstract", off),
    ):
        for index, draft in enumerate(drafts):
            citation = Citation(id=index + 1, manuscript_id=1, **asdict(draft))
            verdict = await verify.verify_citation(None, client, citation, settings=settings)
            flag = verify._verdict_flag_draft(citation, verdict)
            verdicts.append((verdict, flag))
    return verdicts


async def _part_b(ctx, config, patterns, settings, client, cache_stats):
    rng = random.Random(ctx.seed + 5)
    today = datetime.now(UTC).date().isoformat()
    notes: list[str] = []
    candidates = {"nonexistent_doi": [], "retracted_doi": [], "valid_doi": []}
    # The case set is frozen on first build: Crossref's random sample and the
    # handle answers would otherwise change it whenever the cache is cleared.
    frozen = ctx.golden / LANE / "existence_cases.json"
    try:
        if not frozen.exists():
            feed = await _json(
                client,
                "https://api.crossref.org/works",
                {
                    "filter": "update-type:retraction",
                    "rows": str(config["retraction_feed_rows"]),
                    "select": FEED_FIELDS,
                },
            )
            pool = await _json(
                client,
                "https://api.crossref.org/works",
                {
                    "filter": (
                        "type:journal-article,"
                        f"from-pub-date:{config['valid_pool_from_year']},"
                        f"until-pub-date:{config['valid_pool_until_year']}"
                    ),
                    "sample": str(config["valid_pool_rows"]),
                    "select": FEED_FIELDS,
                },
            )
            valid_pool = [m for m in pool["message"]["items"] if _worth_citing(m)]
            rng.shuffle(valid_pool)
            # A feed item either is the retracted work (it carries `updated-by`) or names it in
            # `update-to`; both are followed, and each is re-fetched on the planting day.
            targets = {}
            for item in feed["message"]["items"]:
                if any(
                    (r.get("type") or "").lower() == "retraction"
                    for r in item.get("updated-by") or []
                ):
                    targets.setdefault(item["DOI"], None)
                for relation in item.get("update-to") or []:
                    if (relation.get("type") or "").lower() == "retraction" and relation.get("DOI"):
                        targets.setdefault(relation["DOI"], None)
            wanted = config["per_type"]
            for doi in targets:
                if len(candidates["retracted_doi"]) >= wanted:
                    break
                record = await _json(client, f"https://api.crossref.org/works/{doi}")
                if record is None:
                    continue
                msg = record["message"]
                retractions = [
                    r
                    for r in (msg.get("updated-by") or []) + (msg.get("update-to") or [])
                    if (r.get("type") or "").lower() == "retraction"
                ]
                watched = [r for r in retractions if r.get("source") == "retraction-watch"]
                title = _clean((msg.get("title") or [""])[0])
                # A notice's own title announces the retraction, so it would not test anything.
                if (
                    not watched
                    or _apa_entry(msg, msg["DOI"]) is None
                    or re.match(config["notice_title_pattern"], title, re.IGNORECASE)
                ):
                    continue
                candidates["retracted_doi"].append(
                    {
                        "doi": msg["DOI"],
                        "entry": _apa_entry(msg, msg["DOI"]),
                        "checked_on": today,
                        "relations": [
                            {
                                "type": r.get("type"),
                                "source": r.get("source"),
                                "label": r.get("label"),
                            }
                            for r in retractions
                        ],
                    }
                )
            for msg in valid_pool:
                if len(candidates["valid_doi"]) >= wanted:
                    break
                candidates["valid_doi"].append(
                    {"doi": msg["DOI"], "entry": _apa_entry(msg, msg["DOI"]), "checked_on": today}
                )
            for msg in valid_pool:
                if len(candidates["nonexistent_doi"]) >= wanted:
                    break
                for _ in range(5):
                    doi = _shaped_nonexistent(rng, msg["DOI"])
                    if doi == msg["DOI"]:
                        continue
                    code = await _handle_code(client, doi)
                    if code == config["handle_not_found_code"]:
                        candidates["nonexistent_doi"].append(
                            {
                                "doi": doi,
                                "entry": _apa_entry(msg, doi),
                                "checked_on": today,
                                "handle_response_code": code,
                                "borrowed_from": msg["DOI"],
                            }
                        )
                        break
    except RateLimited as stop:
        notes.append(f"stopped early: {stop}")
    if frozen.exists():
        candidates = json.loads(frozen.read_text(encoding="utf-8"))["candidates"]
    elif not notes:
        frozen.write_text(
            json.dumps({"built_on": today, "candidates": candidates}, indent=1),
            encoding="utf-8",
        )
    drafts_by_type = {}
    outcomes = []
    for plant_type, items in candidates.items():
        items = items[: config["per_type"]]
        for start in range(0, len(items), config["plants_per_copy"]):
            chunk = items[start : start + config["plants_per_copy"]]
            host = load_extraction(ctx.extractions / f"{config['host_for_entries']}.json")
            drafts = _plant_entries(host, patterns, [c["entry"] for c in chunk])
            try:
                verdicts = await _verify_entries(client, settings, drafts)
            except RateLimited as stop:
                notes.append(f"stopped early: {stop}")
                break
            for item, draft, (verdict, flag) in zip(chunk, drafts, verdicts, strict=False):
                split = _split_of(item["doi"])
                intact = (
                    draft.doi == item["doi"] and draft.parse_status == CitationParseStatus.parsed
                )
                reason = (flag.detail.get("reason") if flag else "") or ""
                outcomes.append(
                    {
                        "type": plant_type,
                        "split": split,
                        "doi": item["doi"],
                        "checked_on": item["checked_on"],
                        "handle_response_code": item.get("handle_response_code"),
                        "retraction_relations": item.get("relations"),
                        "entry": item["entry"],
                        "parsed_with_doi_intact": intact,
                        "verdict": verdict.kind.value,
                        "flag_kind": flag.detail.get("kind") if flag else None,
                        "flag_severity": str(flag.severity) if flag else None,
                        "flag_reason": reason or None,
                        "accusation_wording": bool(
                            re.search(config["accusation_words"], reason, re.IGNORECASE)
                        ),
                    }
                )
        drafts_by_type[plant_type] = len(items)
    return outcomes, notes, drafts_by_type


def _part_b_rows(outcomes):
    rows = []
    expected = {
        "nonexistent_doi": ("not_found", "unverifiable_not_found"),
        "retracted_doi": ("retracted", "retracted_source"),
    }
    for split in ("dev", "holdout"):
        mine = [o for o in outcomes if o["split"] == split]
        for plant_type in EXISTENCE_TYPES:
            group = [o for o in mine if o["type"] == plant_type and o["parsed_with_doi_intact"]]
            verdict, kind = expected[plant_type]
            hit = [o for o in group if o["verdict"] == verdict and o["flag_kind"] == kind]
            rows.append(
                rate_row(LANE, f"recall {plant_type}", "plant", len(hit), len(group), split=split)
            )
        controls = [o for o in mine if o["type"] == "valid_doi" and o["parsed_with_doi_intact"]]
        quiet = [o for o in controls if o["flag_kind"] is None]
        rows.append(
            rate_row(
                LANE,
                "valid DOIs kept silent",
                "control entry",
                len(quiet),
                len(controls),
                split=split,
                note="A real registered work cited correctly; any finding is a false alarm.",
            )
        )
    for plant_type in EXISTENCE_TYPES:
        group = [o for o in outcomes if o["type"] == plant_type and o["flag_kind"]]
        clean = [o for o in group if not o["accusation_wording"]]
        rows.append(
            rate_row(
                LANE,
                f"wording check {plant_type}",
                "finding",
                len(clean),
                len(group),
                note="Findings with no accusation word in their text.",
            )
        )
    planted = [o for o in outcomes if o["type"] in EXISTENCE_TYPES + ("valid_doi",)]
    rows.append(
        rate_row(
            LANE,
            "entries parsed with the planted DOI intact",
            "planted entry",
            sum(o["parsed_with_doi_intact"] for o in planted),
            len(planted),
            note="Plants whose entry the reference parser did not read back are excluded above.",
        )
    )
    return rows


def _linkage_rows(tally, new_counts, unmatched):
    rows = []
    for level, label in (("shipped", "check output"), ("engine", "linkage engine")):
        for split in ("dev", "holdout"):
            for plant_type in LINKAGE_TYPES:
                found, planted = tally[level][(split, plant_type)]
                rows.append(
                    rate_row(
                        LANE,
                        f"recall {plant_type} [{label}]",
                        "plant",
                        found,
                        planted,
                        split=split,
                        note=(
                            "Findings the check emits for the entry."
                            if level == "shipped"
                            else "Candidates before coverage rules withhold them."
                        ),
                    )
                )
            control = tally[level][(split, "intact_entry")]
            rows.append(
                rate_row(
                    LANE,
                    f"intact entries kept silent [{label}]",
                    "control entry",
                    control[1] - control[0],
                    control[1],
                    split=split,
                    note="An untouched, correctly linked entry; a new finding naming it is false."
                    + (
                        " Silent by construction: the check emitted no linkage finding at all."
                        if level == "shipped" and new_counts[level][1] == 0
                        else ""
                    ),
                )
            )
        matched, total = new_counts[level]
        rows.append(
            rate_row(
                LANE,
                f"precision of plant-induced findings [{label}]",
                "new finding",
                matched,
                total,
                note="New findings that name a planted entry, over all findings the plants caused.",
            )
        )
    return rows


def run(ctx: Context) -> tuple[list[MetricRow], dict]:
    config = {**DEFAULTS, **ctx.lane_config(LANE)}
    folder = ctx.golden / LANE
    cache_dir = folder / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        _env_file=None,
        external_contact_email="",
        crossref_single_rps=config["crossref_single_rps"],
        crossref_list_rps=config["crossref_list_rps"],
    )
    patterns = load_patterns()

    async def go():
        transport = CachingTransport(cache_dir, config)
        async with httpx.AsyncClient(
            transport=transport, timeout=settings.external_http_timeout_seconds
        ) as client:
            part_a = await _part_a(ctx, config, patterns, settings)
            part_b = await _part_b(ctx, config, patterns, settings, client, transport)
        return part_a, part_b, transport

    part_a, (outcomes, notes, counts), transport = asyncio.run(go())
    rows, details = [], {}
    if isinstance(part_a, tuple):
        tally, new_counts, unmatched, records, coverage = part_a
        rows += _linkage_rows(tally, new_counts, unmatched)
        details.update(
            {
                "linkage_plants": records,
                "linkage_unmatched_new_findings": unmatched,
                "linkage_coverage": coverage,
            }
        )
    else:
        details["linkage_coverage"] = part_a
    rows += _part_b_rows(outcomes)
    details.update(
        {
            "existence_plants": outcomes,
            "existence_candidates": counts,
            "existence_notes": notes,
            "registry_requests_sent": transport.sent,
            "registry_cache_hits": transport.hits,
            "registry_answers_fetched_on": sorted(transport.fetched_on),
            "verdicts": dict(Counter((o["type"], o["verdict"]).__repr__() for o in outcomes)),
        }
    )
    return rows, details
