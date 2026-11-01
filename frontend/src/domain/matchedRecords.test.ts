import { expect, it } from "vitest";
import { matchedRecords, recordList } from "./matchedRecords";

it("lists every matched record once, in order, and falls back to the single saved one", () => {
  expect(matchedRecords({ matched_ref: 10, matched_refs: [10, 12, 12, 14] })).toEqual([10, 12, 14]);
  expect(matchedRecords({ matched_ref: 4 })).toEqual([4]);
  expect(matchedRecords({ matched_ref: null, matched_refs: [] })).toEqual([]);
});

it("names one, two, several, and more than the cap the same way everywhere", () => {
  expect(recordList([10])).toBe("#10");
  expect(recordList([10, 12])).toBe("#10 and #12");
  expect(recordList([10, 12, 14])).toBe("#10, #12 and #14");
  expect(recordList([1, 2, 3, 4, 5, 6, 7])).toBe("#1, #2, #3, #4, #5 and 2 more");
});
