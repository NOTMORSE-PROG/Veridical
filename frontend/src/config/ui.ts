/** Frontend interaction timings. Components never carry policy-like literals. */
export const UI_TIMING = {
  authPendingRevealMs: 400,
  serviceUnavailableRevealMs: 5_000,
} as const;

export const WEIGHT_IMPORTANCE_PREVIEW = {
  lowMaxRatio: 0.5,
  highMinRatio: 1.5,
} as const;

// Mirrors the backend's configurable default. Kept in the shared UI config so
// every reasoned instructor action uses one disclosed validation rule.
export const RESOLUTION_REASON_MIN_LENGTH = 10;
export const INSTRUCTOR_NOTE_MAX_LENGTH = 1000;
// API encoding units for criterion results, independent of readiness thresholds.
export const CRITERION_SCORE_UNITS = { none: 0, full: 100 } as const;
// Stored check_result.score uses Numeric(5, 2). This bounds acknowledgment
// validation at storage precision; it does not calculate a displayed grade.
export const CRITERION_SCORE_DECIMAL_PLACES = 2;

/** Synthetic region id used only to route a criterion's recorded text
 * anchor through the existing document-pane focus engine. Real Flag ids
 * are positive; this value can therefore never be mistaken for one. */
export const CRITERION_ANCHOR_REGION_ID = -2147483647;
// Matches the existing single-pane document layout in signal.css.
export const DOCUMENT_SINGLE_PANE_MEDIA_QUERY = "(max-width: 1199px)";
export const PASSWORD_MIN_LENGTH = 8;
export const LIBRARY_SEARCH_DEBOUNCE_MS = 400;
// V-077: how often an own Library record that is still being read refreshes
// its status, and the most own pending uploads listed above the collection.
export const LIBRARY_STATUS_POLL_MS = 3000;
export const LIBRARY_PENDING_PAGE_SIZE = 100;
// V-077: Library page size; the backend caps it at 200.
export const LIBRARY_PAGE_SIZE = 50;
// V-078: the stored-files list uses the archive endpoint paging contract.
export const STORED_FILES_PAGE_SIZE = 50;
export const REVIEW_DESK_PAGE_SIZE = 20;
export const FLAG_LIST_INITIAL_COUNT = 8;
export const FLAG_LIST_BATCH_COUNT = 8;
export const COMPARISON_LEAD_PAGE_SIZE = FLAG_LIST_INITIAL_COUNT;
export const COMPARISON_EXCERPT_PREVIEW_LENGTH = 140;

/** Protocol value shared with the group-program filter API. */
export const UNSET_PROGRAM_FILTER = "__unset__";

export const SHARE_EXPIRY_OPTIONS = [
  { value: "none", label: "No expiry", days: null },
  { value: "7d", label: "7 days", days: 7 },
  { value: "30d", label: "30 days", days: 30 },
  { value: "90d", label: "90 days", days: 90 },
] as const;
