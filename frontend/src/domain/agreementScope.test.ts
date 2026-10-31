import { describe, expect, it } from "vitest";
import copy from "../../../backend/app/report/data/agreement_scope.json";
import type { AgreementScopeOut } from "../api/types";
import { AGREEMENT_SCOPE_COPY, countInconsistencies, foundLine, interruptedLine, matchedLine, outcomeLines, rolePhrase, setAsideLines } from "./agreementScope";

const scope = (patch: Partial<AgreementScopeOut> = {}): AgreementScopeOut => ({
  n_objectives: 17, n_results: 34, results_section_identified: true, n_results_set_aside: 3, n_results_too_short: 2,
  set_aside_by_role: [{ role: "literature", count: 3 }], set_aside: [], absence_leads_withheld: true, ...patch,
});

describe("agreement scope copy", () => {
  it("matches the PDF export's copy word for word", () => {
    expect(AGREEMENT_SCOPE_COPY.heading).toBe(copy.heading);
    expect(AGREEMENT_SCOPE_COPY.setAsideListHeading).toBe(copy.set_aside_list_heading);
    expect(AGREEMENT_SCOPE_COPY.limit).toBe(copy.limit);
    expect(AGREEMENT_SCOPE_COPY.absence).toBe(copy.absence);
    expect(AGREEMENT_SCOPE_COPY.interrupted).toBe(copy.interrupted);
    expect(AGREEMENT_SCOPE_COPY.interruptedTail).toBe(copy.interrupted_tail);
    expect(AGREEMENT_SCOPE_COPY.notIdentified).toBe(copy.not_identified);
    expect(AGREEMENT_SCOPE_COPY.olderNoScope).toBe(copy.older_no_scope);
    expect(AGREEMENT_SCOPE_COPY.matchingHeading).toBe(copy.matching_heading);
    expect(AGREEMENT_SCOPE_COPY.matchingCaveat).toBe(copy.matching_caveat);
    for (const [role, [phrase]] of Object.entries(copy.roles)) expect(rolePhrase(role)).toBe(phrase);
    const folded = scope({ set_aside_by_role: Array.from({ length: copy.role_folding.fold_at }, (_, i) => ({ role: `part${i}`, count: 1 })), n_results_too_short: 0 });
    expect(setAsideLines(folded)).toHaveLength(copy.role_folding.shown + 1);
  });

  it("reads report 44 as coverage, with the compared count and never an agreeing count", () => {
    expect(foundLine(scope())).toBe("Found 17 possible objectives and 34 possible results.");
    expect(setAsideLines(scope())).toEqual([
      "3 results from the literature review were set aside, because that part of a paper usually describes other studies.",
      "2 results were set aside as too short to compare.",
    ]);
    const outcome = outcomeLines(scope(), { findings: 0, undecided: 0, compared: 31, surface: "screen" });
    expect(outcome).toEqual(["Among the 31 pairs VERIDICAL matched, the comparison found no possible inconsistency."]);
    expect(outcome.join(" ")).not.toMatch(/consistent pair|agreed|agree with/);
  });

  it("says when fewer objectives were matched than found", () => {
    expect(matchedLine(scope({ n_objectives_matched: 9 }))).toBe("9 of the 17 objectives were matched to at least one result. One objective can be paired with more than one result.");
    expect(matchedLine(scope({ n_objectives_matched: 1 }))).toMatch(/^1 of the 17 objectives was matched/);
    expect(matchedLine(scope({ n_objectives_matched: 0 }))).toBe("None of the 17 objectives were matched to a result.");
    expect(matchedLine(scope({ n_objectives_matched: 17 }))).toBeNull();
    expect(matchedLine(scope())).toBeNull();
  });

  it("words one of anything in the singular", () => {
    expect(foundLine(scope({ n_objectives: 1, n_results: 0 }))).toBe("Found 1 possible objective and no possible results.");
    expect(setAsideLines(scope({ set_aside_by_role: [{ role: "methodology", count: 1 }], n_results_too_short: 1 }))).toEqual([
      "1 result from the methodology was set aside, because that part of a paper usually describes how the study was done.",
      "1 result was set aside as too short to compare.",
    ]);
    expect(outcomeLines(scope(), { findings: 1, undecided: 0, compared: 1, surface: "screen" })).toEqual([
      "Among the 1 pair VERIDICAL matched, the comparison found 1 possible inconsistency. It is listed below.",
    ]);
  });

  it("says where findings and undecided pairs are, by surface", () => {
    expect(outcomeLines(scope(), { findings: 1, undecided: 2, compared: 9, surface: "screen" })).toEqual([
      "Among the 9 pairs VERIDICAL matched, the comparison found 1 possible inconsistency. It is listed below.",
      "2 other pairs could not be decided. They are listed after the findings.",
    ]);
    expect(outcomeLines(scope(), { findings: 0, undecided: 2, compared: 9, surface: "screen" })).toEqual([
      "Among the 9 pairs VERIDICAL matched, the comparison found no possible inconsistency in the pairs it could decide.",
      "2 pairs could not be decided. They are listed below for you to read.",
    ]);
    expect(outcomeLines(scope(), { findings: 0, undecided: 2, compared: 9, surface: "adviser" })[1])
      .toBe("2 pairs could not be decided. They are not findings; the instructor's report lists them.");
    expect(outcomeLines(scope(), { findings: 0, undecided: 2, compared: 9, surface: "document" })[1])
      .toBe("2 pairs could not be decided. Read them on the readiness report.");
    expect(outcomeLines(scope(), { findings: 0, undecided: 2, compared: 2, surface: "screen" })).toEqual([
      "The comparison could not decide on any of the 2 pairs VERIDICAL matched, so it makes no finding. They are listed below for you to read.",
    ]);
  });

  it("names an interruption once, in the outcome when pairs were compared", () => {
    expect(outcomeLines(scope({ n_pairs_skipped: 3 }), { findings: 0, undecided: 0, compared: 5, interrupted: true, surface: "screen" })).toEqual([
      "The check was interrupted. Among the 5 pairs it compared before then, the comparison found no possible inconsistency.",
      "Pairs it did not compare are not treated as passed.",
    ]);
    expect(interruptedLine(scope({ n_pairs_skipped: 3 }), { compared: 5, interrupted: true, outcomeShown: true })).toBeNull();
    // Every compared pair undecided: the outcome still says it was interrupted.
    expect(outcomeLines(scope({ n_pairs_skipped: 2 }), { findings: 0, undecided: 2, compared: 2, interrupted: true, surface: "screen" })).toEqual([
      "The check was interrupted. The comparison could not decide on any of the 2 pairs it compared before then, so it makes no finding. They are listed below for you to read.",
      "Pairs it did not compare are not treated as passed.",
    ]);
    // Every pair skipped: the reason itself names the interruption.
    expect(outcomeLines(scope({ n_pairs_skipped: 6 }), { findings: 0, undecided: 0, compared: 0, interrupted: true, surface: "screen" })[0])
      .toBe("The check was interrupted before it compared any pair, so it makes no finding.");
    expect(interruptedLine(scope({ n_pairs_skipped: 6 }), { compared: 0, interrupted: true, outcomeShown: true })).toBeNull();
    // Unknown compared count, or the outcome still loading: said on its own line.
    expect(interruptedLine(scope({ n_pairs_skipped: 2 }), { compared: null, interrupted: true, outcomeShown: true }))
      .toBe("The check was interrupted before it compared every pair. Pairs it did not compare are not treated as passed.");
    expect(interruptedLine(scope(), { compared: 5, interrupted: true, outcomeShown: false })).not.toBeNull();
  });

  it("never claims agreement when nothing was compared", () => {
    expect(outcomeLines(scope({ n_objectives: 0 }), { findings: 0, undecided: 0, compared: 0, surface: "screen" })).toEqual([
      "No objectives were found, so nothing was compared and the check makes no finding.",
      "This does not mean the objectives and results agree.",
    ]);
    expect(outcomeLines(scope({ n_results_set_aside: null }), { findings: 0, undecided: 0, compared: 0, surface: "screen" })[0])
      .toBe("No pair was compared, so the check makes no finding.");
    expect(outcomeLines(scope({ n_results: 5 }), { findings: 0, undecided: 0, compared: 0, surface: "screen" })[0])
      .toBe("No results were left to compare, so the check makes no finding.");
  });

  it("says when a report did not record how many pairs were compared", () => {
    expect(outcomeLines(scope(), { findings: 0, undecided: 0, compared: null, surface: "screen" }))
      .toEqual(["This report did not record how many pairs were compared."]);
    expect(outcomeLines(scope({ n_results: 0 }), { findings: 0, undecided: 0, compared: null, surface: "screen" })[0])
      .toBe("No results were left to compare, so the check makes no finding.");
    expect(outcomeLines(scope(), { findings: 2, undecided: 1, compared: null, surface: "screen" })).toEqual([
      "This report did not record how many pairs were compared.",
      "It found 2 possible inconsistencies. They are listed below.",
      "1 pair could not be decided. It is listed after the findings.",
    ]);
  });

  it("counts only contradictions and partial matches as possible inconsistencies", () => {
    expect(countInconsistencies([
      { check_kind: "internal_agreement", problem_kind: "agreement_contradictory" },
      { check_kind: "internal_agreement", problem_kind: "agreement_partial" },
      { check_kind: "internal_agreement", problem_kind: "agreement_injection_suspected" },
      { check_kind: "internal_agreement", problem_kind: null },
      { check_kind: "citation_integrity", problem_kind: "agreement_partial" },
    ])).toBe(2);
  });
});
