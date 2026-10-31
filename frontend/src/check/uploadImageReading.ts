import type { ImageReadingSummary, IngestSummary } from "../api/types";

const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const was = (n: number) => (n === 1 ? "was" : "were");

// Why images were not read, in the order an instructor is most likely to
// meet them. Word files are their own sentence: no caption makes a Word image
// readable, so the caption reason must not be offered there.
const NOT_READ: [keyof ImageReadingSummary, (n: number) => string][] = [
  ["no_cue", (n) => `${count(n, "had", "had")} no recognised table or equation caption beside ${n === 1 ? "it" : "them"}`],
  ["too_small", (n) => `${n} ${was(n)} too small to hold one`],
  ["over_limit", (n) => `${n} ${was(n)} over the reading limit for one manuscript`],
  ["unavailable", (n) => `${n} could not be read because the image reader was unavailable`],
  ["not_requested", (n) => `${n} ${was(n)} not requested for reading`],
  ["unsupported", (n) => `${n} had no page position to read from`],
];

// What the upload's image reading did, led by what it means for the
// manuscript: which images were read for a table or an equation, why the
// rest were not, and that their content is not counted as passed. A pass
// that read nothing is never reported as done.
export function imageReadingLine(summary: Pick<IngestSummary, "images" | "image_reading" | "anchor_kind">): string | null {
  const reading = summary.image_reading;
  if (!reading || summary.images === 0) return null;
  const parts = [`${count(summary.images, "image", "images")} found.`];
  if (summary.anchor_kind === "paragraph") {
    parts.push("Images in Word files are not read for tables or equations.");
  } else {
    if (reading.checked > 0) {
      parts.push(`${reading.checked} ${was(reading.checked)} read for a table or equation; ${reading.with_content === 0 ? "none held one" : `${reading.with_content} held one`}.`);
    }
    if (reading.unclear > 0) parts.push(`${reading.unclear} could not be read reliably.`);
    if (reading.not_checked > 0) {
      const reasons = NOT_READ.filter(([key]) => reading[key] > 0).map(([key, words]) => words(reading[key]));
      const lead = reading.not_checked === summary.images ? "None was read for a table or equation" : `${reading.not_checked} ${was(reading.not_checked)} not read`;
      parts.push(`${lead}: ${reasons.join("; ")}.`);
    }
  }
  if (reading.not_checked > 0 || reading.unclear > 0 || summary.anchor_kind === "paragraph") parts.push(IMAGE_READING_LIMIT);
  return parts.join(" ");
}

export const IMAGE_READING_LIMIT = "Typed tables and text were read as usual. Anything shown only inside an image that was not read is not counted as passed.";
