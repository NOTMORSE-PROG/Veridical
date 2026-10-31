"""F7 originality/reuse on planted known-answer cases, through the Library.

A private Postgres database is built from scratch for each run. Every host
thesis and a few openly licensed background theses enter it as active Library
entries, the way the Library page adds them. Each planted copy of a host is
then checked with the product's own originality check, and compared with its
unplanted twin checked against the same Library. A host never matches its own
Library copy because the copy and its checks share one instructor's group, and
the check never compares a group with itself.

Recall plants copy text from another host's Library entry (verbatim, lightly
edited, or rewritten). Silent plants must raise nothing: a quotation with its
citation, shared standard text that is also in the Library, and text whose
source is outside the Library or was removed from it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from tools.accuracy.core import (
    Context,
    MetricRow,
    body_pages,
    load_extraction,
    rate_row,
    real_precision_rows,
    spaced_pages,
)

LANE = "f7"
SCOPE = (
    "Known-answer cases planted into stored extractions of the permitted theses and checked "
    "against a Library built from those theses: tests the check, not PDF extraction. "
    "Not instructor-labelled."
)
RECALL_TYPES = ("verbatim", "light_paraphrase", "heavy_paraphrase")
SILENT_TYPES = (
    "quoted_inline",
    "block_quote",
    "boilerplate",
    "not_in_library",
    "removed_from_library",
)
# FEATURES.md F7.2 claims only exact and high-similarity matching, so rewritten
# text is reported beside the module's figures, never counted against them.
NOT_CLAIMED = {"heavy_paraphrase"}
FEATURE_CLAIM = (
    'FEATURES.md F7.2: "Similarity query (exact + high-similarity thresholds) on check".'
)
LIMITATIONS = (
    "Plants are inserted as one block after the last block of a body page, so where they fall "
    "against the 150-word passage boundaries is decided by the host page, not by a person "
    "pasting text. Every plant records the passage it landed in and its similarity to the donor.",
    "Six hosts and nine Library entries: a small Library, so natural matches between unrelated "
    "theses are rare and the natural false-positive figure is a floor, not an estimate.",
    "Light paraphrase uses a fixed synonym table and swaps two sentences; the embedding model "
    "ignores word order, so the swap tests nothing on its own.",
    "Heavy paraphrases are model-written, not written by a person.",
    "A host is kept from matching its own Library copy through the product's same-group "
    "exclusion; that rule is therefore assumed, not tested, here.",
)
QUOTE_FRAMES = (
    'As {author} ({year}) explained, "{text}" (p. {page}).',
    'According to {author} ({year}), "{text}" (p. {page}).',
    '{author} ({year}) stated that "{text}" (p. {page}).',
)
# Planted lines get source ids no extracted line uses, a little below the line
# they follow, the way the F5 lane plants reference entries.
PLANT_LINE_BASE = 200000
PLANT_GAP_LINES = 0.4
AUTHORS = (
    "Santos",
    "Reyes",
    "Cruz",
    "Garcia",
    "Mendoza",
    "Lim",
    "Tan",
    "Aquino",
    "Ramos",
)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"(])")
WORD = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*")


# ---------------------------------------------------------------- text tools


def tokens(text: str) -> list[str]:
    """Lower-cased word tokens with punctuation removed (yardstick 02, F7)."""
    return [t.lower() for t in WORD.findall(text)]


def shingles(text: str, size: int) -> set[tuple[str, ...]]:
    words = tokens(text)
    return {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}


def containment(part: str, whole: str, size: int) -> float:
    """Share of `part`'s word shingles that also occur in `whole` (Broder 1997)."""
    mine = shingles(part, size)
    return len(mine & shingles(whole, size)) / len(mine) if mine else 0.0


def longest_common_run(a: str, b: str) -> int:
    """Longest run of identical consecutive tokens shared by `a` and `b`."""
    x, y = tokens(a), tokens(b)
    best, previous = 0, [0] * (len(y) + 1)
    for i in range(1, len(x) + 1):
        current = [0] * (len(y) + 1)
        for j in range(1, len(y) + 1):
            if x[i - 1] == y[j - 1]:
                current[j] = previous[j - 1] + 1
                best = max(best, current[j])
        previous = current
    return best


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_END.split(" ".join(text.split())) if s.strip()]


def light_paraphrase(text: str, table: dict[str, str], share: float, rng: random.Random):
    """Replace about `share` of the words with table synonyms and swap two
    sentences. Returns the text and the share actually replaced."""
    words = list(WORD.finditer(text))
    eligible = [m for m in words if m.group().lower() in table]
    picked = rng.sample(eligible, min(len(eligible), round(share * len(words))))
    out, last = [], 0
    for match in sorted(picked, key=lambda m: m.start()):
        original = match.group()
        swap = table[original.lower()]
        if original[0].isupper():
            swap = swap[0].upper() + swap[1:]
        out += [text[last : match.start()], swap]
        last = match.end()
    edited = sentences("".join(out) + text[last:])
    if len(edited) >= 2:
        i, j = rng.sample(range(len(edited)), 2)
        edited[i], edited[j] = edited[j], edited[i]
    return " ".join(edited), len(picked) / max(1, len(words))


def banned_hits(text: str, banned: list[str]) -> list[str]:
    lowered = (text or "").lower()
    return [term for term in banned if term in lowered]


