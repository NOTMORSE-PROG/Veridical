// BUG-533: when the reference list's limits are unclear, the originality
// check leaves text out of the comparison. Running it again gives the same
// answer, so these say what was not compared and offer no rerun.
import type { IntegrityCheckStatusOut, ReportOut } from "../api/types";
import { instructorAnchor } from "../domain/sourceAnchor";

// "pages 157 to 187", or "pages 3, 5 and 9 to 12" when there are gaps.
export function pageSpan(pages: readonly number[]): string {
  const sorted = [...new Set(pages.filter(Number.isSafeInteger))].sort((a, b) => a - b);
  const runs: string[] = [];
  for (let i = 0; i < sorted.length; i += 1) {
    let end = i;
    while (end + 1 < sorted.length && sorted[end + 1] === sorted[end] + 1) end += 1;
    // Two pages in a row are named, not written as a range.
    if (end === i + 1) runs.push(`${sorted[i]}`, `${sorted[end]}`);
    else runs.push(end > i ? `${sorted[i]} to ${sorted[end]}` : `${sorted[i]}`);
    i = end;
  }
  const words = runs.length > 1 ? `${runs.slice(0, -1).join(", ")} and ${runs[runs.length - 1]}` : runs[0] ?? "";
  return `${sorted.length === 1 ? "page" : "pages"} ${words}`;
}

export const ORIGINALITY_NOT_COMPARED = {
  endUnclearPages: (span: string, single: boolean, gaps: boolean) => `The end of the reference list could not be found, so ${span}${gaps ? "" : " (from where the list starts to the end)"} ${single ? "was" : "were"} not compared. Check any appendices there yourself. Running the check again gives the same result.`,
  endUnclearFrom: (from: string) => `The end of the reference list could not be found, so everything from ${from} to the end of the document was not compared. Check any appendices there yourself. Running the check again gives the same result.`,
  endUnclear: "The end of the reference list could not be found, so the text from where the list starts to the end was not compared. Check any appendices there yourself. Running the check again gives the same result.",
  boundary: (span: string | null) => `The body could not be told apart from the reference list, so no text was compared${span ? `: ${span}, every page with text` : ""}. This is not evidence of originality. Running the check again gives the same result.`,
  // Older runs left the whole body out when the reference list could not be
  // confirmed. Same wording as the PDF export. Nothing here promises what a new
  // run would do: the saved row cannot show it.
  unconfirmedList: "No text was compared. When this check ran, the whole text was left out because the reference list's entries or end could not be confirmed. This is not evidence of originality.",
  unconfirmedScope: "Originality and reuse: no text was compared. When this check ran, the whole text was left out because the reference list's entries or end could not be confirmed. This is not evidence of originality.",
  boundaryScope: "Originality and reuse: the body could not be told apart from the reference list, so no text was compared. This is not evidence of originality.",
  membershipPartial: "Text after the reference list, such as an appendix that lists sources, could not be confirmed as body text, so it was left out of the comparison. Running the check again gives the same result.",
  someTag: "Some text not compared",
  noneTag: "Text not compared",
  someEmpty: "No findings were recorded in the text that was compared. Some text was not compared, so this is not evidence of originality.",
  noneEmpty: "No text was compared, so there is nothing to review. This is not evidence of originality.",
  limitsLink: "Open assessment limits",
  // A second limit beside "no text was compared" only adds what else was excluded.
  alsoMembership: "Text after the reference list also could not be confirmed as body text.",
  alsoNativeMath: "Native Word equations were also excluded.",
} as const;

const GAPLESS = (pages: readonly number[]) => {
  const sorted = [...new Set(pages)].sort((a, b) => a - b);
  return sorted.every((page, index) => index === 0 || page === sorted[index - 1] + 1);
};

export interface OriginalityCoverageNote {
  // Nothing was compared at all, as opposed to part of the text.
  none: boolean;
  tag: string;
  sentence: string;
  // A second limit recorded beside the first.
  also?: string;
}

type CoverageReport = Pick<ReportOut, "integrity_check_status" | "originality_scope">;

// What the originality check left out, said the same way on every surface.
// "No text was compared" wins over any other limit recorded beside it.
export function originalityCoverageNote(report: CoverageReport | undefined): OriginalityCoverageNote | null {
  const status = report?.integrity_check_status?.find((item) => item.check_kind === "originality_reuse");
  const reason = status?.coverage_reason;
  const pages = status?.pages_not_compared ?? [];
  const boundary = report?.originality_scope?.not_compared_reason === "reference_boundary_unclear" || reason === "reference_boundary_unclear";
  if (boundary) {
    return { none: true, tag: ORIGINALITY_NOT_COMPARED.noneTag, sentence: ORIGINALITY_NOT_COMPARED.boundary(pages.length ? pageSpan(pages) : null),
      also: reason === "reference_membership_partial" ? ORIGINALITY_NOT_COMPARED.alsoMembership
        : reason?.startsWith("native_math_") ? ORIGINALITY_NOT_COMPARED.alsoNativeMath : undefined };
  }
  if (report?.originality_scope?.not_compared_reason === "reference_list_unconfirmed") {
    return { none: true, tag: ORIGINALITY_NOT_COMPARED.noneTag, sentence: ORIGINALITY_NOT_COMPARED.unconfirmedList };
  }
  if (!status) return null;
  const from = status.not_compared_from?.trim();
  if (reason === "reference_end_unclear") {
    const sentence = pages.length ? ORIGINALITY_NOT_COMPARED.endUnclearPages(pageSpan(pages), new Set(pages).size === 1, !GAPLESS(pages))
      : from ? ORIGINALITY_NOT_COMPARED.endUnclearFrom(instructorAnchor(from)) : ORIGINALITY_NOT_COMPARED.endUnclear;
    return { none: false, tag: ORIGINALITY_NOT_COMPARED.someTag, sentence };
  }
  if (reason === "reference_membership_partial") return { none: false, tag: ORIGINALITY_NOT_COMPARED.someTag, sentence: ORIGINALITY_NOT_COMPARED.membershipPartial };
  return null;
}

// Kept for the assessment-limits row, which reads one status row.
export function originalityNotComparedDetail(status: IntegrityCheckStatusOut, scope?: ReportOut["originality_scope"]): string | null {
  if (status.check_kind !== "originality_reuse") return null;
  return originalityCoverageNote({ integrity_check_status: [status], originality_scope: scope ?? null })?.sentence ?? null;
}
