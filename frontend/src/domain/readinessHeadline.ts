// V-087: when the band is Needs Review, the headline says why in one line,
// from the report's own record (partial checks, unread pages, grades).
import type { ReportOut } from "../api/types";

type HeadlineReport = Pick<ReportOut, "status" | "results" | "integrity_modules" | "integrity_check_status" | "pending_review_count">;

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

export function needsReviewReason(report: HeadlineReport): string | null {
  if (report.status !== "needs_review") return null;
  const parts: string[] = [];
  const partial = (report.integrity_modules ?? []).filter((module) => module.state === "partial" || module.state === "incomplete").length;
  // Several checks usually skip the same pages, so the unread count is the union.
  const pages = new Set<number>();
  let largestCount = 0;
  for (const status of report.integrity_check_status ?? []) {
    for (const page of status.source_pages_unassessed ?? []) if (Number.isSafeInteger(page)) pages.add(page);
    largestCount = Math.max(largestCount, status.n_source_pages_unassessed ?? 0);
  }
  const unread = pages.size || largestCount;
  if (partial > 0) {
    parts.push(unread > 0 ? `${plural(partial, "check", "checks")} could not read ${plural(unread, "page", "pages")}` : `${plural(partial, "check", "checks")} ran only in part`);
  }
  const total = report.results.length;
  if (total > 0) {
    const graded = report.results.filter((row) => row.outcome === "passed" || row.outcome === "failed").length;
    parts.push(`${graded} of ${plural(total, "criterion", "criteria")} graded`);
  }
  if (report.pending_review_count > 0) parts.push(`${report.pending_review_count} waiting for you`);
  return parts.length > 0 ? parts.join(" · ") : null;
}
