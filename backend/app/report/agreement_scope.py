"""What the internal-agreement check covered, worded for the PDF export
(BUG-617). Same sentences as frontend/src/domain/agreementScope.ts; the
fixed strings live in data/agreement_scope.json. Coverage only: no count of
agreeing pairs, because the pairing is not yet good enough for one."""

import json
from functools import lru_cache
from pathlib import Path

from app.report.schemas import AgreementScopeOut


@lru_cache(maxsize=1)
def agreement_scope_copy() -> dict:
    return json.loads((Path(__file__).parent / "data" / "agreement_scope.json").read_text("utf-8"))


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def role_phrase(role: str) -> str:
    known = agreement_scope_copy()["roles"].get(role)
    return known[0] if known else "another part of the paper"


def found_line(scope: AgreementScopeOut) -> str | None:
    objectives, results = scope.n_objectives, scope.n_results
    if objectives is None or results is None:
        return None
    o = (
        "no possible objectives"
        if objectives == 0
        else f"{objectives} possible {_plural(objectives, 'objective', 'objectives')}"
    )
    r = (
        "no possible results"
        if results == 0
        else f"{results} possible {_plural(results, 'result', 'results')}"
    )
    return f"Found {o} and {r}."


def set_aside_lines(scope: AgreementScopeOut) -> list[str]:
    roles = scope.set_aside_by_role
    folding = agreement_scope_copy()["role_folding"]
    fold = len(roles) >= folding["fold_at"]
    shown = roles[: folding["shown"]] if fold else roles
    lines = []
    for item in shown:
        known = agreement_scope_copy()["roles"].get(item.role)
        subject = f"{item.count} {_plural(item.count, 'result', 'results')}"
        verb = _plural(item.count, "was", "were")
        lines.append(
            f"{subject} from the {known[0]} {verb} set aside, because that part of a paper "
            f"usually {known[1]}."
            if known
            else f"{subject} from another part of the paper {verb} set aside, because that "
            "part does not usually report the study's own results."
        )
    if fold:
        rest = sum(item.count for item in roles[folding["shown"] :])
        lines.append(
            f"{rest} more {_plural(rest, 'was', 'were')} set aside from other parts of the paper."
        )
    short = scope.n_results_too_short or 0
    if short:
        lines.append(
            f"{short} {_plural(short, 'result was', 'results were')} set aside as too short "
            "to compare."
        )
    return lines


def interrupted(scope: AgreementScopeOut, state: str) -> bool:
    return state == "incomplete" or bool(scope.n_pairs_skipped)


def matched_line(scope: AgreementScopeOut) -> str | None:
    """Said only when fewer objectives were matched than found, so "found 17"
    never reads as 17 checked."""
    found, matched = scope.n_objectives, scope.n_objectives_matched
    if not found or matched is None or matched >= found:
        return None
    if matched == 0:
        return f"None of the {found} objectives were matched to a result."
    # Pairs are counted apart: one objective can be paired with several results.
    verb = _plural(matched, "was", "were")
    return (
        f"{matched} of the {found} objectives {verb} matched to at least one result. "
        "One objective can be paired with more than one result."
    )


def outcome_lines(
    scope: AgreementScopeOut,
    *,
    findings: int,
    undecided: int,
    compared: int | None,
    interrupted: bool = False,
) -> list[str]:
    """Record coverage and leads independently; unresolved pairs may be local."""
    copy = agreement_scope_copy()
    lines = [
        copy["comparison_unknown"]
        if compared is None
        else copy["compared_count"].format(n=compared)
    ]
    if compared == 0:
        lines.extend([_nothing_compared(scope), copy["no_agreement_inference"]])
    if findings:
        label = copy["review_lead"] if findings == 1 else copy["review_leads"]
        lines.append(f"{findings} {label}. Listed under Findings.")
    elif compared is not None and compared > 0:
        lines.append(copy["no_review_leads"])
    if undecided:
        lines.append(copy["unresolved_count"].format(n=undecided))
    if interrupted:
        lines.extend([copy["interrupted"], copy["interrupted_tail"]])
    return lines


def scope_paragraphs(
    scope: AgreementScopeOut,
    *,
    findings: int,
    undecided: int,
    compared: int | None,
    interrupted: bool,
) -> list[str]:
    """The card in reading order: the outcome first, then what it covered."""
    copy = agreement_scope_copy()
    lines = []
    if outcome := outcome_lines(
        scope, findings=findings, undecided=undecided, compared=compared, interrupted=interrupted
    ):
        lines.append(" ".join(outcome))
    found = " ".join(line for line in (found_line(scope), matched_line(scope)) if line)
    if found:
        lines.append(found)
    setaside = set_aside_lines(scope)
    if scope.results_section_identified is False:
        # No section was found, so nothing was set aside by its part; only
        # the too-short count still applies.
        setaside = setaside[-1:] if scope.n_results_too_short else []
    if setaside:
        lines.append(" ".join(setaside))
    if scope.results_section_identified is False:
        lines.append(copy["not_identified"])
    lines.append(copy["limit"])
    if scope.absence_leads_withheld:
        lines.append(copy["absence"])
    if compared:
        lines.append(f"{copy['matching_heading']}: {copy['matching_caveat']}")
    return lines


def _nothing_compared(scope: AgreementScopeOut) -> str:
    """A zero comparison count does not establish a failed candidate match."""
    return agreement_scope_copy()["no_ai_comparison"]
