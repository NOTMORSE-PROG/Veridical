import type { IntegrityCheckStatusOut, ReportOut } from "../api/types";
import type { CoverageStatementItem } from "../ui/CoverageStatement";

const INTEGRITY_LABEL: Record<IntegrityCheckStatusOut["check_kind"], string> = {
  internal_agreement: "Internal agreement",
  citation_integrity: "Citation integrity",
};

// BUG-127/BUG-128: one shared coverage projection now serves both the
// full report and the manuscript-first review workspace. Keeping the
// wording and recovery destinations here prevents the two surfaces from
// making different claims about the same incomplete check.
function integrityCoverageItem(status: IntegrityCheckStatusOut, manuscriptId: number): CoverageStatementItem {
  const unavailable = status.n_skipped_api_down;
  const capacity = status.n_skipped_quota;
  const parse = status.n_skipped_parse_failure;
  const outcomeText =
    status.outcome === "api_down"
      ? "a service interruption"
      : status.outcome === "quota_exhausted"
        ? "a free-capacity limit"
        : "an unverifiable result";
  const subIssues: string[] = [];
  if (unavailable > 0) subIssues.push(`${unavailable} item${unavailable === 1 ? "" : "s"} skipped because a service was unavailable.`);
  if (capacity > 0) subIssues.push(`${capacity} item${capacity === 1 ? "" : "s"} skipped because daily AI capacity was spent.`);
  if (parse > 0) subIssues.push(`${parse} item${parse === 1 ? "" : "s"} skipped because the source could not be parsed reliably.`);
  const label = INTEGRITY_LABEL[status.check_kind];
  return {
    key: status.check_kind,
    label,
    detail: `The check recorded ${outcomeText}. Nothing skipped is presented as passed.`,
    subIssues,
    action: {
      to: `/dashboard?rerun=${manuscriptId}`,
      label: "Run again",
      ariaLabel: `Run again: ${label.toLowerCase()} check for this manuscript`,
    },
  };
}

export function coverageItems(report: ReportOut): CoverageStatementItem[] {
  const items: CoverageStatementItem[] = [];
  if (report.rubric_needs_review) {
    items.push({
      key: "rubric_needs_review",
      label: "Required format review",
      detail: "The required format was activated with unresolved parser uncertainty. Check the criterion record against the original document.",
      subIssues: report.rubric_parse_issues ?? undefined,
      action: { to: "/rubric", label: "Review required format" },
    });
  }
  for (const status of report.integrity_check_status ?? []) {
    items.push(integrityCoverageItem(status, report.manuscript_id));
  }
  return items;
}
