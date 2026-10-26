// F8, screens 4h/4w: criterion selection belongs to one report route family.
export const SELECTED_CRITERION_PARAM = "criterion";

export function selectedCriterionId(raw: string | null): number | null {
  if (raw === null || !/^[1-9]\d*$/.test(raw)) return null;
  const value = Number(raw);
  return Number.isSafeInteger(value) ? value : null;
}

export function criterionReviewSearch(search: string, criterionId?: number) {
  const params = new URLSearchParams(search);
  for (const key of ["finding", "flag", "location", "anchor", "panel", "match", "criterion_record"]) params.delete(key);
  params.set("view", "decision");
  params.set("pane", "review");
  if (criterionId === undefined) params.delete(SELECTED_CRITERION_PARAM);
  else params.set(SELECTED_CRITERION_PARAM, String(criterionId));
  return params.toString();
}
