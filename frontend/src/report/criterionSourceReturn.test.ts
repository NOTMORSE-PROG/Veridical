import { expect, it } from "vitest";
import { criterionSourceReturn } from "./criterionSourceReturn";

const origin = { returnPath: "/report/5?criterion_record=all&sentinel=kept", elementId: "signal-saved-source-5-77-0" };
it("accepts only the same-run criterion return and removes its own metadata on return", () => {
  expect(criterionSourceReturn({ notice: "retained", criterionRecordReturn: origin }, 5)).toEqual({ ...origin,
    kind: "saved", criterionId: 77, fallbackElementId: "criterion-record-5-77", state: { notice: "retained" }, retainOnSearchChange: true });
});
it.each([
  { returnPath: "https://example.com/report/5" }, { returnPath: "/report/6" },
  { returnPath: "/report/5#foreign" }, { returnPath: "/report/5?x=\n" },
  { elementId: "signal-saved-source-6-77-0" }, { elementId: "signal-saved-source-5-0-0" },
  { elementId: "signal-saved-source-5-9007199254740992-0" }, { elementId: "foreign" },
])("rejects an invalid source-return registration %o", (change) => {
  expect(criterionSourceReturn({ criterionRecordReturn: { ...origin, ...change } }, 5)).toBeUndefined();
});

const pendingOrigin = { kind: "pending", returnPath: "/report/5?sentinel=kept", elementId: "signal-resolution-source-5-123-0", checkResultId: 123 };
it.each(["source", "coverage"])("accepts a distinct pending %s result without a saved criterion ID", prefix => {
  const origin = { ...pendingOrigin, elementId: `signal-resolution-${prefix}-5-123-0` };
  const result = criterionSourceReturn({ notice: "retained", pendingCriterionReturn: origin }, 5);
  expect(result).toEqual({ ...origin, state: { notice: "retained" }, retainOnSearchChange: true, fallbackElementId: ["signal-resolution-heading-123", "escalated-heading", "readiness-report-heading"] });
  expect(result).not.toHaveProperty("criterionId");
});
it.each([
  { kind: "saved" }, { checkResultId: 124 }, { checkResultId: "123" },
  { elementId: "signal-resolution-source-6-123-0" }, { elementId: "signal-resolution-source-5-0-0" },
  { elementId: "signal-resolution-source-5-9007199254740992-0", checkResultId: 9007199254740992 },
  { elementId: "signal-resolution-source-5-123-9007199254740992" },
  { elementId: "signal-saved-source-5-123-0" }, { returnPath: "/report/5/document" },
])("rejects invalid pending metadata %o", change => {
  expect(criterionSourceReturn({ pendingCriterionReturn: { ...pendingOrigin, ...change } }, 5)).toBeUndefined();
});
it("rejects arrays and conflicting saved/pending metadata", () => {
  expect(criterionSourceReturn({ criterionRecordReturn: origin, pendingCriterionReturn: pendingOrigin }, 5)).toBeUndefined();
  expect(criterionSourceReturn(Object.assign([], { pendingCriterionReturn: pendingOrigin }), 5)).toBeUndefined();
  expect(criterionSourceReturn({ pendingCriterionReturn: Object.assign([], pendingOrigin) }, 5)).toBeUndefined();
});
