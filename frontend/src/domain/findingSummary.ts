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

export function findingTitle(flag: FlagSummaryOut): string {
  return problemLabel(flag.problem_kind) ?? flag.criterion_text ?? "Possible inconsistency";
}
