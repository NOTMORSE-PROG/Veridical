import type { StatisticalCheckScopeOut } from "../api/types";

export function descriptiveAssumptionLimit(scope?: StatisticalCheckScopeOut | null): string | undefined {
  const count = scope?.unassessed_descriptive_rows;
  if (!Number.isSafeInteger(count) || count === undefined || count <= 0) return;
  return `${count} descriptive ${count === 1 ? "row was" : "rows were"} not assessed by GRIM/GRIMMER because single-item score assumptions were not established. Review the score scale and number of items in the original tables. Independently supported statistical findings remain available.`;
}
