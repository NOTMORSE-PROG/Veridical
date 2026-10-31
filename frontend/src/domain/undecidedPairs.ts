import type { FlagSummaryOut } from "../api/types";

export const LEGACY_UNDECIDED_KIND = "agreement_cannot_determine";

/** Saved "could not determine" agreement rows from runs before the check
 * kept them as a list. They make no claim, so they are shown with the
 * undecided pairs and never counted as findings. */
export function isLegacyUndecided(flag: FlagSummaryOut): boolean {
  return flag.check_kind === "internal_agreement" && flag.problem_kind === LEGACY_UNDECIDED_KIND;
}
