// BUG-566: what the review screen says about each level description, and
// which parser issues still need the instructor before a format can grade.
import type { RubricAcknowledgementReason, RubricIssueDetail, RubricLevel, RubricLevelSource } from "../api/types";
import { RUBRIC_ISSUE_COPY, RUBRIC_KEEP_REASONS } from "../config/ui";

export interface PendingAcknowledgement {
  issue_id: number;
  reason_code: RubricAcknowledgementReason;
  note: string | null;
}

export type LevelStatus =
  | { kind: "check"; issues: RubricIssueDetail[] }
  | { kind: "kept_pending"; issues: RubricIssueDetail[] }
  | { kind: "kept"; issues: RubricIssueDetail[] }
  | { kind: "matches" }
  // Unmatched only because another level in its row failed; it has no issue
  // of its own, so it is neither a match nor something to decide.
  | { kind: "row_unchecked" }
  | { kind: "not_checked" };

/** Every issue on the page in reading order: criterion by criterion, the
 * row-level issue before that criterion's levels, then the issues that
 * belong to no criterion still on the page. */
export function orderedIssues(details: readonly RubricIssueDetail[], criterionIds: readonly (number | null)[]): RubricIssueDetail[] {
  const position = new Map<number, number>();
  criterionIds.forEach((id, index) => { if (id !== null) position.set(id, index); });
  const rank = (issue: RubricIssueDetail) =>
    issue.criterion_id !== null && position.has(issue.criterion_id) ? position.get(issue.criterion_id)! : criterionIds.length;
  return details
    .map((issue, index) => ({ issue, index }))
    .sort((a, b) => rank(a.issue) - rank(b.issue)
      || (a.issue.level ?? 0) - (b.issue.level ?? 0)
      || a.index - b.index)
    .map(({ issue }) => issue);
}

/** Issues the instructor still has to correct or keep. An acknowledgement
 * not saved yet counts as handled on this page; the server decides again
 * when the page is saved. */
export function openIssues(details: readonly RubricIssueDetail[], pending: ReadonlyMap<number, PendingAcknowledgement>): RubricIssueDetail[] {
  return details.filter((issue) => issue.status === "open" && !pending.has(issue.id));
}

/** What a set of issues on one level (or one whole row) still asks for;
 * null when every one of them was resolved. */
export function issueStatus(
  issues: readonly RubricIssueDetail[],
  pending: ReadonlyMap<number, PendingAcknowledgement>,
): Extract<LevelStatus, { issues: RubricIssueDetail[] }> | null {
  const live = issues.filter((issue) => issue.status !== "resolved");
  const open = live.filter((issue) => issue.status === "open" && !pending.has(issue.id));
  if (open.length) return { kind: "check", issues: open };
  const kept = live.filter((issue) => issue.status === "open" && pending.has(issue.id));
  if (kept.length) return { kind: "kept_pending", issues: kept };
  const acknowledged = live.filter((issue) => issue.status === "acknowledged");
  if (acknowledged.length) return { kind: "kept", issues: acknowledged };
  return null;
}

export function levelStatus(
  level: RubricLevel,
  issues: readonly RubricIssueDetail[],
  pending: ReadonlyMap<number, PendingAcknowledgement>,
): LevelStatus {
  const fromIssues = issueStatus(issues, pending);
  if (fromIssues) return fromIssues;
  if (!level.source) return { kind: "not_checked" };
  // An unmatched level the server raised no issue for is never shown as a
  // match, and never as wording VERIDICAL looked for and could not find.
  if (level.source.match === "unmatched") return { kind: "row_unchecked" };
  return { kind: "matches" };
}

export function issueCopy(issue: RubricIssueDetail): string {
  return RUBRIC_ISSUE_COPY[issue.code] ?? issue.message;
}

/** "Page 1 · row "Purpose" · column "PROFICIENT 3"", with plain row and
 * column numbers when the server could not read a label. */
export function sourceLocation(source: RubricLevelSource): string | null {
  const parts: string[] = [];
  if (source.page !== null) parts.push(`Page ${source.page}`);
  if (source.table !== null) {
    if (source.row_label) parts.push(`row "${tidy(source.row_label)}"`);
    else if (source.row !== null) parts.push(`row ${source.row}`);
    if (source.column_heading) parts.push(`column "${tidy(source.column_heading)}"`);
    else if (source.col !== null) parts.push(`column ${source.col}`);
  } else if (source.match === "exact") parts.push("outside the table");
  return parts.length ? parts.join(" · ") : null;
}

/** Where a criterion's row is, from any of its levels that were placed, so a
 * level that could not be placed can still point at the right part of the
 * original. */
export function rowLocation(levels: readonly RubricLevel[]): { page: number | null; text: string | null } {
  const placed = levels.map((level) => level.source).find((source) => source && (source.page !== null || source.row_label));
  if (!placed) return { page: null, text: null };
  const parts: string[] = [];
  if (placed.page !== null) parts.push(`Page ${placed.page}`);
  if (placed.row_label) parts.push(`row "${tidy(placed.row_label)}"`);
  return { page: placed.page, text: parts.join(" · ") || null };
}

export function keepReasonLabel(code: string | null | undefined): string | null {
  return RUBRIC_KEEP_REASONS.find((reason) => reason.value === code)?.label ?? null;
}

// Labels extracted from a PDF can carry line breaks and zero-width spaces.
export function tidy(text: string): string {
  return text.replace(/[​-‍﻿]/g, "").replace(/\s+/g, " ").trim();
}

export function shortDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function levelAnchorId(criterionKey: string, level: number | null): string {
  return level === null ? `criterion-row-issues-${criterionKey}` : `criterion-level-${criterionKey}-${level}`;
}