# ------------------------------------------------------------- case building


@dataclass
class Excerpt:
    doc: str
    text: str
    pages: list[int]

    @property
    def key(self) -> str:
        return hashlib.sha256(f"{self.doc}|{self.text}".encode()).hexdigest()[:16]


@dataclass
class Plant:
    type: str
    page: int
    text: str
    donor: str | None
    donor_pages: list[int] = field(default_factory=list)
    cluster: str = ""
    extra: dict = field(default_factory=dict)
    # The donor's own words, before any edit; the plant text for shared text.
    source_text: str = ""


def _text_blocks(extraction, excluded_pages: set[int]):
    pages = set(body_pages(extraction)) - excluded_pages
    return [
        b
        for b in extraction.blocks
        if not b.is_furniture and b.page in pages and len(b.text.split()) >= 12
    ]


def make_excerpt(doc: str, extraction, excluded: set[int], low: int, high: int, rng) -> Excerpt:
    """Whole sentences of body prose, `low` to `high` words, from consecutive
    paragraph-like blocks of one document."""
    blocks = _text_blocks(extraction, excluded)
    for _ in range(400):
        start = rng.randrange(len(blocks))
        target = rng.randint(low, high)
        taken, words = [], 0
        for block in blocks[start:]:
            taken.append(block)
            words += len(block.text.split())
            if words >= target:
                break
        picked, count = [], 0
        for sentence in sentences(" ".join(b.text for b in taken)):
            if count >= target:
                break
            picked.append(sentence)
            count += len(sentence.split())
        if low <= count <= high + 40 and len(picked) >= 2:
            pages = sorted({b.page for b in taken})
            return Excerpt(doc, " ".join(picked), pages)
    raise ValueError(f"No {low}-{high} word excerpt could be cut from {doc}")


def _config(ctx: Context) -> dict:
    config = ctx.lane_config(LANE)
    if not config:
        raise FileNotFoundError(f"{ctx.golden / LANE / 'config.json'} is required for the F7 lane")
    config.setdefault("seed", ctx.seed)
    return config


def _documents(ctx: Context, config: dict) -> dict[str, Path]:
    """Every extraction the lane can use: the shared hosts, the F7-only hosts
    and the Library-only background records."""
    found = {**ctx.hosts()}
    for folder in ("extractions", "library_only"):
        found.update({p.stem: p for p in sorted((ctx.golden / LANE / folder).glob("*.json"))})
    wanted = [
        *config["hosts"],
        *config["library_background"],
        config["boilerplate_carrier"],
        config["outside_library"],
        config["removed_from_library"],
    ]
    missing = [name for name in wanted if name not in found]
    if missing:
        raise FileNotFoundError(f"F7 lane extractions missing: {missing}")
    return {name: found[name] for name in wanted}


def _body_margin(extraction) -> float:
    lefts = [
        round(b.bbox[0])
        for b in extraction.blocks
        if not b.is_furniture and b.bbox is not None and len(b.text.split()) >= 12
    ]
    return float(Counter(lefts).most_common(1)[0][0]) if lefts else 72.0


def build_cases(
    ctx: Context,
    excluded_pages: dict[str, set[int]],
    *,
    checked_hosts: list[str] | None = None,
) -> tuple[list[dict], dict]:
    """The planted copies, reproducible from the seed. Heavy paraphrases come
    from `heavy_paraphrases.json`; any still missing are listed as requests."""
    config = _config(ctx)
    rng = random.Random(config["seed"])
    docs = _documents(ctx, config)
    extractions = {name: load_extraction(path) for name, path in docs.items()}
    table = json.loads((ctx.golden / LANE / "synonyms.json").read_text(encoding="utf-8"))["table"]
    boilerplate = json.loads((ctx.golden / LANE / "boilerplate.json").read_text(encoding="utf-8"))
    heavy_path = ctx.golden / LANE / "heavy_paraphrases.json"
    heavy = json.loads(heavy_path.read_text(encoding="utf-8")) if heavy_path.exists() else {}
    donors = config["hosts"]
    hosts = checked_hosts if checked_hosts is not None else donors
    long_low, long_high = config["excerpt_words"]["long"]
    quote_low, quote_high = config["excerpt_words"]["quote"]
    dev_pairs = {tuple(pair) for pair in config.get("dev_only_pairs", [])}

    queue = [t for t, n in config["per_type"].items() for _ in range(n)]
    rng.shuffle(queue)
    copies, requests = [], {}
    while queue:
        host = hosts[len(copies) % len(hosts)]
        types = [queue.pop() for _ in range(min(config["plants_per_copy"], len(queue)))]
        others = [h for h in donors if h != host]
        plants = []
        for plant_type in types:
            donor = None
            if plant_type in (*RECALL_TYPES, "quoted_inline", "block_quote"):
                donor = rng.choice(others)
            elif plant_type == "not_in_library":
                donor = config["outside_library"]
            elif plant_type == "removed_from_library":
                donor = config["removed_from_library"]
            plant = {"type": plant_type, "donor": donor}
            if plant_type == "boilerplate":
                item = boilerplate["items"][rng.randrange(len(boilerplate["items"]))]
                plant.update(text=item["text"], cluster=f"boilerplate:{item['id']}")
            else:
                quote = plant_type in ("quoted_inline", "block_quote")
                excerpt = make_excerpt(
                    donor,
                    extractions[donor],
                    excluded_pages.get(donor, set()),
                    quote_low if quote else long_low,
                    quote_high if quote else long_high,
                    rng,
                )
                plant.update(donor_pages=excerpt.pages, cluster=f"{donor}:{excerpt.key}")
                author, year = rng.choice(AUTHORS), rng.randint(2012, 2023)
                if plant_type == "light_paraphrase":
                    text, achieved = light_paraphrase(
                        excerpt.text, table, config["light_replace_share"], rng
                    )
                    plant.update(text=text, extra={"replaced_share": round(achieved, 3)})
                elif plant_type == "heavy_paraphrase":
                    key = f"heavy:{excerpt.key}"
                    plant.update(text=heavy.get(key), key=key)
                    if key not in heavy:
                        requests[key] = {
                            "source": excerpt.text,
                            "words": len(excerpt.text.split()),
                        }
                elif plant_type == "quoted_inline":
                    frame = rng.choice(QUOTE_FRAMES)
                    plant["text"] = frame.format(
                        author=author,
                        year=year,
                        text=excerpt.text,
                        page=rng.randint(3, 120),
                    )
                elif plant_type == "block_quote":
                    plant["text"] = f"{excerpt.text} ({author}, {year}, p. {rng.randint(3, 120)})"
                else:
                    plant["text"] = excerpt.text
                plant["source_text"] = excerpt.text
            plants.append(plant)
        dev_only = any((host, p["donor"]) in dev_pairs for p in plants)
        split = "dev" if dev_only or rng.random() < 0.5 else "holdout"
        copies.append(
            {
                "copy": len(copies),
                "host": host,
                "split": split,
                "seed": rng.randrange(1 << 30),
                "plants": plants,
            }
        )
    return copies, requests


