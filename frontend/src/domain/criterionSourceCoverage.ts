import type { CriterionResultOut, CriterionSourceCoverage, ResultRowCommon } from "../api/types";

function unknown(state: "not_recorded" | "unavailable"): CriterionSourceCoverage {
  return { native_math_coverage_version: null, native_math_inventory_state: state,
    native_math_inventory_scope: null, n_native_equations_unassessed: null,
    n_native_equations_unlocated: null, native_math_excluded_paragraphs: [], native_math_coverage_items: [] };
}
const count = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const flagValid = (flag: unknown) => flag === undefined || typeof flag === "boolean";
const pagesValid = (pages: unknown) => pages === undefined
  || (Array.isArray(pages) && pages.every(page => count(page) && page > 0) && new Set(pages).size === pages.length);

export function privateCriterionCoverage(row: ResultRowCommon, publicView: boolean): CriterionSourceCoverage | null {
  if (publicView) return null;
  const value = (row as CriterionResultOut).source_coverage;
  if (value == null) return row.kind === "semantic" && (row.type === "semantic" || row.type === "structural") ? unknown("not_recorded") : null;
  if (value.native_math_coverage_version !== "v1" && value.native_math_coverage_version !== null) return unknown("unavailable");
  if (!Array.isArray(value.native_math_coverage_items) || !Array.isArray(value.native_math_excluded_paragraphs)
    || !pagesValid(value.unread_pages) || !flagValid(value.images_not_assessed)) return unknown("unavailable");
  const { native_math_inventory_state: state, n_native_equations_unassessed: total,
    n_native_equations_unlocated: unlocated, native_math_coverage_items: locations, native_math_excluded_paragraphs: excluded } = value;
  if (state === "not_recorded" || state === "unavailable") {
    if (total !== null || unlocated !== null || locations.length || excluded.length || value.native_math_inventory_scope !== null) return unknown("unavailable");
    return state === "not_recorded" && row.kind !== "semantic" ? null : value;
  }
  if (state === "not_applicable") return value.native_math_coverage_version === "v1" && total === 0 && unlocated === null && !locations.length && !excluded.length
    && value.native_math_inventory_scope === null ? (value.unread_pages?.length || value.images_not_assessed ? value : null) : unknown("unavailable");
  if (state !== "complete" || value.native_math_coverage_version !== "v1" || value.native_math_inventory_scope !== "main_document_body"
    || !count(total) || !count(unlocated) || locations.some(item => !item || !count(item.n_occurrences) || item.n_occurrences === 0
      || (item.paragraph !== null && !count(item.paragraph)) || !["paragraph", "table_cell"].includes(item.kind))
    || excluded.some(item => !count(item)) || new Set(excluded).size !== excluded.length
    || locations.reduce((sum, item) => sum + item.n_occurrences, 0) !== total
    || locations.reduce((sum, item) => sum + (item.paragraph === null ? item.n_occurrences : 0), 0) !== unlocated
    || excluded.length !== new Set(locations.flatMap(item => item.paragraph === null ? [] : [item.paragraph])).size
    || locations.some(item => item.paragraph !== null && !excluded.includes(item.paragraph))) return unknown("unavailable");
  return value;
}

export function criterionCoverageSuffix(value: CriterionSourceCoverage | null): string {
  if (!value) return "";
  if (value.native_math_inventory_state === "not_recorded") return ": source coverage not recorded";
  if (value.native_math_inventory_state === "unavailable") return ": source coverage unavailable";
  if (value.unread_pages?.length) return ": some pages not read";
  if (value.images_not_assessed) return ": image content not assessed";
  return (value.n_native_equations_unassessed ?? 0) > 0 ? ": source content omitted" : "";
}

/** [45, 46, 47, 53] -> "45-47, 53" */
export function pageRanges(pages: number[]): string {
  const runs: number[][] = [];
  for (const page of [...pages].sort((a, b) => a - b)) {
    const last = runs[runs.length - 1];
    if (last && page === last[last.length - 1] + 1) last.push(page); else runs.push([page]);
  }
  return runs.map(run => run.length > 1 ? `${run[0]}-${run[run.length - 1]}` : `${run[0]}`).join(", ");
}

export function criterionCoverageLocations(value: CriterionSourceCoverage) {
  const grouped = new Map<string, { paragraph: number | null; kind: "paragraph" | "table_cell"; count: number }>();
  for (const item of value.native_math_coverage_items) {
    const key = `${item.kind}:${item.paragraph}`;
    const current = grouped.get(key);
    grouped.set(key, { paragraph: item.paragraph, kind: item.kind, count: (current?.count ?? 0) + item.n_occurrences });
  }
  return [...grouped.values()];
}
