import type { IntegrityCheckStatusOut } from "../api/types";
import type { CoverageStatementItem } from "../ui/CoverageStatement";

export function nativeMathCoverageItem(status: IntegrityCheckStatusOut, checkRunId: number): CoverageStatementItem | null {
  const state = status.native_math_inventory_state;
  const count = status.n_native_equations_unassessed;
  const knownPositive = state === "complete" && typeof count === "number" && count > 0;
  if (!knownPositive && state !== "not_recorded" && state !== "unavailable") return null;
  const partial = status.analysis_coverage === "partial";
  const subIssues: string[] = knownPositive
    ? [`${count} native Word equation ${count === 1 ? "occurrence remains" : "occurrences remain"} unassessed.`,
      "The inventory covers the main document body; it does not establish coverage of other package parts."]
    : [state === "not_recorded"
      ? "This saved extraction has no native Word equation inventory. Their presence and count are unknown. This record does not establish that native Word equations were checked."
      : "The saved native Word equation inventory could not be verified. Their presence and count are unknown."];
  if (status.native_math_excluded_paragraphs?.length) subIssues.push("Text and table content affected by native Word equations was also excluded from these checks.");
  return {
    key: status.check_kind,
    label: `Statistical forensics · ${partial ? "Partially assessed" : "Not assessed"}`,
    detail: knownPositive ? partial
      ? "The results cover supported statistical statements from readable text and tables. Native Word equations remain unassessed. This limit is not a statistical error."
      : "No supported statistical statements were available from the text and tables used by this check. Native Word equations remain unassessed. This does not mean the manuscript contains no statistics."
      : "Native Word source coverage could not be established for this check. Unavailable content is not treated as passed.",
    detailsLabel: "native Word equation details",
    subIssues,
    sources: (status.native_math_coverage_items ?? []).map((item, index) => ({
      label: `${item.paragraph === null ? "Source location unavailable" : `Extracted paragraph ${item.paragraph + 1}${item.kind === "table_cell" ? ", table" : ""}`}: Native Word equations unassessed (${item.n_occurrences} ${item.n_occurrences === 1 ? "occurrence" : "occurrences"}, source record ${index + 1})`,
      to: item.paragraph === null ? null : `/report/${checkRunId}/document?anchor=${encodeURIComponent(`paragraph ${item.paragraph}`)}&coverage=native_math`,
    })),
    action: { to: `/report/${checkRunId}/document?coverage=native_math`, label: "Open the paper" },
  };
}
