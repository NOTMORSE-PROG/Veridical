#!/usr/bin/env python3
"""Pre-commit gate: a ticket's folder must match its own Status.

Definition of Done item 14 (context/TESTING.md) says closing anything is
TWO moves: git mv the ticket file into done/ or fixed/, AND move its row
out of the board's OPEN WORK section. This gate enforces the first half
mechanically and cross-checks the second.

Layout it enforces (tickets/README.md, "The shape"):

    tickets/V<n>-<name>/open/V-###.md     Status: TODO | WIP | BLOCKED | PARKED
    tickets/V<n>-<name>/done/V-###.md     Status: DONE (or DONE-via-...)
    tickets/BUGS/open/BUG-###.md          Status: TODO | WIP
    tickets/BUGS/fixed/BUG-###.md         Status: FIXED | WONTFIX

Why it exists: on 2026-08-16 a whole-product audit found that every
tracking surface in this project is append-only and ticket-scoped, so
closing a ticket causes nothing to read it. Twenty-nine acceptance
criteria had been honestly written down and then evaporated; four were
deferred to a specific named ticket and all four of those shipped without
the work. The audit's own conclusion was blunt: every rule in this project
enforced by a script has held, and every rule enforced by discipline has
drifted at least once. This is that rule, as a script.

Note tickets/ is gitignored by project policy (D-007), so this runs over
the working tree rather than the index, and reports rather than blocking
a commit that merely does not touch tickets.

Usage:
    python tools/check_ticket_placement.py            # report + exit code
    python tools/check_ticket_placement.py --quiet    # exit code only

Exit 0 = consistent, exit 1 = something is in the wrong place.
"""

from __future__ import annotations

import argparse
import contextlib
import pathlib
import re
import sys
from collections import Counter

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TICKETS = REPO_ROOT / "tickets"
BOARD = TICKETS / "BOARD.md"

AGENTS = ("ui-designer", "ux-critic", "backend-critic", "professor", "newcomer", "manager")

# V8 start gate (owner decision 2026-08-16, tickets/V8-PROPOSAL-real-use.md).
# V8 may begin once the three demo-critical bugs are fixed, and even then only
# the unblocked tickets are eligible. Each blocked ticket names the bug that
# must be in BUGS/fixed/ before it may go DONE -- building V-062 on an unfixed
# BUG-043 would bake the silent-drop path into the new Group FK permanently.
V8_START_GATE = ("BUG-049", "BUG-048", "BUG-052")
V8_BLOCKERS = {
    "V-062": "BUG-043",  # Group FK would inherit the silently-dropped label path
    # "V-070": "BUG-049" -- RETIRED 2026-08-18. BUG-049 was fixed on
    # 2026-08-17 and the blocker became a no-op the moment it landed. The
    # session that fixed it wrote "should be trimmed next time that file is
    # touched" into STATE.md, which is discipline, which is what drifts.
    # A satisfied blocker left in the map is a rule that silently outlives
    # its own precondition.
    "V-066": "BUG-050",  # library display depends on the self-match/label fixes
    "V-058": "BUG-050",  # same, and it widens the same exposure
}
OPEN_STORY = {"TODO", "WIP", "BLOCKED", "PARKED"}
DONE_STORY = {"DONE"}
OPEN_BUG = {"TODO", "WIP"}
CLOSED_BUG = {"FIXED", "WONTFIX"}

# Section markers on the board. Distinctive on purpose -- the board
# discusses its own sections in prose, so a bare phrase match is wrong.
OPEN_BUGS_MARKER = "Open bugs"
OPEN_STORY_MARKER = "Open story tickets"
CLOSED_MARKER = "── CLOSED BUGS ──"
GAPS_MARKER = "Carried-forward gaps"
BUG_ROW_RE = re.compile(r"^\|\s*`(BUG-\d+)`\s*\|", re.M)

# Status: **TODO**  /  Status: TODO  /  · Status: DONE-via-BUG-033
STATUS_RE = re.compile(r"Status:\s*\**\s*([A-Za-z][A-Za-z-]*)", re.I)


def read_status(path: pathlib.Path) -> str | None:
    """First Status: token in the file's header block."""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return None
    m = STATUS_RE.search(head)
    if not m:
        return None
    # DONE-via-BUG-033 and friends normalise to their first word.
    return m.group(1).upper().split("-")[0]


def board_text() -> str:
    try:
        return BOARD.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def board_section(text: str, start_marker: str, end_marker: str) -> str | None:
    """Return the uniquely bounded section between two Markdown headings."""

    def heading_matches(marker: str) -> list[re.Match[str]]:
        pattern = re.compile(
            rf"^#+[ \t]+{re.escape(marker)}(?:[ \t]+[^\r\n]*)?[ \t]*$",
            re.M,
        )
        return list(pattern.finditer(text))

    starts = heading_matches(start_marker)
    ends = heading_matches(end_marker)
    if len(starts) != 1 or len(ends) != 1:
        return None
    start = starts[0].end()
    end = ends[0].start()
    if end <= start:
        return None
    return text[start:end]


