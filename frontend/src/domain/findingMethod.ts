import type { FlagOut } from "../api/types";
import { isPValueFinding } from "./checkKind";

type Finding = Pick<FlagOut, "check_kind" | "ai_verdict_summary">;

export function isCitationMetadataFinding(flag: Finding): boolean {
  return flag.check_kind === "citation_integrity" && flag.ai_verdict_summary === "possible_metadata_mismatch";
}

export function findingMethodLabel(flag: Finding): string {
  if (isCitationMetadataFinding(flag)) return "Registry check result";
  if (flag.check_kind === "statistical_forensics" && isPValueFinding(flag.ai_verdict_summary)) return "Calculated check result";
  if (flag.check_kind === "semantic" || flag.check_kind === "internal_agreement" ||
      (flag.check_kind === "citation_integrity" && flag.ai_verdict_summary?.startsWith("claim_"))) return "AI suggestion";
  return "Recorded check result";
}
