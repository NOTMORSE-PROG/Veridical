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

// BUG-566 rubric review. What each parser check means, in the instructor's
// words. An unknown code shows the server's own message.
export const RUBRIC_ISSUE_COPY: Record<string, string> = {
  not_in_source: "VERIDICAL could not find this wording in the document. Compare it with the original.",
  joins_cells: "This wording seems to combine text from more than one cell of the document's table.",
  partial_cell: "This wording seems to be only part of one cell in the document's table.",
  not_one_row: "These levels do not all come from one row of the table, in order.",
  wrong_row: "These levels may come from a table row that belongs to another criterion.",
  shared_cell: "This uses the same table cell as another level or criterion.",
  level_left_out: "The document's row has text under a level this criterion does not include.",
  wrong_column: "This wording is in the document, but under a different level of this row.",
};
// Why an instructor keeps wording the parser could not match. Values are the
// server's reason codes; the labels depend on what the parser raised, so a
// reason never contradicts the problem it answers.
export interface RubricKeepReasonOption {
  value: "document_wording" | "outside_table" | "compared_whole_format" | "other";
  label: string;
}
const OTHER_KEEP_REASON: RubricKeepReasonOption = { value: "other", label: "Another reason, explained in the note" };
export const RUBRIC_KEEP_REASONS: readonly RubricKeepReasonOption[] = [
  { value: "document_wording", label: "The document says it this way" },
  { value: "outside_table", label: "The wording comes from outside the table" },
  { value: "other", label: "Other reason" },
];
// Issue codes with their own choices. Codes not listed offer RUBRIC_KEEP_REASONS.
// "The document says it this way" is never offered for wording VERIDICAL
// could not find in the document.
// Items about the whole format (criterion_id null) are warnings from reading
// the format, such as criteria covering too little of it. There is no wording
// to keep, so they are offered and recorded under this key, not their code.
export const RUBRIC_WHOLE_FORMAT_ISSUE_KEY = "whole_format";
// The same reason, worded for checking a whole-format item again after the
// criteria changed, so agreeing with it means comparing the changed set.
export const RUBRIC_WHOLE_FORMAT_RECHECK_LABEL =
  "I compared the criteria, with these changes, against the original, and nothing in it is missing";
export const RUBRIC_KEEP_REASONS_BY_CODE: Readonly<Record<string, readonly RubricKeepReasonOption[]>> = {
  [RUBRIC_WHOLE_FORMAT_ISSUE_KEY]: [
    { value: "compared_whole_format", label: "I compared the criteria with the original, and nothing in it is missing" },
    OTHER_KEEP_REASON,
  ],
  not_in_source: [
    { value: "outside_table", label: "It is in the document outside the table, for example in a note under it" },
    OTHER_KEEP_REASON,
  ],
  joins_cells: [
    { value: "document_wording", label: "In the document, this level's cell holds all of this wording" },
    OTHER_KEEP_REASON,
  ],
  partial_cell: [
    { value: "document_wording", label: "This part of the cell is the whole description for this level" },
    OTHER_KEEP_REASON,
  ],
  wrong_column: [
    { value: "document_wording", label: "This level should be graded with that wording" },
    OTHER_KEEP_REASON,
  ],
  wrong_row: [
    { value: "document_wording", label: "This criterion should be graded with that row's wording" },
    OTHER_KEEP_REASON,
  ],
  shared_cell: [
    { value: "document_wording", label: "The document uses the same wording in both places" },
    OTHER_KEEP_REASON,
  ],
};
export const RUBRIC_KEEP_NOTE_MAX_LENGTH = INSTRUCTOR_NOTE_MAX_LENGTH;
// A required note must say something: at least this many letters or digits,
// so "x" or "..." is not accepted as the record of what was compared.
export const RUBRIC_KEEP_NOTE_MIN_CHARS = 12;
// Issues that say a level's wording is not where the document puts it. Keeping
// one needs a note, so the record says what the instructor compared rather
// than a reason that contradicts the flag.
// A whole-format warning needs one too: the note is the only record of what
// the instructor compared.
export const RUBRIC_KEEP_NOTE_REQUIRED_CODES: readonly string[] = [
  "not_in_source", "joins_cells", "partial_cell", "wrong_column", "wrong_row", "shared_cell", RUBRIC_WHOLE_FORMAT_ISSUE_KEY,
];
// What a format's review state is called on the format page, the dashboard
// and the review screen. parse_status only records how preparation went and
// never changes afterwards, so the live state comes from the open items.
export const RUBRIC_REVIEW_STATE_LABELS = {
  // "{count} item(s) to check" is built by itemsToCheck() in rubric/rubricReview.ts.
  flaggedAllChecked: "Flagged when prepared · all items checked",
  activeWithItems: "Active with items to check · correct it in a new version",
  allMatch: "All level descriptions match the document",
} as const;
// Criterion text can be a paragraph; names in buttons and summaries are cut here.
export const RUBRIC_CRITERION_NAME_MAX_CHARS = 60;
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