def row_word(count: int) -> str:
    return "row" if count == 1 else "rows"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    problems: list[str] = []
    open_ids: list[str] = []
    closed_ids: list[str] = []
    open_bug_ids: list[str] = []
    closed_bug_ids: list[str] = []
    n_open_story = n_open_bug = 0

    if not TICKETS.exists():
        print("no tickets/ directory; nothing to check")
        return 0

    # ---- story tickets -------------------------------------------------
    for mdir in sorted(TICKETS.glob("V[0-9]*-*")):
        if not mdir.is_dir():
            continue

        for stray in sorted(mdir.glob("V-*.md")):
            problems.append(
                f"{stray.relative_to(REPO_ROOT).as_posix()}: sits at the milestone "
                f"root; must be in open/ or done/"
            )

        for sub, allowed, label in (("open", OPEN_STORY, "open"), ("done", DONE_STORY, "done")):
            d = mdir / sub
            if not d.exists():
                continue
            for f in sorted(d.glob("V-*.md")):
                rel = f.relative_to(REPO_ROOT).as_posix()
                status = read_status(f)
                if status is None:
                    problems.append(f"{rel}: no 'Status:' line found in its header")
                    continue
                if status not in allowed:
                    want = "done/" if status in DONE_STORY else "open/"
                    problems.append(
                        f"{rel}: Status is {status} but it is in {label}/ -- git mv it to {want}"
                    )
                if sub == "open":
                    n_open_story += 1
                    open_ids.append(f.stem)
                else:
                    closed_ids.append(f.stem)

    # ---- bugs ----------------------------------------------------------
    bugs = TICKETS / "BUGS"
    if bugs.exists():
        for stray in sorted(bugs.glob("BUG-*.md")):
            problems.append(
                f"{stray.relative_to(REPO_ROOT).as_posix()}: sits at the BUGS root; "
                f"must be in open/ or fixed/"
            )
        for sub, allowed, label in (("open", OPEN_BUG, "open"), ("fixed", CLOSED_BUG, "fixed")):
            d = bugs / sub
            if not d.exists():
                continue
            for f in sorted(d.glob("BUG-*.md")):
                rel = f.relative_to(REPO_ROOT).as_posix()
                status = read_status(f)
                if status is None:
                    problems.append(f"{rel}: no 'Status:' line found in its header")
                    continue
                if status not in allowed:
                    want = "fixed/" if status in CLOSED_BUG else "open/"
                    problems.append(
                        f"{rel}: Status is {status} but it is in {label}/ -- git mv it to {want}"
                    )
                if sub == "open":
                    n_open_bug += 1
                    open_ids.append(f.stem)
                    open_bug_ids.append(f.stem)
                else:
                    closed_ids.append(f.stem)
                    closed_bug_ids.append(f.stem)

    # ---- DoD 12/12b: a DONE ticket must name its agents and disposition ----
    # Running the agent is half the loop; the audit found 41 of 46 DONE tickets
    # naming no critic at all, and DoD 12 as first written said nothing about
    # what happens to a finding once made. Warn rather than block: this is
    # retroactive over years of history, and a hard failure would make the gate
    # unusable on day one. Loud and countable is the point.
    stale_dod = []
    for mdir in sorted(TICKETS.glob("V[0-9]*-*")):
        d = mdir / "done"
        if not d.exists():
            continue
        for f in sorted(d.glob("V-*.md")):
            try:
                body = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            named = any(a in body for a in AGENTS)
            dispositioned = "Agent findings and disposition" in body
            if not named:
                stale_dod.append((f.stem, "names no agent (DoD 12)"))
            elif not dispositioned:
                stale_dod.append((f.stem, "no disposition section (DoD 12b)"))

    # ---- V8 start gate and per-ticket blockers --------------------------
    fixed_bugs = (
        {f.stem for f in (TICKETS / "BUGS" / "fixed").glob("BUG-*.md")}
        if (TICKETS / "BUGS" / "fixed").exists()
        else set()
    )
    v8_done = TICKETS / "V8-real-use" / "done"
    if v8_done.exists():
        for f in sorted(v8_done.glob("V-*.md")):
            missing = [b for b in V8_START_GATE if b not in fixed_bugs]
            if missing:
                problems.append(
                    f"{f.stem} is DONE but the V8 start gate is not met: "
                    f"{', '.join(missing)} still open"
                )
            blocker = V8_BLOCKERS.get(f.stem)
            if blocker and blocker not in fixed_bugs:
                problems.append(
                    f"{f.stem} is DONE but its blocker {blocker} is still open "
                    f"-- see tickets/V8-PROPOSAL-real-use.md"
                )

    # ---- board cross-check ---------------------------------------------
    text = board_text()
    if not text.strip():
        problems.append("tickets/BOARD.md is missing, unreadable, or empty")
    if text.strip():
        for tid in open_ids:
            if tid in open_bug_ids:
                continue
            if tid not in text:
                problems.append(
                    f"{tid} is open on disk but never mentioned in tickets/BOARD.md "
                    f"-- a ticket with no board row is invisible (DoD 14)"
                )

        # DoD 14's SECOND half, which this gate did not check until
        # 2026-08-18. It enforced "closing a ticket moves the file" and
        # "an OPEN ticket has a row", and those held -- but nothing read
        # the board after a ticket closed, so a closed ticket could leave
        # OPEN WORK and never arrive anywhere. Found live: BUG-070 and
        # BUG-071 sat in BUGS/fixed/ appearing on NO board section at all,
        # while the CLOSED BUGS heading counted them as "49, all verified
        # fixed" -- a heading that overcounted its own list by two and
        # described two WONTFIX-MOOT reverts as verified fixes. Exactly
        # the append-only blind spot the 2026-08-16 audit named.
        for tid in closed_ids:
            if tid in closed_bug_ids:
                continue
            if tid not in text:
                problems.append(
                    f"{tid} is closed on disk but never mentioned in "
                    f"tickets/BOARD.md -- closing is two moves, and the row "
                    f"has to land somewhere (DoD 14)"
                )

        # BUG-230: each bug register is a bijection with its folder. A bare
        # mention anywhere in the board is not a row, and a set is not enough:
        # converting rows to a set silently erases duplicate-row evidence.
        open_section = board_section(text, OPEN_BUGS_MARKER, OPEN_STORY_MARKER)
        closed_section = board_section(text, CLOSED_MARKER, GAPS_MARKER)
        if open_section is None:
            problems.append("BOARD.md must contain exactly one bounded Open bugs register")
        if closed_section is None:
            problems.append("BOARD.md must contain exactly one bounded CLOSED BUGS register")

        if open_section is not None and closed_section is not None:
            open_registered = Counter(BUG_ROW_RE.findall(open_section))
            closed_registered = Counter(BUG_ROW_RE.findall(closed_section))
            open_files = set(open_bug_ids)
            closed_files = set(closed_bug_ids)
            known_files = open_files | closed_files

            for tid, count in sorted(open_registered.items()):
                if count > 1:
                    problems.append(f"{tid} has {count} rows in the board's Open bugs register")
                if tid not in known_files:
                    problems.append(f"{tid} has an Open bugs row but no matching BUG ticket file")

            for tid, count in sorted(closed_registered.items()):
                if count > 1:
                    problems.append(f"{tid} has {count} rows in the board's CLOSED BUGS register")
                if tid not in known_files:
                    problems.append(f"{tid} has a CLOSED BUGS row but no matching BUG ticket file")

            for tid in sorted(open_files):
                open_count = open_registered[tid]
                if open_count == 0:
                    problems.append(
                        f"{tid} is in BUGS/open/ but has 0 rows in the board's Open bugs register"
                    )
                closed_count = closed_registered[tid]
                if closed_count:
                    problems.append(
                        f"{tid} is in BUGS/open/ but has {closed_count} "
                        f"{row_word(closed_count)} in the board's CLOSED BUGS register"
                    )

            for tid in sorted(closed_files):
                open_count = open_registered[tid]
                if open_count:
                    problems.append(
                        f"{tid} is in BUGS/fixed/ but has {open_count} "
                        f"{row_word(open_count)} in the board's Open bugs register"
                    )
                closed_count = closed_registered[tid]
                if closed_count == 0:
                    problems.append(
                        f"{tid} is in BUGS/fixed/ but has 0 rows in the board's "
                        "CLOSED BUGS register"
                    )

        for heading, actual, what in (
            (r"##\s*Open bugs\s*[-—]+\s*(\d+)", n_open_bug, "open bugs"),
            (r"##\s*Open story tickets\s*[-—]+\s*(\d+)", n_open_story, "open story tickets"),
            (r"── CLOSED BUGS ──\s*(\d+)", len(closed_bug_ids), "closed bugs"),
        ):
            m = re.search(heading, text)
            if m and int(m.group(1)) != actual:
                problems.append(
                    f"BOARD.md {what} heading says {m.group(1)} but the "
                    f"filesystem has {actual} -- the counts are content, "
                    f"not decoration"
                )

    if not args.quiet:
        print(f"open story tickets: {n_open_story}   open bugs: {n_open_bug}")
        if stale_dod:
            no_agent = sum(1 for _, r in stale_dod if "names no agent" in r)
            no_disp = len(stale_dod) - no_agent
            print(
                f"DoD 12/12b backlog: {no_agent} DONE ticket(s) name no agent, "
                f"{no_disp} name one but have no disposition section "
                f"(warning, not a failure -- retroactive)"
            )

    if problems:
        print("\nBLOCKED: ticket placement is inconsistent.\n")
        for p in problems:
            print("  " + p)
        print(
            "\nRule: tickets/README.md 'HOW BOARD.md IS ORGANIZED' + "
            "context/TESTING.md DoD item 14.\n"
            "Closing anything is two moves: the file AND the board row."
        )
        return 1

    if not args.quiet:
        print(
            "placement OK: every ticket's folder matches its Status, and the board's counts agree."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
