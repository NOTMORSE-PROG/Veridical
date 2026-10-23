import { describe, expect, it } from "vitest";
import type { FlagSummaryOut } from "../api/types";
import { comparisonLeadCollection } from "./comparisonLeadCollection";
import { ApiError } from "../api/client";

const record = { id: 41, check_kind: "originality_reuse", affects_readiness: false } as FlagSummaryOut;
describe("complete comparison collection state", () => {
  it.each([401, 403, 404, 410])("withholds cached records on access/resource failure %s, including a pending retry", (status) => {
    for (const fetchStatus of ["idle", "fetching", "paused"] as const) {
      const result = comparisonLeadCollection(5, { data: [record], fetchStatus, isError: true, isSuccess: false, error: new ApiError(status, "not_available", "Unavailable") });
      expect(result.snapshot).toBeUndefined();
      expect(result.lastLoaded).toBe(false);
    }
    const transient = comparisonLeadCollection(5, { data: [record], fetchStatus: "idle", isError: true, isSuccess: false, error: new ApiError(503, "unavailable", "Transient") });
    expect(transient.snapshot?.records).toEqual([record]);
  });
  it.each([undefined, [], [record]])("prioritizes fetching then paused ahead of cached error/success (%j)", (data) => {
    const query = { data, isError: true, isSuccess: true };
    for (const fetchStatus of ["fetching", "paused"] as const) {
      const result = comparisonLeadCollection(5, { ...query, fetchStatus });
      expect(result.state).toBe(fetchStatus);
      expect(result.lastLoaded).toBe(Boolean(data?.length));
      expect(result.snapshot?.total).toBe(data?.length ? 1 : undefined);
    }
  });
  it("retains one coherent qualified snapshot through idle failure", () => {
    const result = comparisonLeadCollection(5, { data: [record], fetchStatus: "idle", isError: true, isSuccess: false });
    expect(result.state).toBe("error"); expect(result.lastLoaded).toBe(true);
    expect(result.snapshot).toMatchObject({ runId: 5, total: 1 });
    expect(result.snapshot?.ordinalById.get(41)).toBe(1);
  });
  it("only a complete successful empty response establishes no optional records", () => {
    const success = comparisonLeadCollection(5, { data: [], fetchStatus: "idle", isError: false, isSuccess: true });
    expect(success.state).toBe("success"); expect(success.snapshot?.total).toBe(0);
    const absent = comparisonLeadCollection(6, { fetchStatus: "idle", isError: false, isSuccess: false });
    expect(absent).toEqual({ state: "unavailable", snapshot: undefined, lastLoaded: false });
  });
});
