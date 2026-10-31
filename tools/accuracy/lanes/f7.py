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
RECALL_TYPES = ("verbatim", "short_verbatim", "light_paraphrase", "heavy_paraphrase")
QUOTE_TYPES = ("quoted_inline", "block_quote")
# Cut to quotation length: the unquoted short copy is the quotations' positive
# control, so a silent quotation can be told apart from a too-short one.
SHORT_TYPES = ("short_verbatim", *QUOTE_TYPES)
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
    "pasting text. Every missed plant records the passage it landed in and its similarity to "
    "the donor.",
    "Each copied excerpt is a contiguous run of the donor's text as extracted, cut at sentence "
    "ends; extraction line breaks inside it are joined with spaces.",
    "Recall depends strongly on the host, and there are five checked hosts, so every recall row "
    "is clustered by host and is indicative only (D-016). The comparable rows leave out plants "
    "whose host or donor has no passage inventory (BUG-622), which no match could find.",
    "F7 has no handling for inline quotation marks. A silent inline quotation means something "
    "only beside the short unquoted copies of the same length: if those are missed too, the "
    "quotation was silent because it was short, not because it was quoted.",
    "A whole-document or chapter finding is tied to a plant only through the supporting passage "
    "shown with it; one without that passage is left unexplained, never charged to a plant.",
    "Six hosts and ten active Library entries, all PDFs: a small Library, so natural matches "
    "between unrelated theses are rare and the natural false-positive figure is a floor. Word "
    "files are not covered.",
    "Light paraphrase uses a fixed synonym table and swaps two sentences; the embedding model "
    "ignores word order, so the swap tests nothing on its own.",
    "Heavy paraphrases are model-written, not written by a person. F7.2 claims no paraphrase "
    "detection: with the shared-wording rule (BUG-620) heavy paraphrase is not detected, because "
    "it shares as little wording as a same-topic match. The heavy rows show how many are found.",
    "A host is kept from matching its own Library copy through the product's same-group "
    "exclusion, so that rule is assumed here. Library entries here have a group while real "
    "Library submissions do not; checked copies have no content hash, so the same-file, "
    "resubmission and identical-copy paths are not exercised.",
    "Dev and holdout plants are cut from different donor page buckets and different shared "
    "texts. Hosts are shared by both halves, and the holdout's plant texts are stored in the "
    "frozen case file (it is sealed, not hidden).",
)
SILENT_NOTES = {
    "quoted_inline": (
        "F7 has no inline-quotation handling: read beside recall short_verbatim. Silence here "
        "shows quoting works only if short unquoted copies are found."
    ),
    "block_quote": (
        "Indented quotations are left out by the product's indentation rule; read beside "
        "recall short_verbatim, the same text unindented."
    ),
}
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


def shared_wording(own: str, other: str, config: dict) -> dict:
    """Word 5-gram containment and the longest common run between two
    passages, and whether either reaches the confirmation thresholds."""
    share = containment(own, other, config["match"]["shingle"])
    run = longest_common_run(own, other)
    verify = config["verify"]
    return {
        "containment": round(share, 3),
        "longest_run": run,
        "confirmed": share >= verify["containment_confirmed"]
        or run >= verify["longest_run_confirmed"],
    }


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


def split_of(key: str, config: dict) -> str:
    """Dev or holdout for a piece of source text, fixed by the seed."""
    digest = hashlib.sha256(f"{config['seed']}|{key}".encode()).digest()
    return "holdout" if digest[0] / 256 < config["split"]["holdout_share"] else "dev"


def page_split(doc: str, page: int, config: dict, size: int | None = None) -> str:
    """Dev or holdout for a donor page. Pages go in buckets of `size` pages
    (`split.bucket_pages` by default), each bucket to one side, so a holdout
    plant is never cut from text a dev plant also uses."""
    return split_of(f"{doc}|{page // (size or config['split']['bucket_pages'])}", config)


