import type { IntegrityCheckStatusOut } from "../api/types";
import type { CoverageStatementItem } from "../ui/CoverageStatement";

export const IMAGE_COVERAGE_LABELS: Record<string, string> = {
  reading_not_recorded: "Image reading was not recorded",
  unsupported_source: "Image assessment is not supported for this source",
  vision_disabled: "Image reading was not requested",
  not_attempted: "Image reading was not attempted",
  size_filter: "Outside image-reading size limits",
  no_table_cue: "Not checked: no table or equation caption beside it",
  selection_limit: "Image-reading limit reached",
  client_unavailable: "Image-reading service was not configured",
  api_down: "Image-reading service was unavailable",
  quota_exhausted: "AI capacity was unavailable",
  crop_unavailable: "The image region could not be read",
  invalid_response: "Image reading could not be validated",
  content_not_transcribed: "Identified by type only; content not assessed",
  low_confidence: "Image reading was too uncertain for statistical checks",
  equation_not_checked: "Equation transcribed but not evaluated",
  table_not_checked: "No supported statistics extracted from the image table",
  table_partly_parsed: "Some statistical cells in the image table could not be assessed",
};

// BUG-549: touching small PDF image pieces are combined into one image
// before checking. Same wording as the PDF export (report/export.py).
const formatCount = (value: number) => value.toLocaleString("en-US");

// "Drawn", not "stored": the count is of placements on the page, and one
// stored image can be drawn many times.
export function piecesSuffix(count: number, merged: number, pieces: number) {
  if (!merged) return "";
  if (merged === 1) return `${count === 1 ? "," : "; 1"} drawn in the PDF as ${formatCount(pieces)} small touching pieces`;
  return `${merged === count ? ", each" : `; ${merged}`} drawn in the PDF as small touching pieces, ${formatCount(pieces)} in all`;
}

export function mergedImagesLine(merged: number, pieces: number) {
  return merged === 1
    ? `1 listed image occurrence is drawn in the PDF as ${formatCount(pieces)} small touching pieces. It was combined into one image when the file was prepared; its content was not assessed.`
    : `${merged} listed image occurrences are drawn in the PDF as small touching pieces (${formatCount(pieces)} in all). Each was combined into one image when the file was prepared; their content was not assessed.`;
}

export function imageCoverageItem(status: IntegrityCheckStatusOut, checkRunId: number): CoverageStatementItem {
  const partial = status.analysis_coverage === "partial";
  const locations = new Map<string, { label: string; anchor: string | null; count: number; merged: number; pieces: number }>();
  let merged = 0;
  let mergedPieces = 0;
  for (const item of status.image_coverage_items ?? []) {
    const page = Number.isSafeInteger(item.page) && item.page! > 0 ? item.page : null;
    const paragraph = Number.isSafeInteger(item.paragraph) && item.paragraph! >= 0 ? item.paragraph : null;
    const anchor = page !== null ? `page ${page}` : paragraph !== null ? `paragraph ${paragraph}` : null;
    const location = page !== null ? `Page ${page}` : paragraph !== null ? `Extracted paragraph ${paragraph + 1}` : "Source location unavailable";
    const label = `${location}: ${IMAGE_COVERAGE_LABELS[item.reason] ?? "Image content unassessed"}`;
    // Only merged images count pieces; a plain image is not "a piece".
    const pieces = Number.isSafeInteger(item.piece_count) && item.piece_count! > 1 ? item.piece_count! : 0;
    if (pieces) { merged += 1; mergedPieces += pieces; }
    const previous = locations.get(label);
    locations.set(label, { label, anchor, count: (previous?.count ?? 0) + 1, merged: (previous?.merged ?? 0) + (pieces ? 1 : 0), pieces: (previous?.pieces ?? 0) + pieces });
  }
  const subIssues: string[] = [];
  if (status.n_images_unassessed) subIssues.push(`${status.n_images_unassessed} image ${status.n_images_unassessed === 1 ? "occurrence remains" : "occurrences remain"} unassessed. Occurrences are not necessarily distinct manuscript figures.`);
  // A page of touching tiles is read as one image; say so, so it never reads
  // as one small picture.
  if (merged) subIssues.push(mergedImagesLine(merged, mergedPieces));
  if (status.n_image_occurrences_omitted) subIssues.push(`${status.n_image_occurrences_omitted} additional drawing ${status.n_image_occurrences_omitted === 1 ? "occurrence" : "occurrences"} exceeded the inventory limit.`);
  if (status.n_equations_unassessed) subIssues.push(`${status.n_equations_unassessed} ${status.n_equations_unassessed === 1 ? "equation was" : "equations were"} transcribed from images but not evaluated by statistical forensics.`);
  if (status.n_recovery_figures_unassessed || status.n_recovery_tables_unassessed) subIssues.push(`${status.n_recovery_figures_unassessed ?? 0} figure and ${status.n_recovery_tables_unassessed ?? 0} table recovery proposals remain unassessed. These are not verified image locations.`);
  return {
    key: status.check_kind,
    label: `Statistical checks · ${partial ? "Partially assessed" : "Not assessed"}`,
    detail: partial
      ? "Statistical statements were extracted from readable text or tables. Some image content remains unassessed. This limit is not a statistical error."
      : "No supported statistical statements were found in readable text and tables. Unassessed images may contain information this check missed.",
    detailsLabel: "image assessment details",
    subIssues,
    // Same wording as the PDF export.
    sources: [...locations.values()].map(({ label, anchor, count, merged: mergedHere, pieces }) => ({
      label: `${label} (${count} image ${count === 1 ? "occurrence" : "occurrences"}${piecesSuffix(count, mergedHere, pieces)})`,
      to: anchor === null ? null : `/report/${checkRunId}/document?anchor=${encodeURIComponent(anchor)}&coverage=images`,
    })),
  };
}
