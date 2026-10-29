// BUG-533: when the reference list's limits are unclear, the originality
// check leaves text out of the comparison. Running it again gives the same
// answer, so these say what was not compared and offer no rerun.
import type { IntegrityCheckStatusOut } from "../api/types";

// "pages 157 to 187", or "pages 3, 5 and 9 to 12" when there are gaps.
export function pageSpan(pages: readonly number[]): string {
  const sorted = [...new Set(pages.filter(Number.isSafeInteger))].sort((a, b) => a - b);
  const runs: string[] = [];
  for (let i = 0; i < sorted.length; i += 1) {
    let end = i;
    while (end + 1 < sorted.length && sorted[end + 1] === sorted[end] + 1) end += 1;
    runs.push(end > i ? `${sorted[i]} to ${sorted[end]}` : `${sorted[i]}`);
    i = end;
  }
  const words = runs.length > 1 ? `${runs.slice(0, -1).join(", ")} and ${runs[runs.length - 1]}` : runs[0] ?? "";
  return `${sorted.length === 1 ? "page" : "pages"} ${words}`;
}

export const ORIGINALITY_NOT_COMPARED = {
  endUnclearPages: (span: string) => `The end of the reference list could not be found, so ${span} (from where the list starts to the end) were not compared. Check any appendices there yourself. Running the check again gives the same result.`,
  endUnclearFrom: (from: string) => `The end of the reference list could not be found, so everything from ${from} to the end of the document was not compared. Check any appendices there yourself. Running the check again gives the same result.`,
  endUnclear: "The end of the reference list could not be found, so the text from where the list starts to the end was not compared. Check any appendices there yourself. Running the check again gives the same result.",
  boundary: (span: string | null) => `The body could not be told apart from the reference list, so no text was compared${span ? ` (${span})` : ""}. This is not evidence of originality. Running the check again gives the same result.`,
  boundaryScope: "Originality and reuse: the body could not be told apart from the reference list, so no text was compared. This is not evidence of originality.",
  membershipPartial: "Text after the reference list, such as an appendix that lists sources, could not be confirmed as body text, so it was left out of the comparison. Running the check again gives the same result.",
} as const;

export function originalityNotComparedDetail(status: IntegrityCheckStatusOut): string | null {
  if (status.check_kind !== "originality_reuse") return null;
  const pages = status.pages_not_compared ?? [];
  const from = status.not_compared_from?.trim();
  switch (status.coverage_reason) {
    case "reference_end_unclear":
      return pages.length ? ORIGINALITY_NOT_COMPARED.endUnclearPages(pageSpan(pages))
        : from ? ORIGINALITY_NOT_COMPARED.endUnclearFrom(from) : ORIGINALITY_NOT_COMPARED.endUnclear;
    case "reference_boundary_unclear":
      return ORIGINALITY_NOT_COMPARED.boundary(pages.length ? pageSpan(pages) : null);
    case "reference_membership_partial":
      return ORIGINALITY_NOT_COMPARED.membershipPartial;
    default:
      return null;
  }
}
