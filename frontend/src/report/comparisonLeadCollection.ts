import type { FlagSummaryOut } from "../api/types";
import { comparisonLeadSnapshot, type ComparisonLeadSnapshot } from "../domain/comparisonLeadIdentity";

export type ComparisonLeadState = "fetching" | "paused" | "error" | "success" | "unavailable";
export interface ComparisonLeadCollection {
  state: ComparisonLeadState;
  snapshot?: ComparisonLeadSnapshot;
  lastLoaded: boolean;
}

/** Data is the complete same-run flags endpoint result, including retained data. */
export function comparisonLeadCollection(runId: number, query: {
  data?: FlagSummaryOut[];
  fetchStatus: "fetching" | "paused" | "idle";
  isError: boolean;
  isSuccess: boolean;
}): ComparisonLeadCollection {
  const state = query.fetchStatus === "fetching" ? "fetching"
    : query.fetchStatus === "paused" ? "paused"
      : query.isError ? "error"
        : query.isSuccess && query.data !== undefined ? "success" : "unavailable";
  const data = query.data === undefined ? undefined : comparisonLeadSnapshot(runId, query.data);
  const snapshot = state === "success" || data?.total ? data : undefined;
  return { state, snapshot, lastLoaded: state !== "success" && snapshot !== undefined };
}
