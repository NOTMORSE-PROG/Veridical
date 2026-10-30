// V-089: a finding named so it can be told apart from others with the same title.
import type { FlagSummaryOut } from "../api/types";
import { problemLabel } from "./problemLabel";

const EXCERPT_CHARS = 140;

// Several findings often share a title and page, so each one also shows what
// it is about: the cited work, or a short piece of its excerpt.
export function findingSubject(flag: FlagSummaryOut): string {
  const text = (flag.cited_text?.trim() || flag.evidence_excerpt.trim()).replace(/\s+/g, " ");
  return text.length > EXCERPT_CHARS ? `${text.slice(0, EXCERPT_CHARS).trimEnd()}…` : text;
}

const SEVERITY: Record<FlagSummaryOut["severity"], string> = { high: "High severity", med: "Medium severity", low: "Low severity" };

// The same label the finding's card shows.
export const RECENCY_NOTE_LABEL = "Recency note: not counted";
export const REVIEW_LEAD_LABEL = "Review lead: not scored";

// A summary carries the kind as problem_kind; the full finding record as
// ai_verdict_summary.
type KindCarrier = { problem_kind?: string | null; ai_verdict_summary?: string | null };

export function isRecencyNote(flag: KindCarrier): boolean {
  return (flag.problem_kind ?? flag.ai_verdict_summary) === "outside_recency_window";
}

// What a finding that is not counted for readiness is called.
export function notCountedLabel(flag: KindCarrier): string {
  return isRecencyNote(flag) ? RECENCY_NOTE_LABEL : REVIEW_LEAD_LABEL;
}

export function severityLabel(flag: Pick<FlagSummaryOut, "severity" | "affects_readiness"> & Partial<Pick<FlagSummaryOut, "problem_kind">>): string {
  if (flag.affects_readiness === false) return notCountedLabel({ problem_kind: flag.problem_kind ?? null });
  return SEVERITY[flag.severity] ?? "Severity not recorded";
}

export function findingTitle(flag: FlagSummaryOut): string {
  return problemLabel(flag.problem_kind) ?? flag.criterion_text ?? "Possible inconsistency";
}
