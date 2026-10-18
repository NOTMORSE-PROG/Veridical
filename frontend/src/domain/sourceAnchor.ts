/** Stored DOCX body ordinals are not printed page numbers. */
export function instructorAnchor(anchor: string): string {
  return /^(?:\u00b6|\u00c2\u00b6|paragraph\s+)\d+$/i.test(anchor.trim())
    ? "Reconstructed text"
    : anchor;
}