def plant_block(extraction, index: int, text: str, left: float):
    """A one-line block with its own source identity, just below block
    `index` on the same page, so the line-inventory checks accept it."""
    from app.ingest.schemas import SourceLineSpan, TextBlock

    anchor = extraction.blocks[index]
    top, bottom = (anchor.bbox[1], anchor.bbox[3]) if anchor.bbox else (0.0, 12.0)
    height = max(bottom - top, 1.0)
    right = max(anchor.bbox[2] if anchor.bbox else left + 400.0, left + 200.0)
    box = (
        left,
        bottom + height * PLANT_GAP_LINES,
        right,
        bottom + height * (1 + PLANT_GAP_LINES),
    )
    line_id = f"{anchor.page}:{PLANT_LINE_BASE + len(extraction.blocks)}:0"
    return TextBlock(
        page=anchor.page,
        bbox=box,
        text=text,
        max_font_size=anchor.max_font_size,
        bold_ratio=0.0,
        source_line_ids=[line_id],
        source_lines=[
            SourceLineSpan(
                start=0,
                end=len(text),
                bbox=box,
                max_font_size=anchor.max_font_size,
                bold_ratio=0.0,
                is_furniture=False,
                source_id=line_id,
            )
        ],
    )


def place_plants(extraction, case: dict, excluded: set[int], config: dict) -> list[Plant]:
    """Insert the copy's plants into its host extraction, at least
    `page_gap` pages apart, each at the body text's left margin (an indent
    would make the block read as a quotation), except the block quotes."""
    from app.ingest.source_slices import line_inventory_valid

    rng = random.Random(case["seed"])
    plants = [p for p in case["plants"] if p.get("text")]
    candidates = [p for p in body_pages(extraction) if p not in excluded]
    pages = spaced_pages(rng, candidates, len(plants), config["page_gap"])
    margin = _body_margin(extraction)
    placed = []
    for page, plant in zip(pages, plants, strict=True):
        index = max(
            i for i, b in enumerate(extraction.blocks) if b.page == page and not b.is_furniture
        )
        left = margin + (
            config["block_quote_indent_points"] if plant["type"] == "block_quote" else 0
        )
        block = plant_block(extraction, index, plant["text"], left)
        assert line_inventory_valid(block), f"planted line inventory broken on page {page}"
        extraction.blocks.insert(index + 1, block)
        placed.append(
            Plant(
                type=plant["type"],
                page=page,
                text=plant["text"],
                donor=plant.get("donor"),
                donor_pages=plant.get("donor_pages", []),
                cluster=plant.get("cluster", ""),
                extra=dict(plant.get("extra", {})),
                source_text=plant.get("source_text") or plant["text"],
            )
        )
    return placed


# ------------------------------------------------------------------ matching


def page_of(anchor) -> int | None:
    match = re.fullmatch(r"p\.\s*(\d+)", str(anchor or "").strip())
    return int(match.group(1)) if match else None


def is_passage_flag(flag: dict) -> bool:
    return str(flag["detail"].get("kind", "")).endswith("_passage")


def flag_key(flag: dict) -> tuple:
    """What makes two findings the same finding across a copy and its twin.
    A plant moves later passage boundaries on its page, so passage findings
    are keyed by where they point, not by their exact text."""
    detail = flag["detail"]
    if is_passage_flag(flag):
        return (
            "passage",
            detail.get("matched_manuscript_id"),
            detail.get("matched_page"),
            page_of(flag["page_anchor"]),
        )
    return (
        detail.get("kind"),
        detail.get("matched_manuscript_id"),
        flag["page_anchor"],
    )


