// V-057 — tour state: which step is active, and the two persisted
// actions (dismiss, replay). The step POSITION itself is client-side
// only (resets to step 1 on a fresh page load or a Replay) -- only
// "has this instructor ever finished/dismissed the tour" is persisted
// server-side, reusing Instructor.onboarding_dismissed_at (the same
// field V-055's banner used) via the existing dismiss mutation plus a
// new replay one. This is a deliberate simplification of the original
// spec's per-step server persistence: the ticket's own AC3 asks for
// dismissal to be "remembered per user, not per session" -- it does not
// ask for mid-tour step position to survive a full reload, and a tour
// that always restarts at step 1 on Replay matches both of the owner's
// own reference screenshots.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { useDismissOnboarding, useMe, useReplayOnboarding } from "../auth/useAuth";
import { useTaskModalActive } from "../components/modalActivity";
import { findTourAnchor, TOUR_HOME_PATH, TOUR_STEPS, type TourStep } from "./tourSteps";

const POLL_MS = 400;
const MAX_POLLS = 12; // ~4.8s -- covers async-loaded anchors (e.g. EscalatedPanel's own query) without polling forever

export interface ResolvedTourStep {
  step: TourStep;
  index: number;
  /** null = this step's control is not on the current screen; the card
   * renders unanchored and shows the step's `whereHint` (BUG-091). */
  el: HTMLElement | null;
}

export function useTour() {
  const { data: me } = useMe();
  const location = useLocation();
  const navigate = useNavigate();
  const dismiss = useDismissOnboarding();
  const replay = useReplayOnboarding();

  const [stepIndex, setStepIndex] = useState(0);
  const [dismissedLocally, setDismissedLocally] = useState(false);
  // Bounded anchor discovery, keyed by (step, route, active) so a change
  // to any of them restarts discovery without a separate reset effect.
  const [polls, setPolls] = useState({ key: "", count: 0 });
  const pendingEntry = useRef<HTMLElement | null>(null);
  const paused = useTaskModalActive();
  // BUG-581: the tour opens by itself only before the instructor starts
  // using the page. Its first step waits for the Review Desk's data, so it
  // could appear a second after load, under a click already on its way,
  // and take it. Once the instructor has clicked or typed, an unopened tour
  // stays closed until Replay introduction or the next visit. Replay is
  // asked for, so it always opens.
  const [interrupted, setInterrupted] = useState(false);
  const [shown, setShown] = useState(false);
  const [asked, setAsked] = useState(false);

  const active = Boolean(me) && !me?.onboarding_dismissed_at && !dismissedLocally;
  const held = interrupted && !shown && !asked;

  // BUG-091: every step renders, in order. The old resolver skipped any
  // step whose control was not on the current screen, so a day-one
  // account walked 1 -> 3 -> 5 under an "of 5" counter. A step whose
  // control belongs to this screen waits (bounded) for it to mount; a step
  // whose control lives elsewhere -- or never mounted -- renders
  // unanchored with its whereHint. Still never a pointer aimed at nothing
  // (V-057 AC5): an unanchored card has no spotlight.
  const step = active && !paused && !held ? (TOUR_STEPS[stepIndex] ?? null) : null;
  const discoveryKey = `${stepIndex}|${location.pathname}|${active}|${paused}`;
  const pollCount = polls.key === discoveryKey ? polls.count : 0;
  const anchor = step ? findTourAnchor(step, location.pathname) : null;
  const routeOwnsStep = step !== null && step.routeMatch(location.pathname);
  const waitingForAnchor = routeOwnsStep && anchor === null && pollCount < MAX_POLLS;
  const resolved: ResolvedTourStep | null =
    step && !waitingForAnchor ? { step, index: stepIndex, el: anchor } : null;

  // Before paint, so a click on the opened tour never counts as using the page.
  const opened = resolved !== null;
  useLayoutEffect(() => {
    if (opened && !shown) setShown(true);
  }, [opened, shown]);

  useEffect(() => {
    if (!active || shown || asked || interrupted) return;
    function onUse() { setInterrupted(true); }
    document.addEventListener("pointerdown", onUse, true);
    document.addEventListener("keydown", onUse, true);
    return () => {
      document.removeEventListener("pointerdown", onUse, true);
      document.removeEventListener("keydown", onUse, true);
    };
  }, [active, shown, asked, interrupted]);

  useEffect(() => {
    if (active || !pendingEntry.current) return;
    const target = pendingEntry.current;
    pendingEntry.current = null;
    // The tour's unmount cleanup releases inertness before the next dialog
    // opens. Focus the ordinary trigger so Cancel returns to a real control.
    if (!target.isConnected || target.closest("[inert]") || target.matches(":disabled")) return;
    target.focus();
    target.click();
  }, [active]);

  // Poll a bounded number of times while this screen's control for the
  // current step has not mounted yet -- catches an anchor that mounts
  // after an async fetch resolves, without a MutationObserver watching the
  // whole document. A route change restarts discovery (new discoveryKey).
  useEffect(() => {
    if (!routeOwnsStep || anchor || pollCount >= MAX_POLLS) return;
    const timer = setTimeout(() => {
      setPolls({ key: discoveryKey, count: pollCount + 1 });
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [routeOwnsStep, anchor, pollCount, discoveryKey]);

  function next() {
    if (!resolved) return;
    setStepIndex(resolved.index + 1);
  }

  function back() {
    if (!resolved || resolved.index === 0) return;
    setStepIndex(resolved.index - 1);
  }

  function dismissTour() {
    setDismissedLocally(true); // hide instantly, never gate on the mutation (Dashboard.tsx's own established pattern)
    dismiss.mutate();
  }

  function replayTour() {
    setDismissedLocally(false);
    setAsked(true);
    setStepIndex(0);
    replay.mutate();
    // BUG-303: start now, on the screen that holds the introduction's
    // controls, instead of silently firing the next time the Review Desk
    // happens to open.
    if (location.pathname !== TOUR_HOME_PATH) navigate(TOUR_HOME_PATH);
  }

  function enterTask() {
    if (!resolved?.el || !resolved.step.entryActionLabel) return;
    pendingEntry.current = resolved.el;
    dismissTour();
  }

  return {
    paused,
    resolved,
    stepNumber: resolved ? resolved.index + 1 : 0,
    totalSteps: TOUR_STEPS.length,
    isFirst: resolved?.index === 0,
    isLast: resolved?.index === TOUR_STEPS.length - 1,
    next,
    back,
    dismiss: dismissTour,
    replay: replayTour,
    enterTask,
  };
}
