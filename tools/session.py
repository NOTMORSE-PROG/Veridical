#!/usr/bin/env python3
"""Session registry: who is working on what, right now, in this clone.

VERIDICAL is routinely worked by two or three concurrent sessions against
the SAME working tree. Nothing in the project has ever recorded that fact,
and the project's own history is a list of what that costs:

  * `context/STATE.md` opens with a warning that its own "(cont'd N)"
    suffixes are NOT chronological, because concurrent sessions each pick
    the next free number they can see and their inserts land out of order.
    Position in the file is the only reliable order, and no entry says who
    wrote it.
  * 2026-08-17: a concurrent session closed BUG-049 with a written claim
    ("the fixture did not change") that `git show` proved false. There was
    no way to tell whose note it was or to ask.
  * Three DONE tickets on the board (V-059, V-060, and the V-042 line)
    record a live browser walkthrough that was NOT completed because the
    browser was "held by a concurrent session". Verification
    quality was degraded by contention over a resource nothing arbitrates.
  * 2026-08-17: a whole batch of the previous day's work was found sitting
    uncommitted in the tree, authorship unknown, and had to be verified
    from scratch before it could be shipped.
  * `Status: WIP` has existed since the ticket system was created and has
    been used by exactly ZERO tickets in the project's history. The one
    field meant to say "someone is on this" is dead, so two sessions can
    pick the same ticket and neither can find out.

This tool is the counterweight, and it follows the rule the 2026-08-16
audit stated for everything else here: every rule enforced by a script has
held, every rule enforced by discipline has drifted at least once. So a
claim is a file a script can read, not a promise.

The registry lives in `context/sessions/` (gitignored with the rest of
`context/`, D-007) -- it is per-clone local state, which is exactly right:
the sessions it arbitrates all share one machine and one working tree.

Usage:
    python tools/session.py start --name "audit + management"
    python tools/session.py status
    python tools/session.py claim  <id> BUG-045 browser
    python tools/session.py release <id> browser
    python tools/session.py end <id>
    python tools/session.py check          # the pre-commit gate
    python tools/session.py reap           # clear sessions past their TTL

Environment does not carry between commands in this workflow, so a session
cannot `export` its id once. It passes `<id>` explicitly, or sets it inline
for a single command:

    VERIDICAL_SESSION=s-20260818-a1b2 git commit -m "..."

Exit 0 = clear, exit 1 = a real conflict (check), exit 2 = usage error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import secrets
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SESSIONS = REPO_ROOT / "context" / "sessions"
TICKETS = REPO_ROOT / "tickets"
STATE = REPO_ROOT / "context" / "STATE.md"

# Ground rule 7 -- no inline literals for thresholds. These are the tool's
# own configuration, overridable from the environment, documented here
# because a dev tool has no place in the app's pydantic settings.
#
# STALE_AFTER_HOURS: a session that has not touched the registry in this
# long is presumed dead (a crashed session, a closed terminal). It is
# reported as stale and `reap` removes it. Set generously -- a session
# reading a large file for forty minutes is still alive, and wrongly
# reaping a live claim is worse than carrying a dead one for an afternoon.
STALE_AFTER_HOURS = float(os.environ.get("VERIDICAL_SESSION_STALE_HOURS", "6"))

# Shared resources that are genuinely exclusive: one at a time, or the
# work is silently wrong. `browser` is the Playwright automation instance,
# which is a single shared browser -- this project has already lost three
# live walkthroughs to contention over it.
KNOWN_RESOURCES = {
    "browser": "the Playwright browser (single shared instance)",
    "backend": "the local backend dev server / its port",
    "frontend": "the local frontend dev server / its port",
    "db": "the local dev database (migrations, destructive fixtures)",
    "worktree": "an exclusive hold on the working tree (rebase, bisect, mass rename)",
}

TICKET_RE = re.compile(r"^(?:BUG|V)-\d{3}$")
STATUS_RE = re.compile(r"(Status:\s*\**\s*)([A-Za-z][A-Za-z-]*)")
ID_RE = re.compile(r"^s-\d{8}-[0-9a-f]{4}$")


# ---------------------------------------------------------------- helpers


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


def _parse(s: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _age_hours(s: str) -> float | None:
    t = _parse(s)
    if t is None:
        return None
    return (_now() - t).total_seconds() / 3600.0


def _path(sid: str) -> pathlib.Path:
    return SESSIONS / f"{sid}.json"


def _load_all() -> list[dict]:
    if not SESSIONS.exists():
        return []
    out = []
    for f in sorted(SESSIONS.glob("s-*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        d.setdefault("id", f.stem)
        out.append(d)
    return out


def _load(sid: str) -> dict | None:
    p = _path(sid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save(d: dict) -> None:
    SESSIONS.mkdir(parents=True, exist_ok=True)
    d["last_seen"] = _iso(_now())
    _path(d["id"]).write_text(
        json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _live(sessions: list[dict]) -> list[dict]:
    """Sessions still inside their TTL. A stale session's claims are
    reported but never block -- see STALE_AFTER_HOURS."""
    out = []
    for s in sessions:
        age = _age_hours(s.get("last_seen", ""))
        if age is None or age <= STALE_AFTER_HOURS:
            out.append(s)
    return out


def _find_ticket(tid: str) -> pathlib.Path | None:
    for p in TICKETS.rglob(f"{tid}.md"):
        return p
    return None


def _set_ticket_status(tid: str, new: str, *, only_from: set[str]) -> str | None:
    """Flip a ticket's own Status: line, so a claim is visible to anyone
    reading the ticket rather than only to this registry. Returns the old
    status if it changed, else None.

    This is what revives `WIP`. The status has existed since the ticket
    system was written and no ticket has ever carried it, because setting
    it by hand was discipline and discipline drifts. Claiming is the
    moment the fact becomes true, so that is where the write belongs.
    """
    p = _find_ticket(tid)
    if p is None:
        return None
    body = p.read_text(encoding="utf-8", errors="replace")
    m = STATUS_RE.search(body[:2000])
    if not m:
        return None
    old = m.group(2).upper()
    if old not in only_from or old == new:
        return None
    start, end = m.span(2)
    p.write_text(body[:start] + new + body[end:], encoding="utf-8")
    return old


def _emit(*parts: str) -> None:
    print(*parts)


def _reconfigure() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


# --------------------------------------------------------------- commands


def cmd_start(args: argparse.Namespace) -> int:
    sid = f"s-{_now():%Y%m%d}-{secrets.token_hex(2)}"
    branch = _git_branch()
    d = {
        "id": sid,
        "name": args.name or "(unnamed)",
        "started": _iso(_now()),
        "branch": branch,
        "tickets": [],
        "resources": [],
        "notes": [],
    }
    _save(d)

    _emit(f"session {sid} registered   branch={branch}   name={d['name']}")
    _emit("")
    _emit("Use this id for the rest of the session:")
    _emit(f"  python tools/session.py claim {sid} <TICKET|resource> ...")
    _emit(f"  python tools/session.py end {sid}")
    _emit(f"  VERIDICAL_SESSION={sid} git commit -m \"...\"")
    _emit("")
    _emit(f"Put [{sid}] in your context/STATE.md entry heading so the entry")
    _emit("is attributable -- that file's own top warns that its (cont'd N)")
    _emit("suffixes are not reliable order between concurrent sessions.")

    others = [s for s in _live(_load_all()) if s["id"] != sid]
    if others:
        _emit("")
        _emit(f"{len(others)} other live session(s) -- read this before you pick work:")
        for s in others:
            _emit(f"  {_describe(s)}")
    return 0


def _git_branch() -> str:
    import subprocess

    try:
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        return r.stdout.strip() or "?"
    except OSError:
        return "?"


def _describe(s: dict) -> str:
    age = _age_hours(s.get("last_seen", ""))
    seen = f"{age:.1f}h ago" if age is not None else "never"
    held = ", ".join(s.get("tickets", [])) or "-"
    res = ", ".join(s.get("resources", [])) or "-"
    stale = "  [STALE]" if age is not None and age > STALE_AFTER_HOURS else ""
    return (
        f"{s['id']}  {s.get('name','')!r}  branch={s.get('branch','?')}\n"
        f"      tickets: {held}\n"
        f"      holds:   {res}\n"
        f"      last seen: {seen}{stale}"
    )


def cmd_status(args: argparse.Namespace) -> int:
    sessions = _load_all()
    if not sessions:
        _emit("no sessions registered.")
        _emit("")
        _emit("If another session is running right now it has not registered,")
        _emit("and nothing here can arbitrate against it. Ask it to run:")
        _emit('  python tools/session.py start --name "<what it is doing>"')
        return 0

    live = _live(sessions)
    stale = [s for s in sessions if s not in live]
    _emit(f"{len(live)} live session(s), {len(stale)} stale (TTL {STALE_AFTER_HOURS}h)")
    _emit("")
    for s in sessions:
        _emit(_describe(s))
        _emit("")

    # Resource contention is the headline: it is the failure this project
    # has actually paid for, three times, on the board.
    for r, why in KNOWN_RESOURCES.items():
        holders = [s["id"] for s in live if r in s.get("resources", [])]
        if len(holders) > 1:
            _emit(f"CONTENDED: {r} ({why}) claimed by {', '.join(holders)}")
    dbl = _double_claims(live)
    for tid, ids in sorted(dbl.items()):
        _emit(f"CONTENDED: {tid} claimed by {', '.join(ids)}")

    if stale:
        _emit("Clear dead sessions with: python tools/session.py reap")
    return 0


def _double_claims(live: list[dict]) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {}
    for s in live:
        for t in s.get("tickets", []):
            seen.setdefault(t, []).append(s["id"])
    return {t: ids for t, ids in seen.items() if len(ids) > 1}


def cmd_claim(args: argparse.Namespace) -> int:
    d = _load(args.id)
    if d is None:
        _emit(f"no such session: {args.id}  (run `session.py start` first)")
        return 2

    live = [s for s in _live(_load_all()) if s["id"] != args.id]
    refused, took = [], []

    for item in args.items:
        if item in KNOWN_RESOURCES:
            holder = next((s for s in live if item in s.get("resources", [])), None)
            if holder:
                refused.append(
                    f"{item}: held by {holder['id']} ({holder.get('name','')!r}) "
                    f"-- {KNOWN_RESOURCES[item]}"
                )
                continue
            if item not in d["resources"]:
                d["resources"].append(item)
            took.append(item)
            continue

        tid = item.upper()
        if not TICKET_RE.match(tid):
            refused.append(
                f"{item}: not a ticket id (BUG-### / V-###) and not a known "
                f"resource ({', '.join(sorted(KNOWN_RESOURCES))})"
            )
            continue
        if _find_ticket(tid) is None:
            refused.append(f"{tid}: no such ticket file under tickets/")
            continue
        holder = next((s for s in live if tid in s.get("tickets", [])), None)
        if holder:
            refused.append(f"{tid}: held by {holder['id']} ({holder.get('name','')!r})")
            continue
        if tid not in d["tickets"]:
            d["tickets"].append(tid)
        took.append(tid)
        if not args.no_status:
            old = _set_ticket_status(tid, "WIP", only_from={"TODO"})
            if old:
                _emit(f"  {tid}: Status {old} -> WIP in the ticket file")

    _save(d)

    if took:
        _emit(f"{args.id} claimed: {', '.join(took)}")
    for r in refused:
        _emit(f"REFUSED  {r}")
    if refused:
        _emit("")
        _emit("A refusal is the tool working. Pick different work, or ask the")
        _emit("holding session to release it -- do not work it in parallel.")
        return 1
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    d = _load(args.id)
    if d is None:
        _emit(f"no such session: {args.id}")
        return 2
    freed = []
    for item in args.items:
        tid = item.upper() if TICKET_RE.match(item.upper()) else item
        for key in ("tickets", "resources"):
            if tid in d[key]:
                d[key].remove(tid)
                freed.append(tid)
        if TICKET_RE.match(tid) and not args.no_status:
            # Only WIP goes back to TODO. A ticket the session actually
            # finished is already DONE/FIXED and must not be reopened.
            old = _set_ticket_status(tid, "TODO", only_from={"WIP"})
            if old:
                _emit(f"  {tid}: Status WIP -> TODO in the ticket file")
    _save(d)
    _emit(f"{args.id} released: {', '.join(freed) if freed else '(nothing held)'}")
    return 0


def cmd_end(args: argparse.Namespace) -> int:
    d = _load(args.id)
    if d is None:
        _emit(f"no such session: {args.id}")
        return 2
    held = list(d.get("tickets", []))
    for tid in held:
        if not args.no_status:
            _set_ticket_status(tid, "TODO", only_from={"WIP"})
    _path(args.id).unlink(missing_ok=True)
    _emit(f"session {args.id} ended; released {', '.join(held) if held else 'nothing'}")
    _emit("")
    _emit("Session-end checklist: STATE.md entry appended with")
    _emit(f"[{args.id}] in its heading, BOARD.md status moved, CHANGELOG.md")
    _emit("entry for any code change, working tree committed or explicitly")
    _emit("handed over -- the 2026-08-17 session found a whole day's work")
    _emit("stranded uncommitted with no way to tell whose it was.")
    return 0


def cmd_reap(args: argparse.Namespace) -> int:
    sessions = _load_all()
    live = _live(sessions)
    dead = [s for s in sessions if s not in live]
    if not dead:
        _emit(f"nothing stale (TTL {STALE_AFTER_HOURS}h).")
        return 0
    for s in dead:
        for tid in s.get("tickets", []):
            _set_ticket_status(tid, "TODO", only_from={"WIP"})
        _path(s["id"]).unlink(missing_ok=True)
        _emit(f"reaped {s['id']} ({s.get('name','')!r}), freed {s.get('tickets', []) or 'nothing'}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """The pre-commit gate. Blocks on a real conflict, warns on everything
    else -- a gate that cries wolf gets disabled, and the owner commits
    from this tree by hand too."""
    sessions = _load_all()
    live = _live(sessions)
    problems: list[str] = []
    warnings: list[str] = []

    me = os.environ.get("VERIDICAL_SESSION", "").strip()
    if me:
        if not ID_RE.match(me):
            problems.append(f"VERIDICAL_SESSION={me!r} is not a valid session id")
        elif _load(me) is None:
            problems.append(
                f"VERIDICAL_SESSION={me} names a session that is not registered "
                f"(ended, reaped, or never started)"
            )
        else:
            d = _load(me)
            _save(d)  # commit is a heartbeat

    for tid, ids in sorted(_double_claims(live).items()):
        problems.append(f"{tid} is claimed by {len(ids)} live sessions: {', '.join(ids)}")

    for r in KNOWN_RESOURCES:
        holders = [s["id"] for s in live if r in s.get("resources", [])]
        if len(holders) > 1:
            problems.append(f"resource {r} claimed by {len(holders)} sessions: {', '.join(holders)}")

    others = [s for s in live if s["id"] != me]
    if others:
        warnings.append(f"{len(others)} other live session(s) share this working tree:")
        for s in others:
            held = ", ".join(s.get("tickets", []) + s.get("resources", [])) or "nothing"
            warnings.append(f"    {s['id']} ({s.get('name','')!r}) holds {held}")
        if not me:
            warnings.append(
                "    you did not identify yourself: prefix the commit with "
                "VERIDICAL_SESSION=<id> so this commit is attributable"
            )

    stale = [s for s in sessions if s not in live]
    if stale:
        warnings.append(
            f"{len(stale)} stale session(s) past the {STALE_AFTER_HOURS}h TTL "
            f"still hold claims -- `python tools/session.py reap`"
        )

    warnings.extend(_state_attribution_warnings())
    warnings.extend(_unattributed_tree_warnings(live, me))

    for w in warnings:
        print("warn: " + w if not w.startswith("    ") else w)
    if problems:
        print("\nBLOCKED: session claims conflict.\n")
        for p in problems:
            print("  " + p)
        print(
            "\nTwo live sessions cannot hold the same ticket or the same shared\n"
            "resource. Resolve it (release one side) before committing.\n"
            "See context/SESSIONS.md."
        )
        return 1
    if not args.quiet and not warnings:
        print(f"sessions OK: {len(live)} live, no contention.")
    return 0


def _unattributed_tree_warnings(live: list[dict], me: str) -> list[str]:
    """Modified code files that no live session's claims explain.

    On 2026-08-17 a session found a whole batch of the previous day's work
    sitting uncommitted in this tree, could not tell which session left it,
    and had to re-verify all of it from scratch before it could ship. The
    tree is shared; a dirty file with no owner is a real cost to whoever
    finds it next.

    This warns, never blocks. Plenty of legitimate work in progress looks
    exactly like this -- the point is that the next session SEES it, and
    that the session leaving it knows it is visible.
    """
    import subprocess

    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "--", "backend/", "frontend/", "tools/"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
    except OSError:
        return []
    changed = [
        line[3:].strip() for line in r.stdout.splitlines() if line and not line.startswith("??")
    ]
    if not changed:
        return []

    # A ticket id claimed by ANY live session is taken as explaining the
    # tree: we cannot map files to tickets reliably, and guessing wrong
    # in the noisy direction is how a warning gets ignored.
    claimed = [t for s in live for t in s.get("tickets", [])]
    if claimed:
        return []

    out = [
        f"{len(changed)} modified code file(s) in the shared tree, and NO live "
        f"session claims any ticket:"
    ]
    for f in changed[:8]:
        out.append(f"    {f}")
    if len(changed) > 8:
        out.append(f"    ... and {len(changed) - 8} more")
    out.append(
        "    if this is not yours, it belongs to an unregistered session -- do not "
        "commit it, and do not assume it is finished"
    )
    return out


def _state_attribution_warnings() -> list[str]:
    """STATE.md's own header warns that its (cont'd N) suffixes are not
    reliable chronological order between concurrent sessions, and no entry
    records who wrote it. A session id in the heading fixes both."""
    if not STATE.exists():
        return []
    try:
        text = STATE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    today = f"{_now():%Y-%m-%d}"
    out = []
    for line in text.splitlines():
        if line.startswith(f"## {today}"):
            if not re.search(r"\[s-\d{8}-[0-9a-f]{4}\]", line):
                out.append(
                    "today's STATE.md entry heading carries no [session-id]; "
                    "concurrent entries on one date are not otherwise attributable"
                )
            break
    return out


# ------------------------------------------------------------------- main


def main() -> int:
    _reconfigure()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("start", help="register this session and get an id")
    p.add_argument("--name", help="one phrase: what this session is doing")
    p.set_defaults(fn=cmd_start)

    p = sub.add_parser("status", help="who is live and what they hold")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("claim", help="claim tickets and/or shared resources")
    p.add_argument("id")
    p.add_argument("items", nargs="+")
    p.add_argument("--no-status", action="store_true", help="do not touch ticket Status:")
    p.set_defaults(fn=cmd_claim)

    p = sub.add_parser("release", help="give back tickets/resources")
    p.add_argument("id")
    p.add_argument("items", nargs="+")
    p.add_argument("--no-status", action="store_true")
    p.set_defaults(fn=cmd_release)

    p = sub.add_parser("end", help="end the session, releasing everything")
    p.add_argument("id")
    p.add_argument("--no-status", action="store_true")
    p.set_defaults(fn=cmd_end)

    p = sub.add_parser("reap", help="remove sessions past the TTL")
    p.set_defaults(fn=cmd_reap)

    p = sub.add_parser("check", help="pre-commit gate: conflicts and contention")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(fn=cmd_check)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
