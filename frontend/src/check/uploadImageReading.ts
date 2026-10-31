import type { IngestSummary } from "../api/types";

const images = (n: number) => `${n} ${n === 1 ? "image" : "images"}`;

// What the upload's image reading did, in counts, so a pass that read
// nothing is never reported as done: "81 images found; 12 checked for tables
// or equations, none had one; 69 not checked."
export function imageReadingLine(summary: Pick<IngestSummary, "images" | "image_reading">): string | null {
  const reading = summary.image_reading;
  if (!reading || summary.images === 0) return null;
  const found = reading.with_content === 0 ? "none had one" : `${reading.with_content} had one`;
  const checked = reading.checked === 0
    ? "none checked for tables or equations"
    : `${reading.checked} checked for tables or equations, ${found}`;
  const why = reading.no_cue === 0
    ? ""
    : reading.no_cue === reading.not_checked
      ? ` (no table or equation caption beside ${reading.not_checked === 1 ? "it" : "them"})`
      : ` (${reading.no_cue} with no table or equation caption beside ${reading.no_cue === 1 ? "it" : "them"})`;
  const unchecked = reading.not_checked === 0 ? "" : `; ${reading.not_checked} not checked${why}`;
  return `${images(summary.images)} found; ${checked}${unchecked}.`;
}

// A table pasted as a picture with no caption is among the images not checked.
export const IMAGE_READING_LIMIT = "Image content that was not checked is not treated as passed.";
