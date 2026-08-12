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
// `professor` re-reviews the shipped strings before this ticket closes.
export interface TourStep {
  id: string;
  routeMatch: (pathname: string) => boolean;
  /** Tried in order; the first selector with a real, non-zero-size
   * match wins (handles this app's existing desktop/mobile dual-render
   * pattern, e.g. AppShell's separate desktop/mobile replay buttons). */
  anchorSelectors: string[];
  title: string;
  body: string;
}

export const TOUR_STEPS: readonly TourStep[] = [
  {
    id: "upload-format",
    routeMatch: (pathname) => pathname === "/dashboard",
    anchorSelectors: ['[data-tour="upload-format-cta"]'],
    title: "Format comes first",
    body: "Upload your rubric or format document here first, before any manuscript can be checked against it.",
  },
  {
    id: "confirm-rubric",
    routeMatch: (pathname) => /^\/rubric\/[^/]+\/review$/.test(pathname),
    anchorSelectors: ['[data-tour="confirm-rubric-cta"]'],
    title: "Review before you confirm",
    body: "Review and correct anything VERIDICAL got wrong before you confirm. Nothing runs on a manuscript until you do.",
  },
  {
    id: "new-check",
    routeMatch: (pathname) => pathname === "/dashboard",
    anchorSelectors: ['[data-tour="new-check-cta"]'],
    title: "Run a check when you're ready",
    body: "Pick a manuscript and your active format here to start a check.",
  },
  {
    id: "escalation",
    routeMatch: (pathname) => /^\/report\/[^/]+$/.test(pathname),
    anchorSelectors: ['[data-tour="escalated-panel"]'],
    title: "This is yours to decide",
    // `professor` finding: "cannot agree with itself" implied the split-
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
    anchorSelectors: ['[data-tour="replay-tour-desktop"]', '[data-tour="replay-tour-mobile"]'],
    title: "Come back anytime",
    body: "Replay this tour anytime from this button.",
  },
] as const;

interface ResolvedStep {
  step: TourStep;
  index: number;
  el: HTMLElement;
}

// The entire "which step renders right now" logic — no per-step
// branching. Starts scanning at `fromIndex` and moves forward (never
// backward): a step whose page hasn't been visited yet is skipped, not
// treated as an error, and the tour never renders a pointer aimed at
// nothing (V-057 AC5).
export function resolveTourStep(fromIndex: number, pathname: string): ResolvedStep | null {
  for (let i = fromIndex; i < TOUR_STEPS.length; i++) {
    const step = TOUR_STEPS[i];
    if (!step.routeMatch(pathname)) continue;
    const el = step.anchorSelectors
      .map((selector) => document.querySelector<HTMLElement>(selector))
      .find((node): node is HTMLElement => {
        if (!node) return false;
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      });
    if (el) return { step, index: i, el };
  }
  return null;
}
