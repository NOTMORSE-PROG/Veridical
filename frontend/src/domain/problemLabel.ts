// V-071 AC9: a short, closed-vocabulary label naming WHICH problem a
// flag is (never why it matters or how confident the check was --
// FlagSummaryOut's own docstring reason for keeping ai_reasoning off
// this screen still holds). Sourced from `flag.problem_kind`, the same
// `detail["kind"]` string `flag_ai_verdict_summary` reads server-side
// (backend/app/report/scoring.py). Deliberately a subset: an unmapped
// kind renders no label rather than a guessed one -- extend this table
// from the checks' own `*_WORDING` constants, never invent new copy.
export const PROBLEM_LABEL: Partial<Record<string, string>> = {
  retracted_source: "Possibly retracted source",
  corrected_source: "Correction on record for this source",
  unverifiable_api_down: "Verification service was unreachable",
  unverifiable_not_found: "Source not found in the databases checked",
  grim_inconsistent: "Reported mean may not fit the sample size",
  grimmer_inconsistent: "Reported SD may not fit the mean and sample size",
};

export function problemLabel(kind: string | null | undefined): string | null {
  return kind ? (PROBLEM_LABEL[kind] ?? null) : null;
}