def bucket_size(pages: list[int], config: dict) -> int:
    """`split.bucket_pages`, made smaller for a short document so it still has
    at least `split.min_buckets` buckets to share between the two sides."""
    span = (max(pages) - min(pages) + 1) if pages else 1
    return max(1, min(config["split"]["bucket_pages"], span // config["split"]["min_buckets"]))


def make_excerpt(
    doc: str, extraction, allowed: set[int], low: int, high: int, rng, config: dict
) -> Excerpt:
    """A run of the donor's own text, exactly as it reads: consecutive
    non-furniture blocks in document order, all on `allowed` pages, starting
    where a sentence starts and ending where one ends, `low` to `high` words
    (plus `excerpt.slack_words`)."""
    blocks = [b for b in extraction.blocks if not b.is_furniture and b.text.strip()]
    slack, attempts = config["excerpt"]["slack_words"], config["excerpt"]["attempts"]
    # Headings are not prose: an excerpt never starts on one or runs past one.
    headings = {" ".join(node.title.split()).casefold() for node in _section_nodes(extraction)}

    def heading(block) -> bool:
        return " ".join(block.text.split()).casefold() in headings

    def starts_sentence(i: int) -> bool:
        first = blocks[i].text.lstrip()[:1]
        before = blocks[i - 1].text.rstrip()[-1:] if i else "."
        return (
            first.isupper()
            and before in ".!?"
            and not heading(blocks[i])
            and len(blocks[i].text.split()) >= config["excerpt"]["start_words"]
        )

    starts = [i for i in range(len(blocks)) if blocks[i].page in allowed and starts_sentence(i)]
    if not starts:
        raise ValueError(f"No sentence start on the allowed pages of {doc}")
    for _ in range(attempts):
        start, target = rng.choice(starts), rng.randint(low, high)
        taken, words = [], 0
        for block in blocks[start:]:
            if block.page not in allowed or (taken and heading(block)):
                break
            taken.append(block)
            words += len(block.text.split())
            if words >= high + slack:
                break
        picked, count = [], 0
        for sentence in sentences(" ".join(b.text for b in taken)):
            if count >= target:
                break
            picked.append(sentence)
            count += len(sentence.split())
        text = " ".join(picked)
        if low <= count <= high + slack and len(picked) >= 2 and text[-1:] in ".!?":
            # The pages the excerpt really covers, not every block read ahead.
            pages, used = [], 0
            for block in taken:
                if used >= len(text):
                    break
                pages.append(block.page)
                used += len(" ".join(block.text.split())) + 1
            return Excerpt(doc, text, sorted(set(pages)))
    raise ValueError(f"No {low}-{high} word excerpt could be cut from {doc}")


def _section_nodes(extraction) -> list:
    tree = getattr(extraction, "section_tree", None)
    found, stack = [], list(getattr(tree, "nodes", None) or [])
    while stack:
        node = stack.pop()
        found.append(node)
        stack.extend(getattr(node, "children", None) or [])
    return found


def _config(ctx: Context) -> dict:
    config = ctx.lane_config(LANE)
    if not config:
        raise FileNotFoundError(f"{ctx.golden / LANE / 'config.json'} is required for the F7 lane")
    config.setdefault("seed", ctx.seed)
    return config


def _data(ctx: Context, config: dict, name: str):
    return json.loads((ctx.golden / LANE / config["files"][name]).read_text(encoding="utf-8"))


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


def _body_margin(extraction, config: dict) -> float:
    """The left edge most paragraph lines start at."""
    lefts = [
        round(b.bbox[0])
        for b in extraction.blocks
        if not b.is_furniture
        and b.bbox is not None
        and len(b.text.split()) >= config["excerpt"]["paragraph_words"]
    ]
    return float(Counter(lefts).most_common(1)[0][0]) if lefts else config["geometry"]["margin"]


def build_cases(
    ctx: Context,
    excluded_pages: dict[str, set[int]],
    *,
    checked_hosts: list[str] | None = None,
) -> tuple[list[dict], dict]:
    """The planted copies, reproducible from the seed. A copy's split is
    drawn first; its plants are then cut only from donor pages (and shared
    texts) on that side, so dev and holdout never share source text."""
    config = _config(ctx)
    rng = random.Random(config["seed"])
    docs = _documents(ctx, config)
    extractions = {name: load_extraction(path) for name, path in docs.items()}
    table = _data(ctx, config, "synonyms")["table"]
    items = _data(ctx, config, "boilerplate")["items"]
    donors = config["hosts"]
    hosts = checked_hosts if checked_hosts is not None else donors
    words = config["excerpt_words"]
    # A pair seen while designing the lane is dev only, in either direction.
    dev_pairs = {frozenset(pair) for pair in config.get("dev_only_pairs", [])}
    ordered = sorted(
        items,
        key=lambda i: hashlib.sha256(f"{config['seed']}|boilerplate|{i['id']}".encode()).digest(),
    )
    half = round(len(ordered) * (1 - config["split"]["holdout_share"]))
    shared = {"dev": ordered[:half], "holdout": ordered[half:]}
    if not all(shared.values()):
        raise ValueError("Each split needs at least one shared standard text; add items")

    def allowed(doc: str, split: str) -> set[int]:
        body = body_pages(extractions[doc])
        size = bucket_size(body, config)
        pages = set(body) - excluded_pages.get(doc, set())
        side = {p for p in pages if page_split(doc, p, config, size) == split}
        if not side:
            raise ValueError(f"{doc} has no {split} pages; lower split.bucket_pages")
        return side

    def apart(excerpt, used) -> bool:
        # Two plants on one copy that share donor text would quote each other,
        # so a finding on one would also seem to be about the other.
        return all(
            doc != excerpt.doc or not overlap(excerpt.text, text, config["match"]["shingle"])
            for doc, text in used
        )

    queue = [t for t, n in config["per_type"].items() for _ in range(n)]
    rng.shuffle(queue)
    copies, requests = [], {}
    while queue:
        host = hosts[len(copies) % len(hosts)]
        types = [queue.pop() for _ in range(min(config["plants_per_copy"], len(queue)))]
        split = "holdout" if rng.random() < config["split"]["holdout_share"] else "dev"
        others = [
            h
            for h in donors
            if h != host and not (split == "holdout" and frozenset((host, h)) in dev_pairs)
        ]
        plants, used = [], []
        for plant_type in types:
            donor = None
            if plant_type in (*RECALL_TYPES, *QUOTE_TYPES):
                donor = rng.choice(others)
            elif plant_type == "not_in_library":
                donor = config["outside_library"]
            elif plant_type == "removed_from_library":
                donor = config["removed_from_library"]
            plant = {"type": plant_type, "donor": donor}
            if plant_type == "boilerplate":
                item = rng.choice(shared[split])
                plant.update(text=item["text"], cluster=f"boilerplate:{item['id']}")
                plant["source_text"] = item["text"]
                plants.append(plant)
                continue
            low, high = words["short" if plant_type in SHORT_TYPES else "long"]
            for _ in range(config["excerpt"]["attempts"]):
                excerpt = make_excerpt(
                    donor, extractions[donor], allowed(donor, split), low, high, rng, config
                )
                if apart(excerpt, used):
                    break
            else:
                raise ValueError(f"No excerpt from {donor} apart from the copy's other plants")
            used.append((excerpt.doc, excerpt.text))
            plant.update(donor_pages=excerpt.pages, cluster=f"{donor}:{excerpt.key}")
            author, year = rng.choice(AUTHORS), rng.randint(*config["citation_years"])
            cited_page = rng.randint(*config["citation_pages"])
            if plant_type == "light_paraphrase":
                text, achieved = light_paraphrase(
                    excerpt.text, table, config["light_replace_share"], rng
                )
                plant.update(text=text, extra={"replaced_share": round(achieved, 3)})
            elif plant_type == "heavy_paraphrase":
                plant["key"] = f"heavy:{excerpt.key}"
                plant["text"] = None
            elif plant_type == "quoted_inline":
                plant["text"] = rng.choice(QUOTE_FRAMES).format(
                    author=author, year=year, text=excerpt.text, page=cited_page
                )
            elif plant_type == "block_quote":
                plant["text"] = f"{excerpt.text} ({author}, {year}, p. {cited_page})"
            else:
                plant["text"] = excerpt.text
            plant["source_text"] = excerpt.text
            plants.append(plant)
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


def plant_block(extraction, index: int, text: str, left: float, config: dict):
    """A one-line block with its own source identity, just below block
    `index` on the same page, so the line-inventory checks accept it."""
    from app.ingest.schemas import SourceLineSpan, TextBlock

    anchor = extraction.blocks[index]
    top, bottom = (anchor.bbox[1], anchor.bbox[3]) if anchor.bbox else (0.0, 12.0)
    height = max(bottom - top, 1.0)
    geometry = config["geometry"]
    right = max(
        anchor.bbox[2] if anchor.bbox else left + geometry["width"], left + geometry["min_width"]
    )
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


def assign_pages(extraction, case: dict, excluded: set[int], config: dict) -> None:
    """Choose each plant's page once, when the set is frozen: body pages the
    product compared at that time, at least `page_gap` apart."""
    rng = random.Random(case["seed"])
    candidates = [p for p in body_pages(extraction) if p not in excluded]
    pages = spaced_pages(rng, candidates, len(case["plants"]), config["page_gap"])
    for page, plant in zip(pages, case["plants"], strict=True):
        plant["page"] = page


def place_plants(extraction, case: dict, config: dict) -> list[Plant]:
    """Insert the copy's plants at their frozen pages, each at the body
    text's left margin (an indent would make the block read as a quotation),
    except the block quotes. A plant whose page this version no longer reads
    is kept, marked not placed, so it counts against the version."""
    from app.ingest.source_slices import line_inventory_valid

    margin = _body_margin(extraction, config)
    placed = []
    for plant in case["plants"]:
        if not plant.get("text"):
            continue
        page = plant["page"]
        on_page = [
            i for i, b in enumerate(extraction.blocks) if b.page == page and not b.is_furniture
        ]
        if on_page:
            left = (
                chapter_margin(extraction, page, config) + config["block_quote_indent_points"]
                if plant["type"] == "block_quote"
                else margin
            )
            block = plant_block(extraction, max(on_page), plant["text"], left, config)
            assert line_inventory_valid(block), f"planted line inventory broken on page {page}"
            extraction.blocks.insert(max(on_page) + 1, block)
        placed.append(
            Plant(
                type=plant["type"],
                page=page,
                text=plant["text"],
                donor=plant.get("donor"),
                donor_pages=plant.get("donor_pages", []),
                cluster=plant.get("cluster", ""),
                extra={**plant.get("extra", {}), **({} if on_page else {"not_placed": True})},
                source_text=plant.get("source_text") or plant["text"],
            )
        )
    return placed


def frozen_cases(ctx: Context, config: dict, build) -> tuple[list[dict], dict, bool]:
    """The case set every version is measured on. Built once, with each
    plant's page, and kept in `<golden>/f7/<cases_file>`; delete the file to
    build a new set. Heavy paraphrases written later are filled in by key."""
    path = ctx.golden / LANE / config["cases_file"]
    built = not path.exists()
    if built:
        path.write_text(json.dumps(build(), indent=1), encoding="utf-8")
    copies = json.loads(path.read_text(encoding="utf-8"))
    heavy_path = ctx.golden / LANE / config["files"]["heavy"]
    heavy = json.loads(heavy_path.read_text(encoding="utf-8")) if heavy_path.exists() else {}
    requests = {}
    for case in copies:
        for plant in case["plants"]:
            if plant["type"] == "heavy_paraphrase" and not plant.get("text"):
                plant["text"] = heavy.get(plant["key"])
                if plant["text"] is None:
                    requests[plant["key"]] = {
                        "source": plant["source_text"],
                        "words": len(plant["source_text"].split()),
                    }
    return copies, requests, built


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
    page = page_of(flag["page_anchor"])
    if page is not None and not (
        plant.page - match["max_anchor_lag"] <= page <= plant.page + match["page_window"]
    ):
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


def score_copy(plants: list[Plant], fresh: list[dict], library_ids, match) -> tuple[list, set]:
    """Per-plant outcomes, and which fresh findings a recall plant explains.

    A whole-document or chapter finding quotes no passage of its own; it is
    tied to a plant only through the supporting passage the product shows
    with it, the same overlap rule as a passage finding. Without one it is
    left unexplained rather than charged to any plant: a plant can push a
    section that already sat near the threshold over it, whatever the plant
    says, so the record it names does not show which plant moved it."""
    records, explained = [], set()
    coarse = [i for i, f in enumerate(fresh) if not is_passage_flag(f)]

    def supported_by(plant: Plant, flag: dict) -> bool:
        shown = (flag["detail"].get("supporting_passage") or {}).get("own_text") or ""
        return overlap(plant.text, shown, match["shingle"]) >= match["containment_min"]

    for plant in plants:
        hits = [i for i, f in enumerate(fresh) if touches(plant, f, match)]
        sourced = [(i, *right_source(plant, fresh[i], library_ids, match)) for i in hits]
        backed = [i for i in coarse if supported_by(plant, fresh[i])]
        if plant.type in RECALL_TYPES:
            explained.update(i for i, source, _ in sourced if source)
            explained.update(
                i
                for i in backed
                if fresh[i]["detail"].get("matched_manuscript_id") == library_ids.get(plant.donor)
            )
        anchors = [page_of(fresh[i]["page_anchor"]) for i, _, found in sourced if found]
        records.append(
            {
                "type": plant.type,
                "page": plant.page,
                "donor": plant.donor,
                "donor_pages": plant.donor_pages,
                "cluster": plant.cluster,
                "words": len(plant.text.split()),
                "any_flag": bool(hits or (backed and plant.type not in RECALL_TYPES)),
                "coarse_alarm": bool(backed) and plant.type not in RECALL_TYPES,
                "right_source": any(source for _, source, _ in sourced),
                "detected": any(found for _, _, found in sourced),
                # Where the finding sends the instructor, against the page the
                # copied text is on.
                "anchor_offsets": sorted({plant.page - a for a in anchors if a is not None}),
                # Findings naming the donor's record but showing another
                # passage of it: explained, but the evidence is not the copy.
                "other_passage_findings": [
                    i for i, source, found in sourced if source and not found
                ],
                "levels": sorted({str(fresh[i]["detail"].get("kind")) for i in hits + backed}),
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


def database_name(config: dict) -> str:
    """This run's scratch database: the configured name and the process id,
    so two runs on one server never drop each other's database."""
    return f"{config['database_name']}_{os.getpid()}"


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
    name = database_name(config)
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


def compared_pages(extraction, settings) -> set[int]:
    """Pages of the analysis copy that passage matching can reach: none when
    the passage inventory is refused; otherwise pages with a body block
    outside the reference-list and indented-quotation tags. A passage is
    tagged when any block in it is, so this can overstate slightly."""
    from app.checks.reuse.embed import compute_passage_embeddings
    from app.ingest.blockquotes import block_quote_block_ids
    from app.ingest.patterns import load_patterns
    from app.ingest.references import non_reference_blocks

    if not compute_passage_embeddings(extraction, settings):
        return set()
    patterns = load_patterns(settings.ingest_patterns_file)
    body = {id(b) for b in non_reference_blocks(extraction, patterns, ambiguous_entries_ok=True)}
    quotes = block_quote_block_ids(extraction, settings)
    return {
        b.page
        for b in extraction.blocks
        if not b.is_furniture
        and b.page is not None
        and b.text.strip()
        and id(b) in body
        and id(b) not in quotes
    }


def _excluded_pages(extraction, settings) -> set[int]:
    """Pages a plant must not go on: pages the integrity stage leaves unread,
    and pages holding text the product tags as reference list or indented
    quotation. Taken from the product's own selection rules, not from passage
    tags, so a thesis whose passage inventory is refused today still has its
    bibliography and appendix kept free of plants."""
    from app.ingest.blockquotes import block_quote_block_ids
    from app.ingest.patterns import load_patterns
    from app.ingest.references import non_reference_blocks

    patterns = load_patterns(settings.ingest_patterns_file)
    body = {id(b) for b in non_reference_blocks(extraction, patterns, ambiguous_entries_ok=True)}
    quotes = block_quote_block_ids(extraction, settings)
    tagged = {
        b.page
        for b in extraction.blocks
        if not b.is_furniture
        and b.page is not None
        and b.text.strip()
        and (id(b) not in body or id(b) in quotes)
    }
    return set(source_scope(extraction, settings) or []) | tagged


def chapter_margin(extraction, page: int, config: dict) -> float:
    """The body margin the product's indentation rule measures for the
    chapter holding `page`: the most common left edge in that chapter
    (`app/ingest/blockquotes.py`), or the document's when there are no
    chapters."""
    from app.ingest.blockquotes import _chapter_spans

    for start, end in _chapter_spans(extraction):
        if start is not None and start <= page and (end is None or page < end):
            lefts = [
                round(b.bbox[0])
                for b in extraction.blocks
                if not b.is_furniture
                and b.bbox is not None
                and b.page is not None
                and start <= b.page
                and (end is None or b.page < end)
            ]
            if lefts:
                return float(Counter(lefts).most_common(1)[0][0])
    return _body_margin(extraction, config)


def _landing(plant: Plant, passages: list, donor_passages, match: dict) -> dict:
    """The passage the plant was chunked into, how the product tagged it, and
    its best similarity to any passage of the donor's Library record: why a
    plant was or was not found, and whether it was compared at all."""
    from app.ml.embeddings import cosine_similarity

    near = [
        p
        for p in passages
        if plant.page - match["max_anchor_lag"] <= p.page <= plant.page + match["page_window"]
    ]
    if not near:
        return {}
    best = max(near, key=lambda p: overlap(plant.text, p.text, match["shingle"]))
    if overlap(plant.text, best.text, match["shingle"]) < match["containment_min"]:
        return {}
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
    boilerplate = _data(ctx, config, "boilerplate")
    try:
        # The carrier is the Library record that holds every shared standard text.
        carrier = extractions[config["boilerplate_carrier"]].model_copy(deep=True)
        carrier_pages = body_pages(carrier)
        for i, item in enumerate(boilerplate["items"]):
            _insert_at_page_end(
                carrier, carrier_pages[i % len(carrier_pages)], item["text"], config
            )
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

        def build():
            cases, _ = build_cases(ctx, excluded, checked_hosts=checked)
            for case in cases:
                assign_pages(extractions[case["host"]], case, excluded[case["host"]], config)
            return cases

        copies, requests, built = frozen_cases(ctx, config, build)
        if requests:
            (ctx.golden / LANE / "heavy_requests.json").write_text(
                json.dumps(requests, indent=1), encoding="utf-8"
            )
        twins = {}
        for host in checked:
            unread = scopes[host]
            detail, flags = await library.check(host, prepare(extractions[host], unread), unread)
            twins[host] = {"detail": detail, "flags": flags}
        from app.checks.reuse.embed import compute_passage_embeddings

        inventory = {
            host: bool(
                compute_passage_embeddings(prepare(extractions[host], scopes[host]), settings)
            )
            for host in checked
        }

        def source_has_passages(plant: Plant) -> bool:
            """Whether the text the plant came from is searchable passage by
            passage. Copies and quotations need their donor's passages, shared
            text the carrier's; text outside the Library or removed from it
            has none by design, which is what those controls test."""
            if plant.type in ("not_in_library", "removed_from_library"):
                return True
            name = config["boilerplate_carrier"] if plant.type == "boilerplate" else plant.donor
            return bool(library.passages.get(name))

        sealed = config.get("holdout_sealed", True)
        twin_flags = [flag for twin in twins.values() for flag in twin["flags"]]
        records, findings, fresh_total, explained_total, unexplained = [], [], 0, 0, Counter()
        other_passage, hosts_run = 0, set()
        audited, banned = 0, []
        not_run = [c for c in copies if sealed and c["split"] == "holdout"]
        first_run = next((c for c in copies if not (sealed and c["split"] == "holdout")), None)
        for case in copies:
            if sealed and case["split"] == "holdout":
                continue
            unread = scopes[case["host"]]
            extraction = prepare(extractions[case["host"]], unread)
            plants = place_plants(extraction, case, config)
            # Where each plant landed under this version, and whether the
            # product compares that passage at all (it never matches text it
            # tags as reference list or indented quotation).
            landed_in = [
                p for p in compute_passage_embeddings(extraction, settings) if p.page is not None
            ]
            landings = [
                _landing(plant, landed_in, library.passages.get(plant.donor, []), config["match"])
                for plant in plants
            ]
            _, flags = await library.check(case["host"], extraction, unread)
            twin_keys = {flag_key(f) for f in twins[case["host"]]["flags"]}
            fresh = [f for f in flags if flag_key(f) not in twin_keys]
            outcomes, explained = score_copy(plants, fresh, library.ids, config["match"])
            hosts_run.add(case["host"])
            other_passage += len({i for o in outcomes for i in o.pop("other_passage_findings")})
            for plant, outcome, landing in zip(plants, outcomes, landings, strict=True):
                if plant.type in RECALL_TYPES and not outcome["detected"]:
                    outcome["landing"] = landing
                # The product tags a passage it does not match. A block quote
                # is meant to be tagged; any other plant so tagged was not
                # compared, whatever the version does with it.
                tagged_quote = bool(landing.get("passage_is_block_quote"))
                tagged_reference = bool(landing.get("passage_is_reference_list"))
                records.append(
                    {
                        **outcome,
                        "host": case["host"],
                        "split": case["split"],
                        "landed": bool(landing),
                        "tagged_quote": tagged_quote,
                        "tagged_reference": tagged_reference,
                        # Both sides have passages and the plant's passage is
                        # one the product compares, so a miss is a real miss
                        # and not a coverage hole (BUG-622 and the tags).
                        "comparable": inventory[case["host"]]
                        and source_has_passages(plant)
                        and bool(landing)
                        and not tagged_reference
                        and (plant.type == "block_quote" or not tagged_quote),
                    }
                )
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
                        # Whether the two passages share wording at all, so a
                        # finding no plant explains can be told from real shared
                        # text (yardstick 02 thresholds in `verify`).
                        **(
                            {
                                "shared_wording": shared_wording(
                                    flag["evidence_excerpt"],
                                    flag["detail"].get("matched_text") or "",
                                    config,
                                )
                            }
                            if is_passage_flag(flag)
                            else {}
                        ),
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
            for flag in flags + (twin_flags if case is first_run else []):
                audited += 1
                # Only the product's own words: a passage finding's excerpt is
                # the thesis's text, which may use any word.
                wording = flag["detail"].get("reason", "")
                if not is_passage_flag(flag):
                    wording += " " + flag["evidence_excerpt"]
                hits = banned_hits(wording, config["banned_terms"])
                if hits:
                    banned.append({"host": case["host"], "terms": hits})
        coverage = {}
        for host in config["hosts"]:
            pages = body_pages(extractions[host])
            compared = (
                set()
                if host in refused
                else compared_pages(prepare(extractions[host], scopes[host]), settings)
            )
            coverage[host] = {
                "body_pages": len(pages),
                "not_compared": len(set(pages) - compared),
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
            "explained_other_passage": other_passage,
            "hosts_run": len(hosts_run),
            "unexplained": dict(unexplained),
            "audited": audited,
            "banned": banned,
            "not_run": not_run,
            "requests": requests,
            "cases_built_this_run": built,
            "cases_sha256": hashlib.sha256(
                (ctx.golden / LANE / config["cases_file"]).read_bytes()
            ).hexdigest(),
            "heavy_missing": sum(
                1
                for c in copies
                for pl in c["plants"]
                if pl["type"] == "heavy_paraphrase" and not pl.get("text")
            ),
        }
    finally:
        await library.engine.dispose()


def _insert_at_page_end(extraction, page: int, text: str, config: dict) -> None:
    index = max(i for i, b in enumerate(extraction.blocks) if b.page == page and not b.is_furniture)
    block = plant_block(extraction, index, text, _body_margin(extraction, config), config)
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
    passage, seen = [], set()
    for item in judged:
        if item["confirmed"] is None:
            continue
        pair = frozenset(
            ((item["host"], item.get("page")), (item.get("matched"), item.get("matched_page")))
        )
        if pair not in seen:
            seen.add(pair)
            passage.append(item)
    rows = [
        rate_row(
            LANE,
            "natural findings confirmed as shared text",
            "distinct pair on unplanted hosts",
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
            every = [r for r in mine if r["type"] == plant_type]
            for scope, group in (
                ("", every),
                (" (comparable)", [r for r in every if r.get("comparable")]),
            ):
                missed = Counter(r["host"] for r in group if not r["detected"])
                note = (
                    "Model-written paraphrase; not counted against F7 (" + FEATURE_CLAIM + ")"
                    if plant_type in NOT_CLAIMED
                    else "A passage finding quoting the plant, naming the donor's Library "
                    "record and showing the copied text from it."
                )
                if scope:
                    note += " Only plants whose host and donor both have passages."
                if plant_type == "short_verbatim":
                    note += " The quotation controls' positive control (same length, unquoted)."
                rows.append(
                    rate_row(
                        LANE,
                        f"recall {plant_type}{scope}",
                        "plant",
                        sum(r["detected"] for r in group),
                        len(group),
                        split=split,
                        # Detection depends on the host far more than on the
                        # excerpt, so the host is the cluster (D-016).
                        clusters=len({r["host"] for r in group}),
                        note=note,
                        extra={
                            "any_flag_on_plant": sum(r["any_flag"] for r in group),
                            "right_source_any_page": sum(r["right_source"] for r in group),
                            "not_placed": sum(bool(r.get("not_placed")) for r in group),
                            "excerpts": len({r["cluster"] for r in group}),
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
                clusters=len({r["host"] for r in found}),
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
            # A control this version does not read cannot show silence.
            placed = [r for r in mine if r["type"] == plant_type and not r.get("not_placed")]
            untagged = 0
            if plant_type == "block_quote":
                # The control tests the product's indentation rule; a plant the
                # rule did not tag is reported, not counted as a pass.
                untagged = sum(not r["tagged_quote"] for r in placed)
                placed = [r for r in placed if r["tagged_quote"]]
            note = SILENT_NOTES.get(
                plant_type,
                "Any passage finding on the plant, or a whole-document or chapter finding "
                "whose supporting passage is the plant, is a false alarm.",
            )
            if untagged:
                note += f" {untagged} placed block quotes the product did not tag are left out."
            for scope, group in (
                ("", placed),
                (" (comparable)", [r for r in placed if r.get("comparable")]),
            ):
                alarms = sum(r["any_flag"] for r in group)
                rows.append(
                    rate_row(
                        LANE,
                        f"silent {plant_type}{scope}",
                        "control plant",
                        len(group) - alarms,
                        len(group),
                        split=split,
                        clusters=len({r["host"] for r in group}),
                        note=note
                        + (
                            " Only controls the check could have matched: host and source have "
                            "passages and the plant's passage is compared."
                            if scope
                            else ""
                        ),
                        extra={
                            "alarms_by_host": dict(
                                Counter(r["host"] for r in group if r["any_flag"])
                            ),
                            "distinct_texts": len({r["cluster"] for r in group}),
                            "not_placed": sum(
                                1 for r in mine if r["type"] == plant_type and r.get("not_placed")
                            ),
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
            clusters=measured["hosts_run"],
            note=(
                "New findings that name a recall plant's donor, over every finding the plants "
                f"caused. Unexplained: {measured['unexplained']}. Of the explained, "
                f"{measured['explained_other_passage']} name the right record but show a "
                "different passage of it."
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
            clusters=len(coverage),
            note=(
                "A page is compared when passage matching can reach it: not left unread by the "
                "integrity stage (a host with too many unread pages is not compared at all and "
                "gets no plants), the thesis has a passage inventory, and a body block on it is "
                "outside the reference-list and indented-quotation tags. Plants on pages a "
                "version does not compare count as misses in the recall rows and are left out "
                "of the comparable rows. "
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
                    f'DROP DATABASE IF EXISTS "{database_name(config)}" WITH (FORCE)',
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
    warnings = []
    if measured["not_run"]:
        warnings.append(
            f"Holdout sealed: {len(measured['not_run'])} holdout copies "
            f"({sum(len(c['plants']) for c in measured['not_run'])} plants) were built and not run."
        )
    if measured["cases_built_this_run"]:
        warnings.append(
            f"The case set was built in this run and frozen in {config['cases_file']}; later "
            "runs measure the same plants."
        )
    warnings.append(
        f"Case set {config['cases_file']} sha256 {measured['cases_sha256'][:12]}; lane seed "
        f"{config['seed']} (the seed in the header is the harness default, not the lane's)."
    )
    unplaced = sum(1 for r in measured["records"] if r.get("not_placed"))
    if unplaced:
        warnings.append(
            f"{unplaced} plants were not placed: their frozen page is not read in this version "
            "(recall plants count as missed; controls are left out of the silent rows)."
        )
    if measured["requests"]:
        warnings.append(
            f"{measured['heavy_missing']} heavy-paraphrase plants still lack their text "
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
