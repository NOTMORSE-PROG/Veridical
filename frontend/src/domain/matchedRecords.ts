// BUG-674: a finding can match several Library records (a resubmission can
// match more than one of the instructor's own). Every place that names them
// reads them from here, so one record and many are said the same way.

/** How many records are named before the rest are counted. A layout guard
 * only: the full list is the evidence. */
export const MATCHED_RECORDS_SHOWN = 5;

/** Every matched record, in match order; older findings saved only the first. */
export function matchedRecords(flag: { matched_ref?: number | null; matched_refs?: number[] }): number[] {
  const refs = (flag.matched_refs ?? []).filter(ref => Number.isSafeInteger(ref) && ref > 0);
  if (refs.length) return [...new Set(refs)];
  return flag.matched_ref != null && flag.matched_ref > 0 ? [flag.matched_ref] : [];
}

/** "#10", "#10 and #12", "#10, #12 and #14", then "and 2 more" past the cap. */
export function recordList(refs: number[], cap = MATCHED_RECORDS_SHOWN): string {
  const shown = refs.slice(0, cap).map(ref => `#${ref}`);
  const more = refs.length - shown.length;
  if (more > 0) return `${shown.join(", ")} and ${more} more`;
  return shown.length <= 1 ? shown.join("") : `${shown.slice(0, -1).join(", ")} and ${shown[shown.length - 1]}`;
}
