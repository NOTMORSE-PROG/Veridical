/** Stored DOCX body ordinals are not printed page numbers. */
export function instructorAnchor(anchor: string): string {
  const match = /^(?:\u00b6|\u00c2\u00b6|paragraph\s+)(\d+)$/i.exec(anchor.trim());
  if (!match) return anchor;
  const ordinal = Number(match[1]);
  return Number.isSafeInteger(ordinal) && ordinal < Number.MAX_SAFE_INTEGER
    ? `Extracted paragraph ${ordinal + 1}`
    : "Reconstructed text (location unavailable)";
}

/** One key per manuscript location, whatever wording an anchor uses: grading
 * evidence says "page 45" or "paragraph 12", findings say "p. 45" or "¶12". */
export function anchorLocationKey(anchor: string | null | undefined): string | null {
  const text = anchor?.trim() ?? "";
  const page = /^(?:p\.?|page)\s*(\d+)$/i.exec(text);
  if (page) return `page:${Number(page[1])}`;
  const paragraph = /^(?:\u00b6|\u00c2\u00b6|paragraph\s+)(\d+)$/i.exec(text);
  return paragraph ? `paragraph:${Number(paragraph[1])}` : null;
}
