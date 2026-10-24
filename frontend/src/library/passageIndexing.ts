// F7, screens 4z/4x/4aa: prepared passages are distinct from manuscript analysis.
import type { PassageIndexing } from "../api/types";

export function passageIndexing(value: PassageIndexing | null | undefined): PassageIndexing {
  const unknown: PassageIndexing = { state: "not_recorded", passages_found: null, passages_indexed: null };
  if (!value) return unknown;
  if (value.state === "unavailable" && value.passages_found === null && value.passages_indexed === null) return value;
  const found = value.passages_found;
  const indexed = value.passages_indexed;
  if (!Number.isSafeInteger(found) || !Number.isSafeInteger(indexed) || found === null || indexed === null
    || found < 0 || indexed < 0 || indexed > found) return unknown;
  if (value.state === "complete" && indexed === found) return value;
  if (value.state === "partial" && indexed < found) return value;
  return unknown;
}

export function passageIndexingMarker(value: PassageIndexing | null | undefined): string | null {
  const scope = passageIndexing(value);
  if (scope.state === "partial") return "Limited passage matching";
  if (scope.state === "unavailable") return "Passage matching unavailable";
  if (scope.state === "not_recorded") return "Passage coverage not recorded";
  return scope.passages_found === 0 ? "No passages prepared" : null;
}

export function passageIndexingText(value: PassageIndexing | null | undefined): string {
  const scope = passageIndexing(value);
  if (scope.state === "partial") return `VERIDICAL kept ${scope.passages_indexed} of ${scope.passages_found} extracted passages for automatic passage matching. Other extracted passages were not added to that search.`;
  if (scope.state === "unavailable") return "Individual passages could not be prepared reliably for automatic matching. This does not establish whether the manuscript contains reused text.";
  if (scope.state === "not_recorded") return "The saved record does not establish how many extracted passages were prepared for automatic matching. Do not assume that every passage is searchable.";
  if (scope.passages_found === 0) return "VERIDICAL did not produce individual passages from the available text. This record is not available for automatic passage matching.";
  return `VERIDICAL kept ${scope.passages_indexed} extracted passages for automatic passage matching. This count describes the prepared text, not every part of the original manuscript.`;
}

export function passageAdditionAnnouncement(value: PassageIndexing | null | undefined): string {
  const scope = passageIndexing(value);
  if (scope.state === "partial") return "added with limited passage matching";
  if (scope.state === "unavailable") return "added. Passage matching is unavailable";
  if (scope.state === "not_recorded") return "added. Passage coverage was not recorded";
  if (scope.passages_found === 0) return "added. No individual passages were prepared";
  return "added";
}
