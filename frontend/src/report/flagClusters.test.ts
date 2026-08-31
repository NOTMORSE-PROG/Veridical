import { describe, expect, it } from "vitest";
import type { FlagSummaryOut } from "../api/types";
import { clusterFlagFindings } from "./flagClusters";

function flag(overrides: Partial<FlagSummaryOut> & { id: number }): FlagSummaryOut {
  return {
    check_kind: "originality_reuse",
    severity: "high",
    criterion_text: null,
    evidence_excerpt: `Passage ${overrides.id}`,
    page_anchor: `p. ${overrides.id}`,
    overridden: false,
    is_passage_level: true,
    first_upload_context: false,
    confirmed_citation_source: false,
    problem_kind: "reuse_exact_duplicate_passage",
    matched_ref: 34,
    ...overrides,
  };
}

describe("clusterFlagFindings", () => {
  it("groups different passages only when their reuse identity is explicit", () => {
    const clusters = clusterFlagFindings([
      flag({ id: 9, evidence_excerpt: "First unique passage" }),
      flag({ id: 3, evidence_excerpt: "Second unique passage" }),
    ]);

    expect(clusters).toHaveLength(1);
    expect(clusters[0].key).toBe("3");
    expect(clusters[0].flags).toHaveLength(2);
  });

  it("keeps different archive references and reuse levels separate", () => {
    const clusters = clusterFlagFindings([
      flag({ id: 1, matched_ref: 34 }),
      flag({ id: 2, matched_ref: 35 }),
      flag({ id: 3, matched_ref: 34, problem_kind: "reuse_exact_duplicate_chapter" }),
    ]);

    expect(clusters).toHaveLength(3);
  });

  it("keeps different problem kinds separate even when citation excerpts match", () => {
    const common = {
      check_kind: "citation_integrity",
      evidence_excerpt: "One reference text",
      is_passage_level: false,
      matched_ref: null,
    };
    const clusters = clusterFlagFindings([
      flag({ ...common, id: 1, problem_kind: "uncited_reference" }),
      flag({ ...common, id: 2, problem_kind: "unverifiable_not_found" }),
    ]);

    expect(clusters).toHaveLength(2);
  });

  it("never guesses an identity when problem kind or reuse reference is missing", () => {
    const clusters = clusterFlagFindings([
      flag({ id: 1, problem_kind: null, matched_ref: 34 }),
      flag({ id: 2, problem_kind: null, matched_ref: 34 }),
      flag({ id: 3, matched_ref: null }),
      flag({ id: 4, matched_ref: null }),
    ]);

    expect(clusters).toHaveLength(4);
  });
});
