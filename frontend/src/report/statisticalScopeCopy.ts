import type { ReconstructionScopeOut, StatisticalCheckScopeOut } from "../api/types";

export function descriptiveAssumptionLimit(scope?: StatisticalCheckScopeOut | null): string | undefined {
  const count = scope?.unassessed_descriptive_rows;
  if (!Number.isSafeInteger(count) || count === undefined || count <= 0) return;
  return `${count} descriptive ${count === 1 ? "row was" : "rows were"} not assessed by GRIM/GRIMMER because single-item score assumptions were not established. Review the score scale and number of items in the original tables. Independently supported statistical findings remain available.`;
}

export function reconstructionScopeCopy(value?: ReconstructionScopeOut | null): { detail: string; incomplete: boolean } {
  const unrecorded = { detail: "No SPRITE reconstruction status was recorded. Do not infer that this check ran.", incomplete: true };
  if (!value || value.state === "not_recorded") return unrecorded;
  const counts = [value.considered_rows, value.attempted_rows, value.compatible_rows, value.unassessed_rows];
  if (counts.some(count => !Number.isSafeInteger(count) || count === null || count < 0)) return unrecorded;
  const [considered, attempted, compatible, unassessed] = counts as number[];
  if (compatible + unassessed !== considered || compatible > attempted || attempted > considered) return unrecorded;
  const recordedCounts = ` Rows considered: ${considered}; search attempted: ${attempted}; compatible candidates: ${compatible}; not assessed: ${unassessed}.`;
  if (value.state === "candidate_found" && considered > 0 && unassessed === 0) return {
    detail: `SPRITE found compatible candidate distributions for ${compatible} declared rows. These are possible reconstructions under the reported assumptions, not recovered or verified original data.${recordedCounts}`,
    incomplete: false,
  };
  if (value.state === "incomplete" && unassessed > 0) return {
    detail: `SPRITE reconstruction remains unassessed for ${unassessed} rows. Required scale assumptions may be missing or a bounded search did not produce a verified candidate. Search failure is not proof of inconsistency.${recordedCounts}`,
    incomplete: true,
  };
  if (value.state === "not_applicable" && considered === 0) return {
    detail: "No supported candidate rows were recorded for SPRITE reconstruction. This does not establish that the manuscript contains no statistics." + recordedCounts,
    incomplete: false,
  };
  return unrecorded;
}
