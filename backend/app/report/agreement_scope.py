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
    """The comparison's outcome for the PDF. The compared count is coverage,
    never a count of agreeing pairs."""
    if compared is None:
        # With no objectives or no results, nothing could have been compared.
        if scope.n_objectives == 0 or scope.n_results == 0:
            return [
                _nothing_compared(scope),
                "This does not mean the objectives and results agree.",
            ]
        lines = ["This report did not record how many pairs were compared."]
        if findings:
            lines.append(
                f"It found {findings} possible "
                f"{_plural(findings, 'inconsistency', 'inconsistencies')}, listed under Findings."
            )
        if undecided:
            lines.append(
                f"{undecided} {_plural(undecided, 'pair', 'pairs')} could not be decided; "
                f"{_plural(undecided, 'it is', 'they are')} listed under Pairs to read yourself."
            )
        return lines
    if compared == 0:
        return [_nothing_compared(scope), "This does not mean the objectives and results agree."]
    pairs = f"{compared} {_plural(compared, 'pair', 'pairs')}"
    base = (
        f"The check was interrupted. Among the {pairs} it compared before then"
        if interrupted
        else f"Among the {pairs} VERIDICAL matched"
    )
    if findings > 0:
        lines = [
            f"{base}, the comparison found {findings} possible "
            f"{_plural(findings, 'inconsistency', 'inconsistencies')}. "
            f"{_plural(findings, 'It is', 'They are')} listed under Findings."
        ]
        if undecided > 0:
            lines.append(
                f"{undecided} other {_plural(undecided, 'pair', 'pairs')} could not be decided. "
                f"{_plural(undecided, 'It is', 'They are')} listed under Pairs to read yourself."
            )
    elif undecided == 0:
        lines = [f"{base}, the comparison found no possible inconsistency."]
    elif undecided >= compared:
        # Interrupted, the outcome still names the interruption here.
        lead = "The check was interrupted. " if interrupted else ""
        which = "it compared before then" if interrupted else "VERIDICAL matched"
        lines = [
            (
                f"{lead}The comparison could not decide on the one pair {which}, so it makes "
                "no finding. The pair is listed under Pairs to read yourself."
            )
            if compared == 1
            else (
                f"{lead}The comparison could not decide on any of the {pairs} {which}, so it "
                "makes no finding. The pairs are listed under Pairs to read yourself."
            )
        ]
    else:
        lines = [
            f"{base}, the comparison found no possible inconsistency in the pairs it could decide.",
            f"{undecided} {_plural(undecided, 'pair', 'pairs')} could not be decided. "
            f"{_plural(undecided, 'It is', 'They are')} listed under Pairs to read yourself.",
        ]
    if interrupted:
        lines.append(agreement_scope_copy()["interrupted_tail"])
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
    # With pairs compared, the outcome already names the interruption; with
    # every pair skipped, the nothing-compared reason does.
    if interrupted and not compared and not (compared == 0 and scope.n_pairs_skipped):
        lines.append(f"{copy['interrupted']} {copy['interrupted_tail']}")
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
    """Why no pair was compared, said only as far as the record shows it."""
    if scope.n_pairs_skipped:
        return "The check was interrupted before it compared any pair, so it makes no finding."
    if scope.n_objectives == 0:
        return "No objectives were found, so nothing was compared and the check makes no finding."
    if scope.n_results == 0:
        return "No results were left to compare, so the check makes no finding."
    if scope.n_results_set_aside is None or scope.n_results_too_short is None:
        return "No pair was compared, so the check makes no finding."
    if scope.n_results is not None and scope.n_results <= (
        scope.n_results_set_aside + scope.n_results_too_short
    ):
        return "No results were left to compare, so the check makes no finding."
    return "No result was close enough to an objective to compare, so the check makes no finding."
