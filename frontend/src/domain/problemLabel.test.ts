import { describe, expect, it } from "vitest";
import { PROBLEM_LABEL, problemLabel } from "./problemLabel";

// BUG-168: the whole point of this table is that a flag card states the
// problem, not just the student's excerpt -- an unmapped kind silently
// falls through to a generic "Possible inconsistency" default (honest,
// per this file's own docstring, but far less informative than it could
// be). This list mirrors every literal `detail["kind"]` value the F4-F7
// checks can actually produce, confirmed via `grep -rn '"kind":'` across
// `backend/app/checks/` (reuse's three duplicate/similarity kinds are
// f-string-constructed, not literal, but resolve to the same 6 values
// listed here). Keep this list -- and the table it checks -- in sync with
// the backend the same way this file's own docstring already asks:
// "extend this table from the checks' own `*_WORDING` constants."
const EXPECTED_REACHABLE_KINDS = [
  // F4 internal agreement (checks/agreement/pair.py)
  "agreement_unmatched_intent",
  "agreement_contradictory",
  "agreement_partial",
  "agreement_cannot_determine",
  "agreement_injection_suspected",
  // F5 citation integrity (checks/citations/verify.py, extract.py, support.py)
  "retracted_source",
  "corrected_source",
  "unverifiable_api_down",
  "unverifiable_not_found",
  "orphan_in_text_citation",
  "uncited_reference",
  "claim_possibly_unsupported",
  "claim_support_cannot_determine",
  "claim_support_injection_suspected",
  // F6 statistical forensics (checks/forensics/checks.py, pcheck.py, sanity.py)
  "grim_inconsistent",
  "grimmer_inconsistent",
  "p_value_decision_error",
  "percentage_sum_off",
  "group_count_exceeds_total",
  // F7 originality/reuse (checks/reuse/service.py)
  "reuse_exact_duplicate",
  "reuse_exact_duplicate_chapter",
  "reuse_exact_duplicate_passage",
  "reuse_high_similarity",
  "reuse_high_similarity_chapter",
  "reuse_high_similarity_passage",
  "reuse_same_instructor_resubmission",
];

describe("problemLabel", () => {
  it.each(EXPECTED_REACHABLE_KINDS)("has a real label for %s", (kind) => {
    expect(problemLabel(kind)).not.toBeNull();
    expect(problemLabel(kind)).not.toBe("");
  });

  it("covers exactly the reachable kind set, so a removed backend kind is noticed too", () => {
    expect(Object.keys(PROBLEM_LABEL).sort()).toEqual([...EXPECTED_REACHABLE_KINDS].sort());
  });

  it("returns null, not a guessed label, for an unrecognized kind", () => {
    expect(problemLabel("some_future_check_kind_not_yet_mapped")).toBeNull();
  });

  it("returns null for a missing kind rather than throwing", () => {
    expect(problemLabel(null)).toBeNull();
    expect(problemLabel(undefined)).toBeNull();
    expect(problemLabel("")).toBeNull();
  });

  it("never uses accusatory language, matching the checks' own honest-wording discipline", () => {
    for (const label of Object.values(PROBLEM_LABEL)) {
      const lowered = (label ?? "").toLowerCase();
      expect(lowered).not.toContain("fake");
      expect(lowered).not.toContain("fabricat");
      expect(lowered).not.toContain("lied");
      expect(lowered).not.toContain("dishonest");
      expect(lowered).not.toContain("cheat");
    }
  });
});
