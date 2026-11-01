"""Standard wording that many theses share legitimately (BUG-637).

A standard's definition (ISO/IEC 25010 usability), a Likert scale legend or a
school's fixed title-page statement is the same in every thesis that uses
it, so it shares its wording by definition and the shared-wording rule
(`lexical.py`) cannot tell it apart from copying. Wording that lies inside a
listed standard text is left out of that comparison, so copied prose around
a standard definition is still compared and the definition alone is not.

The lists are data, built by `tools/build_standard_texts.py` from source
texts kept outside the repository. Each holds hashes of a text's word
shingles, with its name and source. Two lists are read:

- the public list bundled with the code (`data/standard_texts.json`), with
  freely shareable texts only; it is always loaded;
- an optional deployment list (`reuse_standard_texts_file`), for texts whose
  wording may not be published, such as ISO definitions. It adds to the
  public list and never replaces it. A deployment list that cannot be read
  is reported and skipped, so a misconfigured server still runs the check.

A run of words counts as standard text only when it is long enough
(`min_run_words`) for the match to be the text itself, not a common phrase.
"""

import hashlib
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# The word `lexical.words` counts, matched on the original text so that
# offsets stay valid.
_WORD = re.compile(r"[a-z]+(?:[-'][a-z]+)*", re.IGNORECASE)


def shingle_hash(words: Sequence[str]) -> str:
    """The stored form of one shingle: lower-cased words joined by single
    spaces, hashed. The build script and the check must agree on this."""
    return hashlib.blake2b(" ".join(words).encode("utf-8"), digest_size=8).hexdigest()


@dataclass(frozen=True)
class StandardTexts:
    shingle_words: int
    min_run_words: int
    names: tuple[str, ...]
    entries_by_hash: dict[str, tuple[int, ...]]
    public_entries: int
    deployment_entries: int
    # "not_configured", "loaded", or why it was skipped: "unreadable",
    # "malformed", "incompatible" (built with another shingle size or run length).
    deployment_status: str
    # First characters of the loaded deployment file's sha256.
    deployment_sha256: str | None


@dataclass(frozen=True)
class StandardSpan:
    start: int
    end: int
    # Each standard the run is named after, with the part of the run it
    # covers: (name, start, end).
    parts: tuple[tuple[str, int, int], ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted({name for name, _, _ in self.parts}))


def _well_formed(raw: object) -> bool:
    """A list the check can use as it is: anything else is skipped whole."""
    return (
        isinstance(raw, dict)
        and isinstance(raw.get("shingle_words"), int)
        and isinstance(raw.get("min_run_words"), int)
        and isinstance(raw.get("entries"), list)
        and all(
            isinstance(entry, dict)
            and isinstance(entry.get("name"), str)
            and isinstance(entry.get("shingles"), list)
            and all(isinstance(h, str) for h in entry["shingles"])
            for entry in raw["entries"]
        )
    )


def _read(path: Path) -> tuple[dict, str]:
    """The parsed list and the first characters of its sha256, so the audit
    can say which version of a list a check used."""
    data = Path(path).read_bytes()
    return json.loads(data.decode("utf-8")), hashlib.sha256(data).hexdigest()[:12]


@lru_cache(maxsize=4)
def load_standard_texts(public: Path, deployment: Path | None = None) -> StandardTexts:
    """The public list, plus the deployment list when one is configured."""
    bundled, _ = _read(public)
    if not _well_formed(bundled):
        raise ValueError(f"the bundled standard-text list {public} is malformed")
    lists = [bundled]
    status, fingerprint = "not_configured", None
    if deployment is not None:
        try:
            extra, fingerprint = _read(deployment)
        except (OSError, ValueError) as exc:
            status = "unreadable"
            logger.warning("Deployment standard-text list skipped: %s", type(exc).__name__)
        else:
            if not _well_formed(extra):
                status = "malformed"
            elif (extra["shingle_words"], extra["min_run_words"]) != (
                bundled["shingle_words"],
                bundled["min_run_words"],
            ):
                status = "incompatible"
            else:
                lists.append(extra)
                status = "loaded"
            if status != "loaded":
                logger.warning("Deployment standard-text list skipped: %s", status)
    entries = [entry for raw in lists for entry in raw["entries"]]
    by_hash: dict[str, list[int]] = {}
    for index, entry in enumerate(entries):
        for shingle in entry["shingles"]:
            by_hash.setdefault(shingle, []).append(index)
    public_count = len(lists[0]["entries"])
    texts = StandardTexts(
        shingle_words=lists[0]["shingle_words"],
        min_run_words=lists[0]["min_run_words"],
        names=tuple(entry["name"] for entry in entries),
        entries_by_hash={h: tuple(ids) for h, ids in by_hash.items()},
        public_entries=public_count,
        deployment_entries=len(entries) - public_count,
        deployment_status=status,
        deployment_sha256=fingerprint if status == "loaded" else None,
    )
    # Counts only, never wording, so the deployment step can be checked.
    logger.info(
        "Standard-text lists: %d public entries, %d deployment entries (%s)",
        texts.public_entries,
        texts.deployment_entries,
        texts.deployment_status,
    )
    return texts


def standard_text_spans(text: str, standards: StandardTexts) -> list[StandardSpan]:
    """Runs of words in `text` covered by a listed standard text's
    shingles, at least `min_run_words` long, with the standards they come
    from."""
    found = list(_WORD.finditer(text))
    tokens = [m.group().lower() for m in found]
    size = standards.shingle_words
    covered: list[set[int]] = [set() for _ in tokens]
    for i in range(len(tokens) - size + 1):
        entries = standards.entries_by_hash.get(shingle_hash(tokens[i : i + size]))
        if entries:
            for j in range(i, i + size):
                covered[j].update(entries)
    spans: list[StandardSpan] = []
    i = 0
    while i < len(tokens):
        if not covered[i]:
            i += 1
            continue
        j = i
        while j < len(tokens) and covered[j]:
            j += 1
        if j - i >= standards.min_run_words:
            parts = tuple(
                (standards.names[e], found[i + min(at)].start(), found[i + max(at)].end())
                for e, at in _covering_entries(covered[i:j], standards)
            )
            spans.append(StandardSpan(found[i].start(), found[j - 1].end(), parts))
        i = j
    return spans


def _covering_entries(run: list[set[int]], standards: StandardTexts) -> list[tuple[int, set[int]]]:
    """The entries a run is named after. Standards share short phrases
    ("degree to which a product"), so naming every entry with one shingle in
    the run would list texts that are not there. The entry covering most of
    the run is taken first, then any other that covers at least
    `min_run_words` of what is left (two standard texts printed together)."""
    positions: dict[int, set[int]] = {}
    for k, entries in enumerate(run):
        for entry in entries:
            positions.setdefault(entry, set()).add(k)
    uncovered = set(range(len(run)))
    picked: list[tuple[int, set[int]]] = []
    while uncovered and positions:
        best = max(positions, key=lambda e: (len(positions[e] & uncovered), -e))
        gain = len(positions[best] & uncovered)
        if picked and gain < standards.min_run_words:
            break
        at = positions.pop(best)
        picked.append((best, at))
        uncovered -= at
    return picked
