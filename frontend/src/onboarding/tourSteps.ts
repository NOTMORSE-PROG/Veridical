// V-057 — coach-mark tour step data. A plain array, not JSX branching
// per step (charter rule 7 / "push complexity into data"): the engine
// in CoachMark.tsx and useTour.ts reads this array and never has a
// per-step switch statement of its own.
//
// Five steps, deliberately not more (dated evidence, 2026-08-11:
// completion drops sharply past ~4 steps; a visible position indicator
// measurably helps — directional, not a peer-reviewed figure, treated
// as such). A sixth step anchored to 4i (flag evidence) was considered
// and cut: no report-screen control links to /flags/:id yet (grepped,
// confirmed absent) — speccing a step against a link that doesn't
// exist would be a step aimed at nothing. Add it once that link ships.
//
// Every string below is pre-vetted against ground rule 1 (VERIDICAL
// escalates, never decides) and ground rule 3 (honest wording) —
// panel review re-reviews the shipped strings before this ticket closes.
export interface TourStep {
  id: string;
  routeMatch: (pathname: string) => boolean;
  /** Tried in order; the first selector with a real, non-zero-size
   * match wins (handles this app's existing desktop/mobile dual-render
   * pattern, e.g. AppShell's separate desktop/mobile replay buttons). */
  anchorSelectors: string[];
  title: string;
  body: string;
  /** Only safe dialog-entry steps opt in; never confirmation or evaluation. */
  entryActionLabel?: string;
  /** BUG-091: shown when this step's control is not on the current screen
   * (a day-one account has no format review or report yet). The step still
   * renders -- unanchored, with no spotlight -- and says where the control
   * lives, so "Step N of 5" never silently skips a step. */
  whereHint: string;
}

/** BUG-303: the screen the introduction starts on. Replay from any other
 * screen visibly navigates here first instead of firing later, elsewhere. */
export const TOUR_HOME_PATH = "/dashboard";

export const TOUR_STEPS: readonly TourStep[] = [
  {
    id: "upload-format",
    routeMatch: (pathname) => pathname === TOUR_HOME_PATH,
    anchorSelectors: ['[data-tour="upload-format-cta"]'],
    title: "Format comes first",
    body: "Add your program's rubric or format document. You can review the prepared criteria before using it.",
    entryActionLabel: "Open format upload",
    whereHint: "You will find this on the Review Desk.",
  },
  {
    id: "confirm-rubric",
    routeMatch: (pathname) => /^\/rubric\/[^/]+\/review$/.test(pathname),
    anchorSelectors: ['[data-tour="confirm-rubric-cta"]'],
    title: "Review before you confirm",
    body: "Review and correct anything VERIDICAL got wrong before you confirm. Nothing runs on a manuscript until you do.",
    whereHint: "This appears on the format review screen after you upload a required format.",
  },
  {
    id: "new-check",
    routeMatch: (pathname) => pathname === TOUR_HOME_PATH,
    anchorSelectors: ['[data-tour="new-check-cta"]'],
    title: "Run a check when you're ready",
    body: "Choose an uploaded manuscript and the active format. Review the setup before starting the check.",
    entryActionLabel: "Choose manuscript",
    whereHint: "You will find this on the Review Desk.",
  },
  {
    id: "escalation",
    routeMatch: (pathname) => /^\/report\/[^/]+$/.test(pathname),
    anchorSelectors: ['[data-tour="escalated-panel"]'],
    whereHint: "This appears on a manuscript's report after a check finishes.",
    title: "This is yours to decide",
    // panel review finding: "cannot agree with itself" implied the split-
    // vote case specifically, but the escalation gate's real, dominant
    // trigger (verified against every escalated row on this account,
    // 10/10) is "neither grading pass produced a usable verdict at all"
    // -- a materially different, less coherent-sounding failure than an
    // active disagreement. EscalatedPanel.tsx's own voteSummary() already
    // treats these as distinct facts that "must never read alike" (V-050)
    // -- this copy now matches that distinction instead of collapsing it.
    body: "When VERIDICAL can't reach a confident verdict on a criterion, it asks you to decide instead of guessing.",
  },
  {
    id: "replay",
    routeMatch: () => true,
    // Menu items disappear after replay. Persistent menu triggers provide
    // a visible fallback on routes without a format/check anchor.
    anchorSelectors: ['[data-tour="replay-tour-desktop"]', '[data-tour="replay-tour-account"]'],
    title: "Come back anytime",
    body: "Use Replay introduction in your account controls whenever you need a reminder.",
    whereHint: "Open Account in the top bar to find it.",
  },
] as const;

/** Find the live, actually-rendered control a step points at on this
 * route, or null. Tried in selector order; a zero-size match (present in
 * the DOM but not laid out) does not count. No per-step branching. */
export function findTourAnchor(step: TourStep, pathname: string): HTMLElement | null {
  if (!step.routeMatch(pathname)) return null;
  return (
    step.anchorSelectors
      .map((selector) => document.querySelector<HTMLElement>(selector))
      .find((node): node is HTMLElement => {
        if (!node) return false;
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      }) ?? null
  );
}
