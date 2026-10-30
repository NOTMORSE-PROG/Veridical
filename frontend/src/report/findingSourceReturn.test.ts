import { describe, expect, it } from "vitest";
import { findingSourceReturn } from "./findingSourceReturn";

const origin = { returnPath: "/report/5?section=findings&module=citation_integrity", elementId: "signal-flag-review-7" };

describe("the way back to the report findings a finding was opened from", () => {
  it("keeps the exact report view and the link that opened the finding", () => {
    expect(findingSourceReturn({ routeReturnFocus: origin, notice: "kept" }, 5)).toEqual({ ...origin, state: { notice: "kept" }, retainOnSearchChange: true });
  });

  it("ignores another report, a hash or a control that is not a finding link", () => {
    expect(findingSourceReturn({ routeReturnFocus: origin }, 6)).toBeUndefined();
    expect(findingSourceReturn({ routeReturnFocus: { ...origin, returnPath: "/report/5#x" } }, 5)).toBeUndefined();
    expect(findingSourceReturn({ routeReturnFocus: { ...origin, elementId: "signal-saved-source-5-1-1" } }, 5)).toBeUndefined();
    expect(findingSourceReturn(null, 5)).toBeUndefined();
  });
});
