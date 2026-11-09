import type { ReconstructionScopeOut, StatisticalCheckScopeOut } from "../api/types";

export function descriptiveAssumptionLimit(scope?: StatisticalCheckScopeOut | null): string | undefined {
  const count = scope?.unassessed_descriptive_rows;
  if (!Number.isSafeInteger(count) || count === undefined || count <= 0) return;
  return `${count} descriptive ${count === 1 ? "row was" : "rows were"} not assessed by GRIM/GRIMMER because single-item score assumptions were not established. Review the score scale and number of items in the original tables. Independently supported statistical findings remain available.`;
}

export function reconstructionScopeCopy(value?: ReconstructionScopeOut | null): { detail: string; incomplete: boolean } {
  // Score reconstruction tries to find whole-number answers that give a
  // table's reported average and spread; said here in an instructor's terms.
  const unrecorded = { detail: "No record of whether this step ran was saved, so do not assume it did.", incomplete: true };
  if (!value || value.state === "not_recorded") return unrecorded;
  const counts = [value.considered_rows, value.attempted_rows, value.compatible_rows, value.unassessed_rows];
  if (counts.some(count => !Number.isSafeInteger(count) || count === null || count < 0)) return unrecorded;
  const [considered, attempted, compatible, unassessed] = counts as number[];
  if (compatible + unassessed !== considered || compatible > attempted || attempted > considered) return unrecorded;
  const rows = (n: number) => `${n} table ${n === 1 ? "row" : "rows"}`;
  if (value.state === "candidate_found" && considered > 0 && unassessed === 0) return {
    detail: `For ${rows(compatible)}, it found at least one set of whole-number answers that gives the reported average and spread. That shows the numbers are possible, not that they are the real data.`,
    incomplete: false,
  };
  if (value.state === "incomplete" && unassessed > 0) return {
    detail: `It could not check ${rows(unassessed)}, usually because the score scale was not stated. Not finding a match is not proof of an error.${compatible > 0 ? ` For ${rows(compatible)}, it found at least one possible set of answers.` : ""}`,
    incomplete: true,
  };
  if (value.state === "not_applicable" && considered === 0) return {
    detail: "It did not run, because no table gave what it needs: an average with its score scale. This does not mean the paper has no statistics.",
    incomplete: false,
  };
  return unrecorded;
}