def overlap(a: str, b: str, size: int) -> float:
    """Shared word shingles over the smaller of the two sets: 1.0 when one
    text lies wholly inside the other, whichever is longer. A plant can be
    longer than the passage it lands in, or split across two."""
    first, second = shingles(a, size), shingles(b, size)
    if not first or not second:
        return 0.0
    return len(first & second) / min(len(first), len(second))


def touches(plant: Plant, flag: dict, match: dict) -> bool:
    """A passage finding on the plant: its quoted passage and the plant
    share at least `containment_min` of the smaller one's word shingles.
    The text decides, not the page: the product anchors a passage at its
    first block, which can sit pages before the plant (reported separately)."""
    if not is_passage_flag(flag):
        return False
    shared = overlap(plant.text, flag["evidence_excerpt"], match["shingle"])
    return shared >= match["containment_min"]


def right_source(plant: Plant, flag: dict, library_ids: dict[str, int], match: dict):
    """Whether the finding names the donor's Library record, and whether the
    passage it shows from that record is the copied text."""
    detail = flag["detail"]
    source = detail.get("matched_manuscript_id") == library_ids.get(plant.donor)
    shown = detail.get("matched_text") or ""
    anchor = source and (
        overlap(plant.source_text or plant.text, shown, match["shingle"])
        >= match["containment_min"]
    )
    return source, bool(anchor)


def score_copy(
    plants: list[Plant], fresh: list[dict], library_ids, match, *, carrier: str | None = None
) -> tuple[list, set]:
    """Per-plant outcomes, and which fresh findings a recall plant explains.

    A whole-document or chapter finding quotes no passage, so it is credited
    by the record it names: to a recall plant copied from that record, or,
    failing that, held against a silent plant whose source is that record
    (the boilerplate carrier for shared standard text)."""
    records, explained = [], set()
    coarse = [i for i, f in enumerate(fresh) if not is_passage_flag(f)]

    def record_of(plant: Plant) -> int | None:
        name = carrier if plant.type == "boilerplate" else plant.donor
        return library_ids.get(name)

    recall_sources = {record_of(p) for p in plants if p.type in RECALL_TYPES}
    for plant in plants:
        hits = [i for i, f in enumerate(fresh) if touches(plant, f, match)]
        sourced = [(i, *right_source(plant, fresh[i], library_ids, match)) for i in hits]
        named = [
            i for i in coarse if fresh[i]["detail"].get("matched_manuscript_id") == record_of(plant)
        ]
        if plant.type in RECALL_TYPES:
            explained.update(i for i, source, _ in sourced if source)
            explained.update(named)
            coarse_alarm = []
        else:
            coarse_alarm = [
                i
                for i in named
                if fresh[i]["detail"].get("matched_manuscript_id") not in recall_sources
            ]
        anchors = [page_of(fresh[i]["page_anchor"]) for i, _, found in sourced if found]
        records.append(
            {
                "type": plant.type,
                "page": plant.page,
                "donor": plant.donor,
                "donor_pages": plant.donor_pages,
                "cluster": plant.cluster,
                "words": len(plant.text.split()),
                "any_flag": bool(hits or coarse_alarm),
                "coarse_alarm": bool(coarse_alarm),
                "right_source": any(source for _, source, _ in sourced),
                "detected": any(found for _, _, found in sourced),
                # Where the finding sends the instructor, against the page the
                # copied text is on.
                "anchor_offsets": sorted({plant.page - a for a in anchors if a is not None}),
                "coarse_named": bool(named) if plant.type in RECALL_TYPES else False,
                "levels": sorted({str(fresh[i]["detail"].get("kind")) for i in hits + named}),
                **plant.extra,
            }
        )
    return records, explained


# ------------------------------------------------------------------ database


def _swap_db(url: str, name: str) -> str:
    return url.rsplit("/", 1)[0] + "/" + name


