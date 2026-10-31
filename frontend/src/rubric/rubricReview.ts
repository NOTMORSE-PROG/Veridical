// BUG-566: what the review screen says about each level description, and
// which parser issues still need the instructor before a format can grade.
import type { Rubric, RubricAcknowledgementReason, RubricIssueDetail, RubricLevel, RubricLevelSource } from "../api/types";
import {
  RUBRIC_ISSUE_COPY, RUBRIC_KEEP_NOTE_MIN_CHARS, RUBRIC_KEEP_NOTE_REQUIRED_CODES, RUBRIC_KEEP_REASONS,
  RUBRIC_KEEP_REASONS_BY_CODE, RUBRIC_REVIEW_STATE_LABELS, RUBRIC_WHOLE_FORMAT_ISSUE_KEY, type RubricKeepReasonOption,
} from "../config/ui";

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

/** Every issue on the page in reading order: the items about the whole
 * format first, as the page shows them above the criteria; then criterion by
 * criterion, the row-level issue before that criterion's levels; then issues
 * on criteria no longer on the page. */
export function orderedIssues(details: readonly RubricIssueDetail[], criterionIds: readonly (number | null)[]): RubricIssueDetail[] {
  const position = new Map<number, number>();
  criterionIds.forEach((id, index) => { if (id !== null) position.set(id, index); });
  const rank = (issue: RubricIssueDetail) => issue.criterion_id === null ? -1
    : position.has(issue.criterion_id) ? position.get(issue.criterion_id)! : criterionIds.length;
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

/** The key an issue's keep reasons and note rule are looked up under. */
export function reasonKey(issue: Pick<RubricIssueDetail, "criterion_id" | "code">): string {
  return issue.criterion_id === null ? RUBRIC_WHOLE_FORMAT_ISSUE_KEY : issue.code;
}

export function issueCopy(issue: RubricIssueDetail): string {
  const known = RUBRIC_ISSUE_COPY[issue.code];
  if (known) return known;
  // The server's own message is a lower-case clause; show it as a sentence.
  const text = issue.message.trim();
  if (!text) return text;
  const sentence = text.charAt(0).toUpperCase() + text.slice(1);
  return /[.!?]$/.test(sentence) ? sentence : `${sentence}.`;
}

/** An issue on one level, worded the same wherever it is listed. Wording
 * found under another level's column names that level. */
export function levelIssueCopy(issue: RubricIssueDetail, level: RubricLevel | undefined, levels: readonly RubricLevel[]): string {
  const under = issue.code === "wrong_column" && level ? columnLevelName(level, levels) : null;
  return under && level ? `This wording is in the document, but under ${under}, not ${level.name}.` : issueCopy(issue);
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

/** The label a reason had when it was offered for this issue code, so the
 * record reads the way the choice did. */
export function keepReasonLabel(code: string | null | undefined, issueCode?: string): string | null {
  const offered = issueCode ? RUBRIC_KEEP_REASONS_BY_CODE[issueCode] : undefined;
  return (offered ?? RUBRIC_KEEP_REASONS).find((reason) => reason.value === code)?.label
    ?? RUBRIC_KEEP_REASONS.find((reason) => reason.value === code)?.label ?? null;
}

/** Reasons that fit every issue being kept together. When the issues share
 * none, only the explained "other" reason is left. */
export function keepReasonsFor(codes: readonly string[]): readonly RubricKeepReasonOption[] {
  const lists = codes.map((code) => RUBRIC_KEEP_REASONS_BY_CODE[code] ?? RUBRIC_KEEP_REASONS);
  if (!lists.length) return RUBRIC_KEEP_REASONS;
  const shared = lists[0].filter((option) => lists.every((list) => list.some((other) => other.value === option.value)));
  return shared.length ? shared : lists[0].filter((option) => option.value === "other");
}

export function keepNoteRequired(codes: readonly string[]): boolean {
  return codes.some((code) => RUBRIC_KEEP_NOTE_REQUIRED_CODES.includes(code));
}

/** A note counts once it has enough letters or digits to say something. */
export function noteHasContent(note: string): boolean {
  return (note.match(/[\p{L}\p{N}]/gu)?.length ?? 0) >= RUBRIC_KEEP_NOTE_MIN_CHARS;
}

export function itemsToCheck(count: number): string {
  return `${count} ${count === 1 ? "item" : "items"} to check`;
}

/** For wording found under another level's column of the same row: the
 * level of this criterion that column belongs to, matched by name against
 * the column heading ("PROFICIEN T 3" -> Proficient). */
export function columnLevelName(level: RubricLevel, levels: readonly RubricLevel[]): string | null {
  const heading = level.source?.column_heading;
  if (!heading) return null;
  const letters = (text: string) => text.toLowerCase().replace(/[^\p{L}]/gu, "");
  const found = letters(heading);
  const match = levels.find((other) => other.level !== level.level && letters(other.name) && found.includes(letters(other.name)));
  return match?.name ?? (tidy(heading) || null);
}

export type FormatReviewState =
  | { kind: "legacy" }
  | { kind: "to_check"; open: number }
  | { kind: "active_with_items"; open: number }
  | { kind: "flagged_all_checked" }
  | { kind: "all_match" };

/** The live review state. Only the open items decide it: parse_status
 * says how preparation went and stays "needs_review" after every item is
 * kept. Formats prepared before items were recorded have no state here.
 * A clean upload also has no items (the server stores null), but its levels
 * carry where they were found, which is how the two are told apart. */
export function formatReviewState(
  rubric: Pick<Rubric, "is_active" | "parse_status" | "parse_issue_details"> & { criteria?: Rubric["criteria"] },
): FormatReviewState {
  const details = rubric.parse_issue_details;
  if (details === null || details === undefined) {
    const traced = rubric.criteria?.some((criterion) => criterion.levels?.some((level) => level.source)) ?? false;
    return traced ? { kind: "all_match" } : { kind: "legacy" };
  }
  const open = details.filter((issue) => issue.status === "open").length;
  if (open) return rubric.is_active ? { kind: "active_with_items", open } : { kind: "to_check", open };
  return rubric.parse_status === "needs_review" ? { kind: "flagged_all_checked" } : { kind: "all_match" };
}

export function formatReviewLabel(state: FormatReviewState): string | null {
  switch (state.kind) {
    case "legacy": return null;
    case "to_check": return itemsToCheck(state.open);
    case "active_with_items": return RUBRIC_REVIEW_STATE_LABELS.activeWithItems;
    case "flagged_all_checked": return RUBRIC_REVIEW_STATE_LABELS.flaggedAllChecked;
    case "all_match": return RUBRIC_REVIEW_STATE_LABELS.allMatch;
  }
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
