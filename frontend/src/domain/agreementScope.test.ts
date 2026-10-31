import { describe, expect, it } from "vitest";
import copy from "../../../backend/app/report/data/agreement_scope.json";
import type { AgreementScopeOut } from "../api/types";
import { AGREEMENT_SCOPE_COPY, foundLine, outcomeLines, rolePhrase, setAsideLines } from "./agreementScope";

const scope = (patch: Partial<AgreementScopeOut> = {}): AgreementScopeOut => ({
  n_objectives: 17, n_results: 34, results_section_identified: true, n_results_set_aside: 3, n_results_too_short: 2,
  set_aside_by_role: [{ role: "literature", count: 3 }], set_aside: [], absence_leads_withheld: true, ...patch,
});

describe("agreement scope copy", () => {
  it("matches the PDF export's copy word for word", () => {
    expect(AGREEMENT_SCOPE_COPY.heading).toBe(copy.heading);
    expect(AGREEMENT_SCOPE_COPY.purpose).toBe(copy.purpose);
    expect(AGREEMENT_SCOPE_COPY.setAsideListHeading).toBe(copy.set_aside_list_heading);
    expect(AGREEMENT_SCOPE_COPY.limit).toBe(copy.limit);
    expect(AGREEMENT_SCOPE_COPY.absence).toBe(copy.absence);
    expect(AGREEMENT_SCOPE_COPY.interrupted).toBe(copy.interrupted);
    expect(AGREEMENT_SCOPE_COPY.notIdentified).toBe(copy.not_identified);
    expect(AGREEMENT_SCOPE_COPY.olderNoSections).toBe(copy.older_no_sections);
    expect(AGREEMENT_SCOPE_COPY.olderNoScope).toBe(copy.older_no_scope);
    for (const [role, [phrase]] of Object.entries(copy.roles)) expect(rolePhrase(role)).toBe(phrase);
  });

  it("reads report 44 as coverage, never as a count of agreeing pairs", () => {
    expect(foundLine(scope())).toBe("Found 17 possible objectives and 34 possible results.");
    expect(setAsideLines(scope())).toEqual([
      "3 results from the literature review were set aside, because that part of a paper usually describes other studies.",
      "2 results were set aside as too short to compare.",
    ]);
    const outcome = outcomeLines(scope(), { findings: 0, undecided: 0, compared: 31, surface: "screen" });
    expect(outcome).toEqual(["The comparison found no possible inconsistency among the pairs it compared."]);
    expect(outcome.join(" ")).not.toMatch(/\b31\b|consistent pair|agree/);
  });

  it("words one of anything in the singular", () => {
    expect(foundLine(scope({ n_objectives: 1, n_results: 0 }))).toBe("Found 1 possible objective and no possible results.");
    expect(setAsideLines(scope({ set_aside_by_role: [{ role: "methodology", count: 1 }], n_results_too_short: 1 }))).toEqual([
      "1 result from the methodology was set aside, because that part of a paper usually describes how the study was done.",
      "1 result was set aside as too short to compare.",
    ]);
  });

  it("says where findings and undecided pairs are, by surface", () => {
    expect(outcomeLines(scope(), { findings: 1, undecided: 2, compared: 9, surface: "screen" })).toEqual([
      "The comparison found 1 possible inconsistency. It is listed below.",
      "2 other pairs could not be decided. They are listed after the findings.",
    ]);
    expect(outcomeLines(scope(), { findings: 0, undecided: 2, compared: 9, surface: "screen" })).toEqual([
      "The comparison found no possible inconsistency among the pairs it could decide.",
      "2 pairs could not be decided. They are listed below for you to read.",
    ]);
    // The adviser view keeps its own undecided sentence.
    expect(outcomeLines(scope(), { findings: 0, undecided: 2, compared: 9, surface: "adviser" })).toEqual([
      "The comparison found no possible inconsistency among the pairs it could decide.",
    ]);
    expect(outcomeLines(scope(), { findings: 0, undecided: 2, compared: 2, surface: "screen" })).toEqual([
      "The comparison could not decide on any pair it compared, so it makes no finding. The pairs are listed below for you to read.",
    ]);
  });

  it("never claims agreement when nothing was compared", () => {
    expect(outcomeLines(scope({ n_objectives: 0 }), { findings: 0, undecided: 0, compared: 0, surface: "screen" })).toEqual([
      "No objectives were found, so nothing was compared and the check makes no finding.",
      "This does not mean the objectives and results agree.",
    ]);
    expect(outcomeLines(scope(), { findings: 0, undecided: 0, compared: null, surface: "screen" })).toEqual([]);
  });
});
