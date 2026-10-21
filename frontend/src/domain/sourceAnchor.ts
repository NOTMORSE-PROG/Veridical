/** Stored DOCX body ordinals are not printed page numbers. */
export function instructorAnchor(anchor: string): string {
  const match = /^(?:\u00b6|\u00c2\u00b6|paragraph\s+)(\d+)$/i.exec(anchor.trim());
  if (!match) return anchor;
  const ordinal = Number(match[1]);
  return Number.isSafeInteger(ordinal) && ordinal < Number.MAX_SAFE_INTEGER
    ? `Extracted paragraph ${ordinal + 1}`
    : "Reconstructed text (location unavailable)";
}
