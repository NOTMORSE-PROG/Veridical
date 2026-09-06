// V-071 AC9: a short, closed-vocabulary label naming WHICH problem a
// flag is (never why it matters or how confident the check was --
// FlagSummaryOut's own docstring reason for keeping ai_reasoning off
// this screen still holds). Sourced from `flag.problem_kind`, the same
// `detail["kind"]` string `flag_ai_verdict_summary` reads server-side
// (backend/app/report/scoring.py). Deliberately a subset: an unmapped
// kind renders no label rather than a guessed one -- extend this table
// from the checks' own `*_WORDING` constants, never invent new copy.
export const PROBLEM_LABEL: Partial<Record<string, string>> = {
  agreement_unmatched_intent: "Possible objective-to-outcome gap",
  retracted_source: "Possibly retracted source",
  corrected_source: "Correction on record for this source",
  unverifiable_api_down: "Verification service was unreachable",
  unverifiable_not_found: "Source not found in the databases checked",
  uncited_reference: "Reference may not be cited in the manuscript body",
  grim_inconsistent: "Reported mean may not fit the sample size",
  grimmer_inconsistent: "Reported SD may not fit the mean and sample size",
  reuse_exact_duplicate: "Possible exact or near-exact reuse",
  reuse_exact_duplicate_chapter: "Possible exact or near-exact section reuse",
  reuse_exact_duplicate_passage: "Possible exact or near-exact passage reuse",
  reuse_high_similarity: "High textual similarity",
  reuse_high_similarity_chapter: "High section similarity",
  reuse_high_similarity_passage: "High passage similarity",
  // BUG-168: extended past the original 14 -- these 12 kinds are real,
  // reachable outcomes of the F4-F7 checks (confirmed via `grep '"kind":'`
  // across `backend/app/checks/`) that had no label at all, falling
  // through to the generic "Possible inconsistency" default. Each derived
  // from its own check's `*_WORDING` constant, never invented copy.
  agreement_contradictory: "Objective and result may contradict each other",
  agreement_partial: "Objective may be only partially addressed",
  agreement_cannot_determine: "Could not determine if the result addresses the objective",
  agreement_injection_suspected: "Objective or result text may address an automated grader",
  claim_possibly_unsupported: "Citation may not support the attached claim",
  claim_support_cannot_determine: "Could not determine if the source supports the claim",
  claim_support_injection_suspected: "Citation text may address an automated grader",
  orphan_in_text_citation: "In-text citation may be missing from the reference list",
  p_value_decision_error: "Reported p-value may change the significance decision",
  percentage_sum_off: "Table percentages may not sum to 100%",
  group_count_exceeds_total: "Group sample sizes may not match the stated total",
  reuse_same_instructor_resubmission: "Possible resubmission of your own earlier upload",
};

export function problemLabel(kind: string | null | undefined): string | null {
  return kind ? (PROBLEM_LABEL[kind] ?? null) : null;
}
