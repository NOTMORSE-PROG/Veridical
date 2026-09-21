import { describe, expect, it } from "vitest";
import type { FlagSummaryOut } from "../api/types";
import { buildReviewFindings, findingForFlag, findingNumberByFlagId } from "./reviewFindings";

function flag(overrides: Partial<FlagSummaryOut> & { id: number }): FlagSummaryOut {
  return {
    check_kind: "originality_reuse",
    severity: "high",
    criterion_text: null,
    evidence_excerpt: `Evidence ${overrides.id}`,
    page_anchor: `p. ${overrides.id}`,
    overridden: false,
    is_passage_level: false,
    first_upload_context: false,
    confirmed_citation_source: false,
    problem_kind: "reuse_high_similarity",
    matched_ref: 3,
    ...overrides,
  };
}

describe("buildReviewFindings", () => {
  it("groups whole-document, chapter, and passage records for one persisted reuse identity", () => {
    const findings = buildReviewFindings([
      flag({ id: 109 }),
      flag({ id: 110, problem_kind: "reuse_high_similarity_chapter" }),
      flag({ id: 111, problem_kind: "reuse_high_similarity_passage", is_passage_level: true }),
    ]);

    expect(findings).toHaveLength(1);
    expect(findings[0].number).toBe(1);
    expect(findings[0].representative.id).toBe(111);
    expect(findings[0].flags.map((member) => member.id)).toEqual([109, 110, 111]);
  });

  it("keeps different sources, base outcomes, and unknown identities separate", () => {
    const findings = buildReviewFindings([
      flag({ id: 1, matched_ref: 3 }),
      flag({ id: 2, matched_ref: 4 }),
      flag({ id: 3, problem_kind: "reuse_exact_duplicate", matched_ref: 3 }),
      flag({ id: 4, problem_kind: null, matched_ref: 3 }),
      flag({ id: 5, problem_kind: null, matched_ref: 3 }),
    ]);

    expect(findings).toHaveLength(5);
    expect(findings.map((finding) => finding.number)).toEqual([1, 2, 3, 4, 5]);
  });

  it("keeps canonical numbers stable while exposing every member id", () => {
    const findings = buildReviewFindings([
      flag({ id: 8, check_kind: "internal_agreement", problem_kind: "agreement_partial", matched_ref: null }),
      flag({ id: 9 }),
      flag({ id: 10, problem_kind: "reuse_high_similarity_passage", is_passage_level: true }),
    ]);
    const numbers = findingNumberByFlagId(findings);

    expect(findings.map((finding) => finding.number)).toEqual([1, 2]);
    expect(numbers.get(9)).toBe(2);
    expect(numbers.get(10)).toBe(2);
    expect(findingForFlag(findings, 10)?.representative.id).toBe(10);
  });

  it("uses the highest stored severity and resolves only when every member is resolved", () => {
    const findings = buildReviewFindings([
      flag({ id: 1, severity: "med", overridden: true }),
      flag({ id: 2, severity: "high", overridden: false, problem_kind: "reuse_high_similarity_passage", is_passage_level: true }),
    ]);

    expect(findings[0].severity).toBe("high");
    expect(findings[0].isResolved).toBe(false);
    expect(findings[0].representative.id).toBe(2);
  });
});

