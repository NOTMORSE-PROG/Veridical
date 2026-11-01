"""Build F7's standard-text lists from source texts (BUG-637).

Some wording is shared by many theses without being reuse: a standard's
definitions, a scale legend, a school's fixed title-page statement. The
originality check leaves such wording out of its shared-wording test. It
reads two lists of hashed word shingles, each entry with its name and source:

- the public list bundled with the code
  (`backend/app/checks/reuse/data/standard_texts.json`), for freely
  shareable texts only, because overlapping hashes can be chained back into
  the wording;
- an optional deployment list for texts whose wording may not be published
  (ISO definitions). Build it to a path outside the repository and point
  `REUSE_STANDARD_TEXTS_FILE` at it on the server; it adds to the public list.

Source format (UTF-8 `.txt` files in one folder):

    # a comment line
    ## ISO/IEC 25010 usability definition | where the wording was taken from
    first wording of the text
    another wording of the same text

    ## Likert agreement scale legend | source | scale
    Strongly disagree, Disagree, Neutral, Agree, Strongly agree

A `## name | source` line starts an entry, and every following non-blank
line is one wording of it. With `| scale`, each line is a comma-separated
list of labels, also hashed in reverse order, because legends are printed
both ways.

Usage, from the repository root:

    uv run --project backend python tools/build_standard_texts.py \\
        --source-dir <public sources>
    uv run --project backend python tools/build_standard_texts.py \\
        --source-dir <deployment sources> --out <path outside the repository>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.checks.reuse.lexical import words  # noqa: E402
from app.checks.reuse.standard_texts import shingle_hash  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "backend" / "app" / "checks" / "reuse" / "data" / "standard_texts.json"
NOTE = (
    "Standard texts many theses share legitimately (BUG-637). Wording inside one of "
    "these does not confirm an originality match. Each entry keeps the hashes of its "
    "word 5-grams (lower-cased words joined by single spaces, BLAKE2b, 8 bytes). "
    "Overlapping hashes can be chained back into the wording, so a list built from "
    "texts that may not be published is kept outside the repository and loaded "
    "through REUSE_STANDARD_TEXTS_FILE. A run of words counts as a standard text only "
    "when at least min_run_words of it are covered. Built by tools/build_standard_texts.py; "
    "do not edit by hand."
)


def parse(source_dir: Path) -> list[dict]:
    entries: list[dict] = []
    for path in sorted(source_dir.glob("*.txt")):
        current = None
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or (line.startswith("#") and not line.startswith("## ")):
                continue
            if line.startswith("## "):
                parts = [part.strip() for part in line[3:].split(" | ")]
                if len(parts) < 2:
                    raise SystemExit(f"{path.name}: an entry needs '## name | source': {line}")
                current = {
                    "name": parts[0],
                    "source": parts[1],
                    "scale": "scale" in parts[2:],
                    "wordings": [],
                }
                entries.append(current)
                continue
            if current is None:
                raise SystemExit(f"{path.name}: wording before the first '## name | source' line")
            current["wordings"].append(line)
    return entries


def shingles_of(entry: dict, size: int) -> list[str]:
    texts = []
    for wording in entry["wordings"]:
        if entry["scale"]:
            labels = [label.strip() for label in wording.split(",") if label.strip()]
            texts += [" ".join(labels), " ".join(reversed(labels))]
        else:
            texts.append(wording)
    hashes = set()
    for text in texts:
        tokens = words(text)
        hashes.update(shingle_hash(tokens[i : i + size]) for i in range(len(tokens) - size + 1))
    return sorted(hashes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--shingle-words", type=int, default=5)
    parser.add_argument("--min-run-words", type=int, default=8)
    args = parser.parse_args()

    entries = parse(args.source_dir)
    if not entries:
        raise SystemExit(f"no entries found in {args.source_dir}")
    built = []
    for entry in entries:
        short = [w for w in entry["wordings"] if len(words(w)) < args.min_run_words]
        if short and not entry["scale"]:
            print(f"warning: '{entry['name']}' has wordings shorter than --min-run-words")
        built.append(
            {
                "name": entry["name"],
                "source": entry["source"],
                "shingles": shingles_of(entry, args.shingle_words),
            }
        )
    out = {
        "_note": NOTE,
        "shingle_words": args.shingle_words,
        "min_run_words": args.min_run_words,
        "entries": built,
    }
    # LF on every platform, so a rebuild on Windows does not rewrite every line.
    args.out.write_text(
        json.dumps(out, indent=1, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"{len(built)} entries, {sum(len(e['shingles']) for e in built)} shingles -> {args.out}")


if __name__ == "__main__":
    main()
