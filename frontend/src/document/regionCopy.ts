// V-065 §5.3 (ui-designer spec, 2026-08-19): exact evidence-section copy
// for every degraded anchor-precision state, keyed by kind so the branch
// is data-driven (ground rule 7), not a per-check-kind if-chain. Every
// string here traces to a real measured finding (V-065.md Q2) or a real
// schema fact (no per-entry reference-list location, no PDF.js path for
// DOCX) -- none of these are invented placeholders.
import type { FlagRegionOut } from "../api/types";

export function regionCopy(region: FlagRegionOut): string | null {
  switch (region.kind) {
    case "page_bbox":
    case "paragraph_only":
      return null; // precise (or as precise as this format gets) -- no extra copy needed
    case "page_only":
      return `VERIDICAL could not find this exact wording on page ${region.page}, so it can't show a precise highlight there. The page has opened in the document pane. Compare the excerpt above to the page yourself.`;
    case "reference_list":
      return "This flag points to the reference list, not a specific page. Open the reference list in the document pane to check it yourself.";
    case "reference_position":
      return `This flag points to entry #${region.index} of the reference list, not a specific page. Open the reference list in the document pane to check it yourself.`;
    case "whole_document":
      return "This flag compares the whole manuscript against a previously processed document, not one specific page.";
    case "section":
      return region.page !== null
        ? `This flag compares a section of the manuscript against a previously processed document. That section has opened in the document pane (page ${region.page}).`
        : "This flag compares a section of the manuscript against a previously processed document. VERIDICAL could not determine which page that section starts on.";
    case "unavailable":
      return "This flag's exact position in the manuscript isn't available. The excerpt above is everything VERIDICAL recorded.";
  }
}

// The page-nav bar's own short substitute text when a flag's region has no
// page to jump to at all (§4.3).
export function noPageNavCopy(region: FlagRegionOut): string | null {
  if (region.page !== null) return null;
  if (region.kind === "reference_list" || region.kind === "reference_position") {
    return "This flag doesn't point to a specific page. See the panel on the right.";
  }
  if (region.kind === "whole_document" || region.kind === "section" || region.kind === "unavailable") {
    return "This flag doesn't point to a specific page. See the panel on the right.";
  }
  return null;
}
