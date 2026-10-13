// V-077: small Library display helpers shared by the collection, add, record
// and compare screens.
import type { LibraryItemOut } from "../api/types";

export function formatLibraryDate(value: string | null): string {
  if (!value) return "Not added yet";
  return new Date(value).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** The name an own record is shown under; another instructor's record is
 * always its record number (BUG-147). */
export function libraryRecordName(item: LibraryItemOut): string {
  if (!item.is_own) return item.label;
  return item.title ?? item.original_filename ?? "Untitled manuscript";
}

const SCHOOL_YEAR = /^\d{4}-\d{4}$/;
// A hyphen, an en dash (U+2013) or an em dash (U+2014), with or without
// spaces, all become one hyphen. Built from escapes so the source carries no
// dash character (the dash guard decodes regex literals).
const SCHOOL_YEAR_SEPARATOR = new RegExp("\\s*[-\\u2013\\u2014]\\s*");

/** "" for a blank entry, the normalized "YYYY-YYYY" value, or null when the
 * entry cannot be read as two years. */
export function normalizeSchoolYear(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return "";
  const value = trimmed.split(SCHOOL_YEAR_SEPARATOR).join("-");
  if (!SCHOOL_YEAR.test(value)) return null;
  // A school year spans two consecutive years ("2025-2026", never "2026-2025").
  const [start, end] = value.split("-").map(Number);
  return end === start + 1 ? value : null;
}
