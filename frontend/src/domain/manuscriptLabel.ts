// group_label defaults to this literal for any manuscript not explicitly
// grouped (backend/app/ingest/router.py) -- the common case pre-V7
// student portal (BUG-022). Centralized here for the same reason
// readinessTone.ts centralizes verdict->tone: one meaning, one place
// that decides it, not a comparison repeated (and inevitably drifted)
// across every screen that lists manuscripts.
export const UNGROUPED_LABEL = "Ungrouped";

export interface ManuscriptIdentity {
  /** Always non-empty. The more informative available string. */
  primary: string;
  /** Present only when it adds information beyond `primary`. */
  secondary: string | null;
}

// Decides which of group_label / original_filename is the primary
// identity string for a manuscript, so an instructor triaging several
// otherwise-identical rows sees the field that actually distinguishes
// them first, at full visual weight -- not buried as a secondary line
// or a hover-only tooltip.
export function manuscriptIdentity(
  groupLabel: string,
  originalFilename: string | null,
): ManuscriptIdentity {
  if (!originalFilename) {
    // Pre-migration row: original_filename is null. Fall back exactly to
    // the display that shipped before this field existed.
    return { primary: groupLabel, secondary: null };
  }
  if (groupLabel === UNGROUPED_LABEL) {
    // The default label carries no information the instructor doesn't
    // already know (every unlabeled upload says this); showing it back
    // as a muted secondary line would be redundant, not helpful.
    return { primary: originalFilename, secondary: null };
  }
  return { primary: groupLabel, secondary: originalFilename };
}

// Caps the filename portion only (never group_label, already bounded at
// 200 chars server-side and rarely long) before it's interpolated into a
// native <select>'s <option> text, which cannot be CSS-truncated.
const MAX_OPTION_FILENAME_CHARS = 40;

export function truncateForOption(text: string, max: number = MAX_OPTION_FILENAME_CHARS): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1).trimEnd()}…`;
}

export function formatManuscriptOption(identity: ManuscriptIdentity, dateStr: string): string {
  // Both pieces are capped, not just secondary -- when a filename wins
  // the primary slot (the common case, group_label === "Ungrouped"),
  // it's exactly the string this cap exists to protect the <select>
  // from, not just the parenthetical.
  const primary = truncateForOption(identity.primary);
  const secondary = identity.secondary ? truncateForOption(identity.secondary) : null;
  const base = secondary ? `${primary} (${secondary})` : primary;
  return `${base}, uploaded ${dateStr}`;
}
