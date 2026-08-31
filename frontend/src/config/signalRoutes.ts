export interface SignalNavigationRoute {
  label: string;
  description: string;
  to: string;
  matches: (pathname: string) => boolean;
}

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
    description: "Stored manuscripts, comparison, and archive",
    to: "/library",
    matches: (path) => path.startsWith("/library"),
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
