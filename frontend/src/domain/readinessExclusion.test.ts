import { describe, expect, it } from "vitest";
import { readinessExclusionLine } from "./readinessExclusion";

describe("readiness exclusion line", () => {
  it("says why a cited retraction notice does not count", () => {
    expect(readinessExclusionLine({ affects_readiness: false, readiness_exclusion_reason: "retraction_notice_not_counted" }))
      .toBe("Not counted for readiness: a retraction notice is not itself a retracted paper. Check which one the entry means to cite.");
  });
  it("says why an OpenAlex-only retraction lead does not count", () => {
    expect(readinessExclusionLine({ affects_readiness: false, readiness_exclusion_reason: "retraction_lead_not_counted" }))
      .toBe("Not counted for readiness: this lead comes from one database only. Check it yourself.");
  });
  it("adds nothing for a finding that counts", () => {
    expect(readinessExclusionLine({ affects_readiness: true, readiness_exclusion_reason: null })).toBeNull();
  });
});
