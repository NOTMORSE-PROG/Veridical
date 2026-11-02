import { describe, expect, it } from "vitest";
import copy from "../../../backend/app/report/data/agreement_scope.json";
import type { AgreementScopeOut } from "../api/types";
import { AGREEMENT_SCOPE_COPY, countInconsistencies, foundLine, interruptedLine, matchedLine, objectiveSetAsideLines, outcomeLines, rolePhrase, setAsideLines } from "./agreementScope";

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
    expect(AGREEMENT_SCOPE_COPY.reviewLead).toBe(copy.review_lead);
    expect(AGREEMENT_SCOPE_COPY.reviewLeads).toBe(copy.review_leads);
    expect(AGREEMENT_SCOPE_COPY.noReviewLeads).toBe(copy.no_review_leads);
    expect(AGREEMENT_SCOPE_COPY.comparisonUnknown).toBe(copy.comparison_unknown);
    expect(AGREEMENT_SCOPE_COPY.comparedCount).toBe(copy.compared_count);
    expect(AGREEMENT_SCOPE_COPY.unresolvedCount).toBe(copy.unresolved_count);
    expect(AGREEMENT_SCOPE_COPY.noAiComparison).toBe(copy.no_ai_comparison);
    expect(AGREEMENT_SCOPE_COPY.noAgreementInference).toBe(copy.no_agreement_inference);
    for (const [role, [phrase]] of Object.entries(copy.roles)) expect(rolePhrase(role)).toBe(phrase);
    const folded = scope({ set_aside_by_role: Array.from({ length: copy.role_folding.fold_at }, (_, i) => ({ role: `part${i}`, count: 1 })), n_results_too_short: 0 });
    expect(setAsideLines(folded)).toHaveLength(copy.role_folding.shown + 1);
    expect(AGREEMENT_SCOPE_COPY.objectiveOther).toBe(copy.objective_other);
    expect(AGREEMENT_SCOPE_COPY.objectiveSetAsideListHeading).toBe(copy.objective_set_aside_list_heading);
    expect(AGREEMENT_SCOPE_COPY.objectiveSetAsideListIntro).toBe(copy.objective_set_aside_list_intro);
    expect(AGREEMENT_SCOPE_COPY.objectivesKeptDespiteRole).toBe(copy.objectives_kept_despite_role);
    expect(AGREEMENT_SCOPE_COPY.objectivesNotIdentified).toBe(copy.objectives_not_identified);
    for (const [role, reason] of Object.entries(copy.objective_roles)) {
      expect(objectiveSetAsideLines(scope({ n_objectives_set_aside: 2, objectives_set_aside_by_role: [{ role, count: 2 }] }))[0]).toContain(`usually ${reason}.`);
    }
  });

  it("lists the objectives set aside by part of the paper, with why", () => {
    const s = scope({ n_objectives: 5, n_objectives_extracted: 9, n_objectives_set_aside: 4, objectives_section_identified: true,
      objectives_set_aside_by_role: [{ role: "literature", count: 3 }, { role: "appendix", count: 1 }] });
    expect(foundLine(s)).toBe("Found 9 possible objectives and 34 possible results. Of the objectives, 5 were kept to compare and 4 were set aside.");
    expect(objectiveSetAsideLines(s)).toEqual([
      "3 possible objectives from the literature review were set aside, because that part of a paper usually describes other studies.",
      "1 possible objective from the appendices was set aside, because that part of a paper usually holds supporting material.",
    ]);
  });

  it("says why no objective was set aside, and nothing for an older run", () => {
    expect(objectiveSetAsideLines(scope({ n_objectives: 4, n_objectives_kept_despite_role: 4 })))
      .toEqual([copy.objectives_kept_despite_role.replace("{n}", "4")]);
    expect(objectiveSetAsideLines(scope({ objectives_section_identified: false }))).toEqual([copy.objectives_not_identified]);
    expect(objectiveSetAsideLines(scope())).toEqual([]);
    expect(foundLine(scope())).toBe("Found 17 possible objectives and 34 possible results.");
  });

  it("reads report 44 as coverage, with the compared count and never an agreeing count", () => {
    expect(foundLine(scope())).toBe("Found 17 possible objectives and 34 possible results.");
    expect(setAsideLines(scope())).toEqual([
      "3 results from the literature review were set aside, because that part of a paper usually describes other studies.",
      "2 results were set aside as too short to compare.",
    ]);
    const outcome = outcomeLines(scope(), { findings: 0, undecided: 0, compared: 31, surface: "screen" });
    expect(outcome).toEqual(["Pairs compared with AI: 31.", "No internal agreement review lead was recorded."]);
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
      "Pairs compared with AI: 1.",
      "1 internal agreement review lead. It is listed below.",
    ]);
  });

  it("says where findings and undecided pairs are, by surface", () => {
    expect(outcomeLines(scope(), { findings: 1, undecided: 2, compared: 9, surface: "screen" })).toEqual([
      "Pairs compared with AI: 9.",
      "1 internal agreement review lead. It is listed below.",
      "Selected pairs left unresolved: 2. They are listed after the findings.",
    ]);
    expect(outcomeLines(scope(), { findings: 0, undecided: 2, compared: 9, surface: "screen" })).toEqual([
      "Pairs compared with AI: 9.",
      "No internal agreement review lead was recorded.",
      "Selected pairs left unresolved: 2. They are listed below for you to read.",
    ]);
    expect(outcomeLines(scope(), { findings: 0, undecided: 2, compared: 9, surface: "adviser" })[2])
      .toBe("Selected pairs left unresolved: 2. They are not findings and do not change readiness.");
    expect(outcomeLines(scope(), { findings: 0, undecided: 2, compared: 9, surface: "document" })[2])
      .toBe("Selected pairs left unresolved: 2. Read them on the readiness report.");
    expect(outcomeLines(scope(), { findings: 0, undecided: 2, compared: 2, surface: "screen" })).toEqual([
      "Pairs compared with AI: 2.",
      "No internal agreement review lead was recorded.",
      "Selected pairs left unresolved: 2. They are listed below for you to read.",
    ]);
  });

  it("names an interruption once, in the outcome when pairs were compared", () => {
    expect(outcomeLines(scope({ n_pairs_skipped: 3 }), { findings: 0, undecided: 0, compared: 5, interrupted: true, surface: "screen" })).toEqual([
      "Pairs compared with AI: 5.",
      "No internal agreement review lead was recorded.",
      "The check was interrupted before it compared every pair.",
      "Pairs it did not compare are not treated as passed.",
    ]);
    expect(interruptedLine(scope({ n_pairs_skipped: 3 }), { compared: 5, interrupted: true, outcomeShown: true })).toBeNull();
    // Unresolved pairs can include local abstentions, so no subset is inferred.
    expect(outcomeLines(scope({ n_pairs_skipped: 2 }), { findings: 0, undecided: 2, compared: 2, interrupted: true, surface: "screen" })).toEqual([
      "Pairs compared with AI: 2.",
      "No internal agreement review lead was recorded.",
      "Selected pairs left unresolved: 2. They are listed below for you to read.",
      "The check was interrupted before it compared every pair.",
      "Pairs it did not compare are not treated as passed.",
    ]);
    const none = outcomeLines(scope({ n_pairs_skipped: 6 }), { findings: 0, undecided: 0, compared: 0, interrupted: true, surface: "screen" });
    expect(none).toContain("No pair was compared with AI.");
    expect(none).toContain("The check was interrupted before it compared every pair.");
    expect(interruptedLine(scope({ n_pairs_skipped: 6 }), { compared: 0, interrupted: true, outcomeShown: true })).toBeNull();
    // Even an unknown count has the interruption in a shown outcome.
    expect(interruptedLine(scope({ n_pairs_skipped: 2 }), { compared: null, interrupted: true, outcomeShown: true }))
      .toBeNull();
    expect(interruptedLine(scope(), { compared: 5, interrupted: true, outcomeShown: false })).not.toBeNull();
  });

  it("never claims agreement when nothing was compared", () => {
    expect(outcomeLines(scope({ n_objectives: 0 }), { findings: 0, undecided: 0, compared: 0, surface: "screen" })).toEqual([
      "Pairs compared with AI: 0.",
      "No pair was compared with AI.",
      "This does not mean the objectives and results agree.",
    ]);
    for (const value of [scope({ n_results_set_aside: null }), scope({ n_results: 5 })]) {
      const lines = outcomeLines(value, { findings: 0, undecided: 0, compared: 0, surface: "screen" });
      expect(lines).toContain("No pair was compared with AI.");
      expect(lines.join(" ")).not.toMatch(/no results were left|close enough/i);
    }
  });

  it("says when a report did not record how many pairs were compared", () => {
    expect(outcomeLines(scope(), { findings: 0, undecided: 0, compared: null, surface: "screen" }))
      .toEqual(["The number of pairs compared with AI was not recorded."]);
    expect(outcomeLines(scope({ n_results: 0 }), { findings: 0, undecided: 0, compared: null, surface: "screen" })[0])
      .toBe("The number of pairs compared with AI was not recorded.");
    expect(outcomeLines(scope(), { findings: 2, undecided: 1, compared: null, surface: "screen" })).toEqual([
      "The number of pairs compared with AI was not recorded.",
      "2 internal agreement review leads. They are listed below.",
      "Selected pairs left unresolved: 1. It is listed after the findings.",
    ]);
  });

  it("counts factual and attainment leads without folding in unresolved or injection records", () => {
    expect(countInconsistencies([
      { check_kind: "internal_agreement", problem_kind: "agreement_contradictory" },
      { check_kind: "internal_agreement", problem_kind: "agreement_partial" },
      { check_kind: "internal_agreement", problem_kind: "agreement_factual_conflict" },
      { check_kind: "internal_agreement", problem_kind: "agreement_unmet" },
      { check_kind: "internal_agreement", problem_kind: "agreement_cannot_determine" },
      { check_kind: "internal_agreement", problem_kind: "agreement_injection_suspected" },
      { check_kind: "internal_agreement", problem_kind: null },
      { check_kind: "citation_integrity", problem_kind: "agreement_partial" },
    ])).toBe(4);
    for (const kind of copy.review_lead_kinds) {
      expect(countInconsistencies([{ check_kind: "internal_agreement", problem_kind: kind }])).toBe(1);
    }
  });
});
