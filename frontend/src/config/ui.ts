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
// The manuscript review steps stay pinned only while they take at most this
// share of the review pane's height; in a short window or at high zoom they
// scroll with the content instead.
export const WORKSPACE_STEPS_MAX_PINNED_SHARE = 0.34;
// Long review lists are split into pages of this size rather than one scroll.
export const LIST_PAGE_SIZE = { findings: 8, imageReadings: 6 } as const;
export const PASSWORD_MIN_LENGTH = 8;
// A failed upload part waits this long times its attempt number before it is
// sent again; the retry count comes from the server.
export const UPLOAD_PART_RETRY_DELAY_MS = 1000;
export const LIBRARY_SEARCH_DEBOUNCE_MS = 400;
// V-077: how often an own Library record that is still being read refreshes
// its status, and the most own pending uploads listed above the collection.
export const LIBRARY_STATUS_POLL_MS = 3000;
export const LIBRARY_PENDING_PAGE_SIZE = 100;
// V-077: Library page size; the backend caps it at 200.
export const LIBRARY_PAGE_SIZE = 50;
// V-078: the stored-files list uses the archive endpoint paging contract.
export const STORED_FILES_PAGE_SIZE = 10;
// Manuscripts at least this large upload in the background (V-090) so the
// instructor is not held on the upload dialog while the server prepares them.
export const BACKGROUND_UPLOAD_MIN_BYTES = 10 * 1024 * 1024;
// While the server is busy with another file transfer, a background upload
// waits and retries instead of failing; it gives up after this many tries.
// Dismissed tray uploads remembered per instructor on this device. The server
// lists only the last day's uploads, so a short list covers them.
export const BACKGROUND_UPLOAD_DISMISSED_KEPT = 50;
export const BACKGROUND_UPLOAD_BUSY_RETRY = { delayMs: 15_000, maxAttempts: 12 } as const;
export const REVIEW_DESK_PAGE_SIZE = 20;
export const FLAG_LIST_INITIAL_COUNT = 8;
export const FLAG_LIST_BATCH_COUNT = 8;
export const COMPARISON_LEAD_PAGE_SIZE = FLAG_LIST_INITIAL_COUNT;
export const COMPARISON_EXCERPT_PREVIEW_LENGTH = 140;

/** Protocol value shared with the group-program filter API. */
export const UNSET_PROGRAM_FILTER = "__unset__";

export const SHARE_EXPIRY_OPTIONS = [
  { value: "7d", label: "7 days", days: 7 },
  { value: "30d", label: "30 days", days: 30 },
  { value: "90d", label: "90 days", days: 90 },
  { value: "none", label: "No expiry", days: null },
] as const;

// V-089: one bulk confirm or dismiss. The server enforces its own limits
// (FLAG_BULK_RESOLVE_MAX_FLAGS / _MAX_TEXT_CHARS); these mirror its defaults
// so the screen can say so before anything is sent.
export const BULK_RESOLVE_MAX_LOCATIONS = 200;
export const BULK_RESOLVE_MAX_TEXT_CHARS = 2000;
// Below this width (including a zoomed desktop window) the selection bar sits
// in the page above the list instead of fixed to the screen.
export const BULK_BAR_INLINE_MEDIA_QUERY = "(max-width: 1023px)";
