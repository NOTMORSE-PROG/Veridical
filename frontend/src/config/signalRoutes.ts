export interface SignalNavigationRoute {
  label: string;
  description: string;
  to: string;
  matches: (pathname: string) => boolean;
}

// F8 review navigation protocol, shared by queue links and return controls.
export const REVIEW_DESK_RETURN = {
  pathname: "/dashboard",
  fallback: "/dashboard?queue=needs_review",
  pathParam: "desk_return",
  focusParam: "desk_focus",
  maxPathLength: 4096,
  actionPattern: /^review-desk-(?:primary|prior)-([1-9]\d*)$/,
  workflowPattern: /^\/(?:report\/[1-9]\d*(?:\/document)?|flags\/[1-9]\d*|checks\/[1-9]\d*)$/,
} as const;

/** Library source-reader context, independent of manuscript review routes. */
export const LIBRARY_RETURN = {
  pathname: "/library",
  pathParam: "library_return",
  originParam: "library_origin",
  modeParam: "library_compare",
  selectionParam: "library_selected",
  openMode: "open",
  closedMode: "closed",
  actionPrefix: "library-view-",
  sourcePattern: /^\/library\/([1-9]\d*)$/,
  maxPathLength: 4096,
  maxContextLength: 16384,
  maxProgramLength: 50,
  selectionLimit: 2,
  queryKeys: ["q", "program", "page"],
} as const;

/** F8.10: private history can return only to the same review. */
export const AUDIT_RETURN = {
  pathParam: "review_return",
  maxPathLength: REVIEW_DESK_RETURN.maxPathLength,
  actionPrefix: "report-audit-",
  routePattern: /^\/report\/([1-9]\d*)(?:\/document)?$/,
  criterionHashPattern: /^#criterion-record-([1-9]\d*)-([1-9]\d*)$/,
  hashes: ["#review-criteria", "#escalated-heading", "#integrity-signals", "#criteria-results-heading", "#final-decision"],
  positiveParams: ["finding", "flag", "match", "lead_page", "lead_return", "flags_visible", "lead_visible"],
  deskQueues: {
    needs_review: "needs_attention", checking: "checking", ready_to_decide: "checked",
    complete: "decided", not_checked: "not_checked",
  },
  deskSorts: ["newest", "oldest", "group_asc", "needs_review_desc"],
  enums: {
    view: ["decision", "matches", "finding"], pane: ["review", "analysis", "manuscript"],
    reading: ["full"], panel: ["explore"], ref: ["1"], quote: ["1"],
    lead_view: ["all", "open", "resolved"], flags_view: ["all", "high", "med", "low", "open", "resolved"],
  },
} as const;

/**
 * Stable workspace destinations. Workflow stages belong inside active tasks;
 * global navigation names places an instructor can predictably return to.
 */
export const SIGNAL_NAVIGATION: readonly SignalNavigationRoute[] = [
  {
    label: "Review Desk",
    description: "Manuscripts and work that need attention",
    to: "/dashboard",
    matches: (path) =>
      path === "/dashboard" ||
      path.startsWith("/checks/") ||
      path.startsWith("/report/") ||
      path.startsWith("/flags/"),
  },
  {
    label: "Required format",
    description: "Required formats and review criteria",
    to: "/rubric",
    matches: (path) => path.startsWith("/rubric"),
  },
  {
    label: "Library",
    description: "Manuscripts that originality checks compare against",
    to: "/library",
    matches: (path) => path.startsWith("/library"),
  },
  {
    label: "Stored files",
    description: "Uploaded manuscript files, and deleting one",
    to: "/stored-files",
    matches: (path) => path === "/stored-files",
  },
  {
    label: "Audit",
    description: "Trace system and instructor actions",
    to: "/audit",
    matches: (path) => path === "/audit",
  },
  {
    label: "Settings",
    description: "Account and review configuration",
    to: "/settings",
    matches: (path) => path === "/settings",
  },
] as const;
