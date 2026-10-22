import { expect, it } from "vitest";
import { criterionSourceReturn } from "./criterionSourceReturn";

const origin = { returnPath: "/report/5?criterion_record=all&sentinel=kept", elementId: "signal-saved-source-5-77-0" };
it("accepts only the same-run criterion return and removes its own metadata on return", () => {
  expect(criterionSourceReturn({ notice: "retained", criterionRecordReturn: origin }, 5)).toEqual({ ...origin,
    criterionId: 77, fallbackElementId: "criterion-record-5-77", state: { notice: "retained" } });
});
it.each([
  { returnPath: "https://example.com/report/5" }, { returnPath: "/report/6" },
  { returnPath: "/report/5#foreign" }, { returnPath: "/report/5?x=\n" },
  { elementId: "signal-saved-source-6-77-0" }, { elementId: "signal-saved-source-5-0-0" },
  { elementId: "signal-saved-source-5-9007199254740992-0" }, { elementId: "foreign" },
])("rejects an invalid source-return registration %o", (change) => {
  expect(criterionSourceReturn({ criterionRecordReturn: { ...origin, ...change } }, 5)).toBeUndefined();
});
