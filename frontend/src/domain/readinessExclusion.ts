// BUG-516: why a finding does not count toward readiness, said where the
// instructor sees it. Keyed by the server's readiness_exclusion_reason.
import type { FlagSummaryOut } from "../api/types";
import { isOptionalComparisonLead } from "./comparisonLeadIdentity";

export const READINESS_EXCLUSION_COPY: Record<string, string> = {
  comparison_lead_not_scored: "matching text is a lead to compare, not proof of copying, so you judge it from the passages.",
  statement_roles_unverified: "VERIDICAL cannot yet reliably tell which statements report this study's own results.",
  unassessed_evidence: "the evidence for this finding could not be checked.",
};

const NOT_COUNTED = "Not counted for readiness";

type ExclusionFlag = Pick<FlagSummaryOut, "affects_readiness" | "readiness_exclusion_reason">;

function exclusionReason(flag: ExclusionFlag): string | null {
  const reason = flag.readiness_exclusion_reason;
  return reason && Object.hasOwn(READINESS_EXCLUSION_COPY, reason) ? READINESS_EXCLUSION_COPY[reason] : null;
}

export function readinessExclusionLine(flag: ExclusionFlag): string | null {
  if (flag.affects_readiness !== false) return null;
  const reason = exclusionReason(flag);
  return reason ? `${NOT_COUNTED}: ${reason}` : `${NOT_COUNTED}.`;
}

// The same rule the readiness score uses: every finding that is not dismissed
// and not excluded deducts by its severity; open high ones also cap the band.
export function countsForReadiness(flag: Pick<FlagSummaryOut, "overridden" | "affects_readiness">): boolean {
  return !flag.overridden && flag.affects_readiness !== false;
}

// One line beside the readiness count for high-severity findings it leaves out.
export function uncountedHighLine(flags: (ExclusionFlag & Pick<FlagSummaryOut, "id" | "check_kind">)[]): string {
  const leads = flags.every(isOptionalComparisonLead);
  const noun = leads ? flags.length === 1 ? "comparison lead is" : "comparison leads are" : flags.length === 1 ? "finding is" : "findings are";
  const lead = `${flags.length} high-severity ${noun} not in this count`;
  const reasons = new Set(flags.map(exclusionReason));
  const [only] = reasons;
  return reasons.size === 1 && only ? `${lead}: ${only}` : `${lead}. Each one says why under Integrity findings.`;
}
