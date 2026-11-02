/** BUG-685: a saved page is the PDF's own page count from its first page,
 * which can differ from the number printed on the page, so it always says
 * "PDF". Short form for inline text and links ("PDF p. 49"), long form for
 * image descriptions and dialog titles ("PDF page 49"). */
export function pdfPage(page: number, form: "short" | "long" = "short"): string {
  return form === "long" ? `PDF page ${page}` : `PDF p. ${page}`;
}

/** A list of PDF pages as runs: "PDF pages 2, 4, 19-21", or one page. */
export function pdfPageList(ranges: string, single: boolean): string {
  return `${single ? "PDF page" : "PDF pages"} ${ranges}`;
}

/** Said once near a list of PDF pages, never on every card. */
export const PDF_PAGE_NUMBERING_HINT = "PDF page numbers count from the first page of the file. They can differ from the page numbers printed on the pages.";

/** Stored DOCX body ordinals are not printed page numbers; saved PDF pages
 * are the file's own count, so they are labelled as PDF pages. */
export function instructorAnchor(anchor: string, form: "short" | "long" = "short"): string {
  const page = /^(?:p\.?|page)\s*(\d+)$/i.exec(anchor.trim());
  if (page) return pdfPage(Number(page[1]), form);
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
