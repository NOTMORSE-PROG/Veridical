import type { FlagSummaryOut } from "../api/types";
import { severityLabel } from "./severity";

interface LeadCandidate { id: number; check_kind: string; affects_readiness?: boolean | null }
export interface ComparisonLeadSnapshot {
  runId: number;
  records: FlagSummaryOut[];
  ordinalById: ReadonlyMap<number, number>;
  total: number;
}
export const COMPARISON_LEAD_HEADING_ID = "comparison-leads-heading";
export type ComparisonLeadFilter = "all" | "open" | "resolved";
export function comparisonLeadFilter(value: string | null): ComparisonLeadFilter {
  return value === "open" || value === "resolved" ? value : "all";
}

export const COMPARISON_LEAD_COPY = {
  title: "Optional comparison leads",
  intro: "Each record is a saved comparison lead. Records can overlap or cover different scopes. These leads do not affect readiness or block the instructor decision.",
  badge: "Comparison lead: not scored",
  markerLegend: "L marks a comparison lead. Comparison leads are not scored.",
  viewed: "Viewed markers are saved in this browser. They do not resolve leads.",
  outsideFilter: "This comparison lead is outside the current filter.",
  lastLoaded: "Showing the last loaded comparison lead records.",
  empty: "No optional comparison leads are available in this view.",
  unavailable: "Comparison lead records are not available.",
  failed: "Comparison lead records could not be loaded.",
} as const;

export function isOptionalComparisonLead(flag: LeadCandidate): boolean {
  return flag.check_kind === "originality_reuse" && flag.affects_readiness === false;
}

export function comparisonLeadSnapshot(runId: number, completeFlags: readonly FlagSummaryOut[]): ComparisonLeadSnapshot {
  const records = [...new Map(completeFlags.filter(isOptionalComparisonLead).map((flag) => [flag.id, flag])).values()]
    .sort((left, right) => left.id - right.id);
  return { runId, records, total: records.length, ordinalById: new Map(records.map((flag, index) => [flag.id, index + 1])) };
}

export function comparisonLeadIdentity(flag: Pick<LeadCandidate, "id">, snapshot?: ComparisonLeadSnapshot) {
  const ordinal = snapshot?.ordinalById.get(flag.id);
  return ordinal === undefined
    ? { label: `Record #${flag.id}`, marker: `Record #${flag.id}` }
    : { label: `Comparison lead ${ordinal} of ${snapshot!.total}. Record #${flag.id}.`, marker: `L${ordinal}` };
}

export function sourceRecordPresentation(flag: Pick<FlagSummaryOut, "id" | "check_kind" | "affects_readiness" | "severity">, legacyNumber: number, snapshot?: ComparisonLeadSnapshot) {
  if (isOptionalComparisonLead(flag)) {
    const identity = comparisonLeadIdentity(flag, snapshot);
    return { ...identity, label: identity.label.replace(/\.$/, ""), qualification: COMPARISON_LEAD_COPY.badge };
  }
  return { label: `Finding ${legacyNumber}`, marker: String(legacyNumber), qualification: severityLabel(flag.severity) };
}
