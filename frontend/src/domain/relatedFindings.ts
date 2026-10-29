// BUG-516: copy and matching for findings on the pages an automatic grade quoted.
import type { EvidenceItem, FlagSummaryOut } from "../api/types";
import { isOptionalComparisonLead } from "./comparisonLeadIdentity";
import { anchorLocationKey } from "./sourceAnchor";

export const RELATED_FINDINGS_COPY = {
  located: (count: number, pages: string) => `${count} integrity ${count === 1 ? "finding is" : "findings are"} on ${pages.includes(",") ? "pages" : "the page"} this grade quoted (${pages}).`,
  notUsed: "This grade does not use integrity findings.",
  allCounted: (count: number) => `The overall readiness does count ${count === 1 ? "it" : "them"}.`,
  someCounted: (counted: number) => `The overall readiness counts ${counted} of them; the others are marked not counted.`,
  noneCounted: (count: number) => `${count === 1 ? "It is" : "They are"} marked not counted for readiness.`,
  inspect: "Inspect these findings",
} as const;

export function relatedFindings(evidence: EvidenceItem[], flags: FlagSummaryOut[]): FlagSummaryOut[] {
  const quoted = new Set(evidence.map((item) => anchorLocationKey(item.anchor)).filter((key): key is string => key !== null));
  if (quoted.size === 0) return [];
  // Optional comparison leads are listed apart from findings, so they are left out here too.
  return flags.filter((flag) => !flag.overridden && !isOptionalComparisonLead(flag)
    && quoted.has(anchorLocationKey(flag.page_anchor) ?? ""));
}
