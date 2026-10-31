import { describe, expect, it } from "vitest";
import { findingVerificationPrompt } from "./checkKind";
import { notCountedLabel, severityLabel } from "./findingSummary";
import { readinessExclusionLine } from "./readinessExclusion";

describe("a citation recency note", () => {
  it("is called a recency note wherever it is not counted, not a review lead", () => {
    expect(notCountedLabel({ problem_kind: "outside_recency_window" })).toBe("Recency note: not counted");
    expect(notCountedLabel({ ai_verdict_summary: "outside_recency_window" })).toBe("Recency note: not counted");
    expect(severityLabel({ severity: "low", affects_readiness: false, problem_kind: "outside_recency_window" })).toBe("Recency note: not counted");
    expect(notCountedLabel({ problem_kind: "reuse_high_similarity" })).toBe("Review lead: not scored");
  });

  it("asks the instructor to judge the source's age, never to audit it as an error", () => {
    const prompt = findingVerificationPrompt("citation_integrity", "outside_recency_window", false);
    expect(prompt).toContain("decide whether an older source suits the claim it supports");
    expect(prompt).not.toMatch(/confirm that its author, title, year/);
  });

  it("says why a not-counted note is left out of readiness", () => {
    expect(readinessExclusionLine({ affects_readiness: false, readiness_exclusion_reason: "recency_note_not_counted" }))
      .toBe("Not counted for readiness: this format records source age as a note, not as a readiness concern.");
  });
});
