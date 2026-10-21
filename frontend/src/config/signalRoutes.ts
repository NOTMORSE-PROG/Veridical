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
