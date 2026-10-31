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
    shown = roles[:2] if len(roles) >= 4 else roles
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
    if len(roles) >= 4:
        rest = sum(item.count for item in roles[2:])
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


def outcome_lines(
    scope: AgreementScopeOut, *, findings: int, undecided: int, compared: int | None
) -> list[str]:
    """The comparison's outcome for the PDF, never a count of agreeing pairs."""
    if compared is None:
        return []
    if findings > 0:
        lines = [
            "The comparison found 1 possible inconsistency. It is listed under Findings."
            if findings == 1
            else f"The comparison found {findings} possible inconsistencies. They are listed "
            "under Findings."
        ]
        if undecided > 0:
            lines.append(
                "1 other pair could not be decided. It is listed under Pairs to read yourself."
                if undecided == 1
                else f"{undecided} other pairs could not be decided. They are listed under "
                "Pairs to read yourself."
            )
        return lines
    if compared == 0:
        left_out = (scope.n_results_set_aside or 0) + (scope.n_results_too_short or 0)
        reason = (
            "No objectives were found, so nothing was compared and the check makes no finding."
            if scope.n_objectives == 0
            else "No results were left to compare, so the check makes no finding."
            if scope.n_results == 0 or (scope.n_results is not None and scope.n_results <= left_out)
            else "No result was close enough to an objective to compare, so the check makes "
            "no finding."
        )
        return [reason, "This does not mean the objectives and results agree."]
    if undecided == 0:
        return ["The comparison found no possible inconsistency among the pairs it compared."]
    if undecided >= compared:
        return [
            "The comparison could not decide on the one pair it compared, so it makes no "
            "finding. The pair is listed under Pairs to read yourself."
            if compared == 1
            else "The comparison could not decide on any pair it compared, so it makes no "
            "finding. The pairs are listed under Pairs to read yourself."
        ]
    return [
        "The comparison found no possible inconsistency among the pairs it could decide.",
        "1 pair could not be decided. It is listed under Pairs to read yourself."
        if undecided == 1
        else f"{undecided} pairs could not be decided. They are listed under Pairs to read "
        "yourself.",
    ]


def scope_paragraphs(
    scope: AgreementScopeOut,
    *,
    findings: int,
    undecided: int,
    compared: int | None,
    interrupted: bool,
) -> list[str]:
    copy = agreement_scope_copy()
    lines = [copy["purpose"]]
    if found := found_line(scope):
        lines.append(found)
    if scope.results_section_identified is not False and (setaside := set_aside_lines(scope)):
        lines.append(" ".join(setaside))
    if scope.results_section_identified is False:
        lines.append(copy["not_identified"])
    if outcome := outcome_lines(scope, findings=findings, undecided=undecided, compared=compared):
        lines.append(" ".join(outcome))
    if interrupted:
        lines.append(copy["interrupted"])
    lines.append(copy["limit"])
    if scope.absence_leads_withheld:
        lines.append(copy["absence"])
    if scope.results_section_identified is None:
        lines.append(copy["older_no_sections"])
    return lines