async def _admin(url: str, sql: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


def _prepare_database(config: dict) -> tuple[str, str]:
    """A fresh database for this run on the server named by the lane's
    environment variable (never a live-testing or production database)."""
    from alembic.config import Config

    from alembic import command
    from app.db import sqlalchemy_url

    server = os.environ.get(config["database_url_env"])
    if not server:
        raise RuntimeError(
            f"Set {config['database_url_env']} to a scratch Postgres server with pgvector "
            "(for example postgresql://veridical@127.0.0.1:5571/postgres)."
        )
    name = config["database_name"]
    asyncio.run(_admin(server, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    asyncio.run(_admin(server, f'CREATE DATABASE "{name}"'))
    url = _swap_db(server, name)
    backend = Path(__file__).resolve().parents[3] / "backend"
    alembic_config = Config(str(backend / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", sqlalchemy_url(url))
    command.upgrade(alembic_config, "head")
    return server, url


class Library:
    """The scratch database and the records in it."""

    def __init__(self, url: str, settings):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.db import sqlalchemy_url

        self.engine = create_async_engine(sqlalchemy_url(url))
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.settings = settings
        self.owners: dict[str, tuple[int, int, int]] = {}  # doc -> instructor, group, rubric
        self.ids: dict[str, int] = {}  # doc -> Library manuscript id
        self.passages: dict[str, list] = {}

    async def owner(self, doc: str) -> tuple[int, int, int]:
        from app.models.group import Group
        from app.models.instructor import Instructor
        from app.models.rubric import Rubric

        if doc not in self.owners:
            async with self.sessions() as session:
                instructor = Instructor(email=f"{doc}@f7-lane.local", display_name=doc)
                session.add(instructor)
                await session.flush()
                group = Group(
                    instructor_id=instructor.id,
                    name=f"Team {doc}",
                    name_normalized=f"team {doc}",
                )
                rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="f.pdf")
                session.add_all([group, rubric])
                await session.commit()
                self.owners[doc] = (instructor.id, group.id, rubric.id)
        return self.owners[doc]

    async def add(self, doc: str, extraction) -> int:
        """Enter `doc` the way an activation does: comparison rows, the
        indexing snapshot and the active status together."""
        from app.checks.reuse.embed import (
            compute_document_embeddings,
            compute_library_passage_embeddings,
        )
        from app.checks.reuse.store import (
            store_document_embeddings,
            store_passage_embeddings,
        )
        from app.ingest.analytical_source import bind_analytical_source
        from app.ingest.service import _write_raw_store, raw_store_path
        from app.library.passage_indexing import indexing_snapshot
        from app.models.enums import (
            LibraryEntrySource,
            LibraryEntryStatus,
            ManuscriptOrigin,
        )
        from app.models.library import LibraryEntry
        from app.models.manuscript import Manuscript

        instructor_id, group_id, _ = await self.owner(doc)
        content_hash = hashlib.sha256(extraction.model_dump_json().encode()).hexdigest()
        extraction = bind_analytical_source(extraction, content_hash)
        document = compute_document_embeddings(extraction, self.settings)
        inventory = compute_library_passage_embeddings(extraction, self.settings)
        async with self.sessions() as session:
            manuscript = Manuscript(
                instructor_id=instructor_id,
                group_label=f"Team {doc}",
                group_id=group_id,
                file_ref="",
                content_hash=content_hash,
                origin=ManuscriptOrigin.library_submission,
            )
            session.add(manuscript)
            await session.flush()
            _write_raw_store(extraction, raw_store_path(self.settings, manuscript.id))
            await store_document_embeddings(session, manuscript.id, document, commit=False)
            if inventory.passages:
                await store_passage_embeddings(
                    session,
                    manuscript.id,
                    inventory.passages,
                    self.settings,
                    commit=False,
                )
            session.add(
                LibraryEntry(
                    manuscript_id=manuscript.id,
                    source=LibraryEntrySource.library_submission,
                    status=LibraryEntryStatus.active,
                    added_at=datetime.now(UTC),
                    passage_indexing_snapshot=indexing_snapshot(
                        inventory, content_hash, document.model_id
                    ),
                )
            )
            await session.commit()
            self.ids[doc] = manuscript.id
            self.passages[doc] = inventory.passages
            return manuscript.id

    async def remove(self, doc: str) -> None:
        from app.library.service import remove_library_entry

        instructor_id, _, _ = self.owners[doc]
        async with self.sessions() as session:
            await remove_library_entry(session, instructor_id, self.ids[doc], self.settings)

    async def check(self, host: str, extraction, unread: list[int]) -> tuple[dict, list[dict]]:
        """One originality check of `extraction` (from `prepare`) as a new
        upload by the host's own instructor and group; the pages left unread
        are recorded on the result the way the integrity stage records them."""
        from sqlalchemy import select

        from app.checks.reuse.service import run_originality_reuse_check
        from app.ingest.service import _write_raw_store, raw_store_path
        from app.models.manuscript import Manuscript
        from app.models.run import CheckRun, Flag
        from app.pipeline.machine import _record_unread_pages

        instructor_id, group_id, rubric_id = await self.owner(host)
        async with self.sessions() as session:
            manuscript = Manuscript(
                instructor_id=instructor_id,
                group_label=f"Team {host}",
                group_id=group_id,
                file_ref="",
                content_hash=None,
            )
            session.add(manuscript)
            await session.flush()
            run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric_id)
            session.add(run)
            await session.commit()
            _write_raw_store(extraction, raw_store_path(self.settings, manuscript.id))
            result = await run_originality_reuse_check(
                session,
                manuscript.id,
                run.id,
                extraction,
                self.settings,
                instructor_id=instructor_id,
                content_hash=None,
            )
            await _record_unread_pages(session, run.id, result, unread)
            flags = (
                await session.scalars(select(Flag).where(Flag.check_result_id == result.id))
            ).all()
            return (
                {"outcome": result.outcome.value, **(result.detail or {})},
                [
                    {
                        "severity": str(f.severity.value),
                        "evidence_excerpt": f.evidence_excerpt,
                        "page_anchor": f.page_anchor,
                        "detail": f.detail or {},
                    }
                    for f in flags
                ],
            )


def source_scope(extraction, settings) -> list[int] | None:
    """The pages the integrity stage leaves unread for this host, from its
    own reading-order ledger: [] when every page is read, None when the
    product refuses the whole document (F7 then compares nothing)."""
    from app.checks.source_quality import unread_page_scope
    from app.ingest.analytical_source import bind_analytical_source
    from app.ingest.quality import reading_order_issues

    bound = bind_analytical_source(extraction, None)
    if not reading_order_issues(bound):
        return []
    return unread_page_scope(bound, settings.analysis_unread_pages_max_ratio)


def prepare(extraction, unread: list[int]):
    """The analysis copy the integrity stage would check, with the
    reading-order ledger cleared. The ledger records the PDF's exact source
    lines, so any planted line would fail its provenance check for a reason
    that has nothing to do with matching; it is cleared on the twin as well
    as the copy, and the pages it marks unread stay left out (same precedent
    as the F5 lane)."""
    from app.checks.source_quality import without_unread_pages
    from app.ingest.analytical_source import bind_analytical_source

    copy = bind_analytical_source(extraction.model_copy(deep=True), None)
    if unread:
        copy = without_unread_pages(copy, unread)
    copy.reading_order = None
    return copy


def _excluded_pages(extraction, settings) -> set[int]:
    """Pages where a plant would not be compared at all: pages the integrity
    stage leaves unread, and pages inside the reference list or an indented
    quotation as the product tags them."""
    from app.checks.reuse.embed import compute_passage_embeddings

    unread = set(source_scope(extraction, settings) or [])
    return unread | {
        p.page
        for p in compute_passage_embeddings(extraction, settings)
        if p.page is not None and (p.is_reference_list or p.is_block_quote)
    }


def _landing(plant: Plant, extraction, settings, donor_passages, size: int) -> dict:
    """The passage the plant was chunked into and its best similarity to any
    passage of the donor's Library record: why a plant was or was not found."""
    from app.checks.reuse.embed import compute_passage_embeddings
    from app.ml.embeddings import cosine_similarity

    passages = [p for p in compute_passage_embeddings(extraction, settings) if p.page is not None]
    near = [p for p in passages if abs(p.page - plant.page) <= 1]
    if not near:
        return {}
    best = max(near, key=lambda p: containment(plant.text, p.text, size))
    landing = {
        "passage_words": len(best.text.split()),
        "plant_share_of_passage": round(
            len(plant.text.split()) / max(1, len(best.text.split())), 2
        ),
        "passage_is_block_quote": best.is_block_quote,
        "passage_is_reference_list": best.is_reference_list,
    }
    if donor_passages:
        landing["best_similarity_to_donor"] = round(
            max(cosine_similarity(best.embedding, d.embedding) for d in donor_passages),
            3,
        )
    return landing


# ----------------------------------------------------------------------- run


async def _measure(ctx: Context, config: dict, url: str, settings) -> dict:
    docs = _documents(ctx, config)
    extractions = {name: load_extraction(path) for name, path in docs.items()}
    library = Library(url, settings)
    boilerplate = json.loads((ctx.golden / LANE / "boilerplate.json").read_text(encoding="utf-8"))
    try:
        # The carrier is the Library record that holds every shared standard text.
        carrier = extractions[config["boilerplate_carrier"]].model_copy(deep=True)
        carrier_pages = body_pages(carrier)
        for i, item in enumerate(boilerplate["items"]):
            _insert_at_page_end(carrier, carrier_pages[i % len(carrier_pages)], item["text"])
        for name in [*config["hosts"], *config["library_background"]]:
            await library.add(name, extractions[name])
        await library.add(config["boilerplate_carrier"], carrier)
        await library.add(
            config["removed_from_library"], extractions[config["removed_from_library"]]
        )
        await library.remove(config["removed_from_library"])

        excluded = {
            name: _excluded_pages(extractions[name], settings)
            for name in [
                *config["hosts"],
                config["outside_library"],
                config["removed_from_library"],
            ]
        }
        scopes = {host: source_scope(extractions[host], settings) for host in config["hosts"]}
        refused = sorted(host for host, scope in scopes.items() if scope is None)
        checked = [host for host in config["hosts"] if host not in refused]
        copies, requests = build_cases(ctx, excluded, checked_hosts=checked)
        (ctx.golden / LANE / "heavy_requests.json").write_text(
            json.dumps(requests, indent=1), encoding="utf-8"
        )
        twins = {}
        for host in checked:
            unread = scopes[host]
            detail, flags = await library.check(host, prepare(extractions[host], unread), unread)
            twins[host] = {"detail": detail, "flags": flags}
        sealed = config.get("holdout_sealed", True)
        records, findings, fresh_total, explained_total, unexplained = [], [], 0, 0, Counter()
        audited, banned = 0, []
        not_run = [c for c in copies if sealed and c["split"] == "holdout"]
        for case in copies:
            if sealed and case["split"] == "holdout":
                continue
            unread = scopes[case["host"]]
            extraction = prepare(extractions[case["host"]], unread)
            plants = place_plants(extraction, case, excluded[case["host"]], config)
            _, flags = await library.check(case["host"], extraction, unread)
            twin_keys = {flag_key(f) for f in twins[case["host"]]["flags"]}
            fresh = [f for f in flags if flag_key(f) not in twin_keys]
            outcomes, explained = score_copy(
                plants, fresh, library.ids, config["match"], carrier=config["boilerplate_carrier"]
            )
            for plant, outcome in zip(plants, outcomes, strict=True):
                if plant.type in RECALL_TYPES and not outcome["detected"]:
                    outcome["landing"] = _landing(
                        plant,
                        extraction,
                        settings,
                        library.passages.get(plant.donor, []),
                        config["match"]["shingle"],
                    )
                records.append({**outcome, "host": case["host"], "split": case["split"]})
            # Every new finding, with how much of each plant it quotes, so a
            # finding the matching rules did not credit can be read by hand.
            for flag in fresh:
                findings.append(
                    {
                        "copy": case["copy"],
                        "host": case["host"],
                        "page": page_of(flag["page_anchor"]),
                        "kind": flag["detail"].get("kind"),
                        "similarity": flag["detail"].get("similarity"),
                        "matched_manuscript_id": flag["detail"].get("matched_manuscript_id"),
                        "matched_page": flag["detail"].get("matched_page"),
                        "plants": [
                            {
                                "type": plant.type,
                                "page": plant.page,
                                "containment": round(
                                    containment(
                                        plant.text,
                                        flag["evidence_excerpt"],
                                        config["match"]["shingle"],
                                    ),
                                    3,
                                ),
                            }
                            for plant in plants
                        ],
                    }
                )
            touched = {
                i for i, f in enumerate(fresh) for p in plants if touches(p, f, config["match"])
            }
            fresh_total += len(fresh)
            explained_total += len(explained)
            for i in range(len(fresh)):
                if i not in explained:
                    unexplained["on a plant" if i in touched else "elsewhere"] += 1
            for flag in flags:
                audited += 1
                hits = banned_hits(
                    f"{flag['evidence_excerpt']} {flag['detail'].get('reason', '')}",
                    config["banned_terms"],
                )
                if hits:
                    banned.append({"host": case["host"], "terms": hits})
        coverage = {}
        for host in config["hosts"]:
            pages = body_pages(extractions[host])
            coverage[host] = {
                "body_pages": len(pages),
                "not_compared": len(pages) if host in refused else len(set(pages) & excluded[host]),
                "refused": host in refused,
            }
        return {
            "coverage": coverage,
            "copies": copies,
            "records": records,
            "findings": findings,
            "twins": twins,
            "library_ids": library.ids,
            "fresh_total": fresh_total,
            "explained_total": explained_total,
            "unexplained": dict(unexplained),
            "audited": audited,
            "banned": banned,
            "not_run": not_run,
            "requests": requests,
        }
    finally:
        await library.engine.dispose()


def _insert_at_page_end(extraction, page: int, text: str) -> None:
    index = max(i for i, b in enumerate(extraction.blocks) if b.page == page and not b.is_furniture)
    block = plant_block(extraction, index, text, _body_margin(extraction))
    extraction.blocks.insert(index + 1, block)


def twin_rows(twins: dict, library_ids: dict, verify: dict, size: int) -> tuple[list, list]:
    """Findings on the unplanted hosts. Nothing was planted, so each one is
    checked for real shared text: containment or a long common run."""
    names = {v: k for k, v in library_ids.items()}
    judged = []
    for host, twin in twins.items():
        for flag in twin["flags"]:
            if not is_passage_flag(flag):
                judged.append(
                    {
                        "host": host,
                        "kind": flag["detail"].get("kind"),
                        "confirmed": None,
                    }
                )
                continue
            own, other = (
                flag["evidence_excerpt"],
                flag["detail"].get("matched_text", ""),
            )
            share, run = containment(own, other, size), longest_common_run(own, other)
            judged.append(
                {
                    "host": host,
                    "page": page_of(flag["page_anchor"]),
                    "matched": names.get(flag["detail"].get("matched_manuscript_id")),
                    "matched_page": flag["detail"].get("matched_page"),
                    "similarity": flag["detail"].get("similarity"),
                    "kind": flag["detail"].get("kind"),
                    "containment": round(share, 3),
                    "longest_run": run,
                    "confirmed": share >= verify["containment_confirmed"]
                    or run >= verify["longest_run_confirmed"],
                    "own_text": own[:300],
                    "matched_text": other[:300],
                }
            )
    passage = [j for j in judged if j["confirmed"] is not None]
    rows = [
        rate_row(
            LANE,
            "natural findings confirmed as shared text",
            "finding on an unplanted host",
            sum(j["confirmed"] for j in passage),
            len(passage),
            note=(
                "Passage findings on the hosts with nothing planted, confirmed when word 5-gram "
                f"containment >= {verify['containment_confirmed']} or a common run >= "
                f"{verify['longest_run_confirmed']} words; an unconfirmed one is a likely "
                "false alarm (topic or genre only)."
            ),
        )
    ]
    return rows, judged


def metric_rows(measured: dict, config: dict) -> list[MetricRow]:
    rows = []
    splits = ["dev"] if config.get("holdout_sealed", True) else ["dev", "holdout"]
    records = measured["records"]
    for split in splits:
        mine = [r for r in records if r["split"] == split]
        for plant_type in RECALL_TYPES:
            group = [r for r in mine if r["type"] == plant_type]
            missed = Counter(r["host"] for r in group if not r["detected"])
            rows.append(
                rate_row(
                    LANE,
                    f"recall {plant_type}",
                    "plant",
                    sum(r["detected"] for r in group),
                    len(group),
                    split=split,
                    clusters=len({r["cluster"] for r in group}),
                    note=(
                        "Model-written paraphrase; not counted against F7 (" + FEATURE_CLAIM + ")"
                        if plant_type in NOT_CLAIMED
                        else "A passage finding quoting the plant, naming the donor's Library record "
                        "and showing the copied text from it."
                    ),
                    extra={
                        "any_flag_on_plant": sum(r["any_flag"] for r in group),
                        "right_source_any_page": sum(r["right_source"] for r in group),
                        **({"misses_by_host": dict(missed)} if missed else {}),
                    },
                )
            )
        found = [r for r in mine if r["type"] in RECALL_TYPES and r["detected"]]
        window = config["match"]["page_window"]
        rows.append(
            rate_row(
                LANE,
                "finding anchored within one page of the copied text",
                "detected plant",
                sum(any(abs(o) <= window for o in r["anchor_offsets"]) for r in found),
                len(found),
                split=split,
                note=(
                    "The page a passage finding names is its first block's page, which can come "
                    "before the copied text. Offsets seen (plant page minus anchor page): "
                    + ", ".join(
                        f"{o}: {n}"
                        for o, n in sorted(
                            Counter(min(r["anchor_offsets"], key=abs) for r in found).items()
                        )
                    )
                    + "."
                ),
            )
        )
        for plant_type in SILENT_TYPES:
            group = [r for r in mine if r["type"] == plant_type]
            alarms = sum(r["any_flag"] for r in group)
            rows.append(
                rate_row(
                    LANE,
                    f"silent {plant_type}",
                    "control plant",
                    len(group) - alarms,
                    len(group),
                    split=split,
                    clusters=len({r["cluster"] for r in group}),
                    note=(
                        "Any passage finding on the plant, or a whole-document or chapter finding "
                        "naming its source record, is a false alarm."
                    ),
                    extra={
                        "alarms_by_host": dict(Counter(r["host"] for r in group if r["any_flag"]))
                    },
                )
            )
    rows.append(
        rate_row(
            LANE,
            "precision of plant-induced findings",
            "new finding",
            measured["explained_total"],
            measured["fresh_total"],
            split="dev" if len(splits) == 1 else "all",
            note=(
                "New findings that name a recall plant's donor, over every finding the plants "
                f"caused. Unexplained: {measured['unexplained']}."
            ),
        )
    )
    coverage = measured["coverage"]
    rows.append(
        rate_row(
            LANE,
            "host body pages whose passages are compared",
            "body page",
            sum(c["body_pages"] - c["not_compared"] for c in coverage.values()),
            sum(c["body_pages"] for c in coverage.values()),
            note=(
                "A page is not compared when the integrity stage leaves it unread (reading order "
                "unresolved; a host with too many such pages is not compared at all and gets no "
                "plants) or the product tags its passages as reference list or indented "
                "quotation. Plants are never put there, so recall does not see this gap. "
                "Not compared, by host: "
                + ", ".join(
                    f"{h} {c['not_compared']}/{c['body_pages']}" for h, c in coverage.items()
                )
                + "."
            ),
            extra={"by_host": coverage},
        )
    )
    rows.append(
        rate_row(
            LANE,
            "wording free of accusation terms",
            "finding",
            measured["audited"] - len(measured["banned"]),
            measured["audited"],
            split="dev" if len(splits) == 1 else "all",
            note=f"Scanned for: {', '.join(config['banned_terms'])}.",
        )
    )
    return rows


def run(ctx: Context) -> tuple[list[MetricRow], dict]:
    config = _config(ctx)
    data_dir = Path(tempfile.mkdtemp(prefix="f7-lane-"))
    previous = {k: os.environ.get(k) for k in ("DATA_DIR", "STORAGE_BACKEND")}
    os.environ["DATA_DIR"], os.environ["STORAGE_BACKEND"] = str(data_dir), "local"
    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    server, url = _prepare_database(config)
    try:
        measured = asyncio.run(_measure(ctx, config, url, settings))
    finally:
        if not config.get("keep_database"):
            asyncio.run(
                _admin(
                    server,
                    f'DROP DATABASE IF EXISTS "{config["database_name"]}" WITH (FORCE)',
                )
            )
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
        import shutil

        shutil.rmtree(data_dir, ignore_errors=True)
    rows = metric_rows(measured, config)
    natural, judged = twin_rows(
        measured["twins"],
        measured["library_ids"],
        config["verify"],
        config["match"]["shingle"],
    )
    rows += natural
    rows += real_precision_rows(
        ctx.golden, LANE, tiebreak="the PM tie-break (model raters, not human-judged)"
    )
    manifest = [
        {
            **c,
            "plants": [{k: v for k, v in p.items() if k != "source_text"} for p in c["plants"]],
        }
        for c in measured["copies"]
    ]
    (ctx.golden / LANE / "manifest.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8"
    )
    warnings = []
    if measured["not_run"]:
        warnings.append(
            f"Holdout sealed: {len(measured['not_run'])} holdout copies "
            f"({sum(len(c['plants']) for c in measured['not_run'])} plants) were built and not run."
        )
    if measured["requests"]:
        warnings.append(
            f"{len(measured['requests'])} heavy paraphrases are still to be written "
            "(heavy_requests.json); those plants were left out."
        )
    if measured["banned"]:
        warnings.append(f"Accusation terms found in {len(measured['banned'])} findings.")
    return rows, {
        "seed": config["seed"],
        "feature_claim": FEATURE_CLAIM,
        "warnings": warnings,
        "library_ids": measured["library_ids"],
        "plants": measured["records"],
        "new_findings": measured["findings"],
        "natural_findings": judged,
        "twin_outcomes": {
            h: {k: t["detail"].get(k) for k in ("outcome", "coverage_reason", "comparison_ran")}
            for h, t in measured["twins"].items()
        },
        "banned": measured["banned"],
    }
