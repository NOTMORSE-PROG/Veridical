// V-057 — anchored coach-mark tour. Reuses Modal.tsx's own WAI-ARIA APG
// Dialog mechanics (focus trap, Escape, inert background, focus-return)
// rather than inventing a second implementation of the same pattern;
// what's new here is anchored positioning + a spotlight cutout, neither
// of which Modal.tsx needed. Custom-built, no third-party tour library
// (charter: zero-budget, custom-everything).
import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { useInertBackground } from "../components/useInertBackground";
import { useCoachMarkPosition } from "./useCoachMarkPosition";
import { useTour } from "./TourContext";

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function getFocusable(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

function CloseIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <line x1="5" y1="5" x2="19" y2="19" />
      <line x1="19" y1="5" x2="5" y2="19" />
    </svg>
  );
}

// One dot per step, filled/wide for the current step, filled-narrow for
// past steps, hollow for future ones -- the exact filled/hollow-by-
// state convention Stepper.tsx's own connecting rail already uses
// (DESIGN.md §2), reused here for the same "settled vs. not" meaning
// rather than inventing a second visual language for one idea.
function StepDots({ current, total }: { current: number; total: number }) {
  return (
    <div className="flex items-center gap-1.5" aria-hidden="true">
      {Array.from({ length: total }, (_, i) => {
        const stepNum = i + 1;
        const isCurrent = stepNum === current;
        const isPast = stepNum < current;
        return (
          <span
            key={stepNum}
            className="h-1.5 rounded-full motion-safe:transition-[width,background-color]"
            style={{
              width: isCurrent ? "16px" : "6px",
              backgroundColor: isCurrent
                ? "var(--color-action)"
                : isPast
                  ? "var(--color-neutral-400)"
                  : "var(--color-panel)",
              border: isCurrent || isPast ? "none" : "1px solid var(--color-border)",
            }}
          />
        );
      })}
    </div>
  );
}

function CoachMarkCard({
  anchorEl,
  title,
  body,
  stepNumber,
  totalSteps,
  isFirst,
  isLast,
  onBack,
  onNext,
  onDismiss,
}: {
  anchorEl: HTMLElement;
  title: string;
  body: string;
  stepNumber: number;
  totalSteps: number;
  isFirst: boolean;
  isLast: boolean;
  onBack: () => void;
  onNext: () => void;
  onDismiss: () => void;
}) {
  useInertBackground();
  const position = useCoachMarkPosition(anchorEl);
  const titleId = useId();
  const bodyId = useId();
  const cardRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const hasFocusedRef = useRef(false);

  // Full remount per step (key={step.id} at the call site) means a fresh
  // instance mounts per step, but `position` starts null on that first
  // render (the positioner needs a layout pass) -- the dialog itself
  // doesn't exist in the DOM yet on that first commit, so focusing it
  // then would silently no-op. Wait for `position` to actually resolve,
  // then focus exactly once per instance (AC6).
  useEffect(() => {
    if (!position || hasFocusedRef.current) return;
    hasFocusedRef.current = true;
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const card = cardRef.current;
    const [first] = card ? getFocusable(card) : [];
    (first ?? card)?.focus();
  }, [position]);

  // Return focus on unmount -- a separate effect (not this one's cleanup)
  // since the mount-focus effect above can legitimately re-run its body
  // zero times if `position` never resolves before the step advances.
  useEffect(() => {
    return () => {
      // On close, prefer the anchor itself (still visible, now
      // reachable again once inert lifts) over whatever the previous
      // step's own opener was -- matches Dashboard.tsx's own rule for
      // this exact class of unmount ("focus must move to the next
      // logical heading rather than fall back to <body>").
      if (anchorEl.isConnected) anchorEl.focus();
      else previouslyFocused.current?.focus();
    };
  }, [anchorEl]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        onDismiss();
        return;
      }
      if (event.key !== "Tab") return;
      const card = cardRef.current;
      if (!card) return;
      const focusable = getFocusable(card);
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [onDismiss]);

  if (!position) return null;
  const { anchorRect, cardTop, cardLeft } = position;

  return (
    <>
      {/* Spotlight cutout — a proxy overlay sized to the anchor's live
          rect, pointer-events: none so it never intercepts clicks (the
          real control underneath is intentionally inert while the tour
          is open, not reachable through the overlay — see CoachMark's
          own module doc for why). Ring reuses --color-action, the same
          near-black token already used for :focus-visible rings
          (tokens.css), not a status hue -- a spotlight is "look here,"
          not "this is flagged" (DESIGN.md §1.7's overreliance rule). */}
      <div
        aria-hidden="true"
        className="motion-safe:transition-[top,left,width,height] motion-safe:duration-(--motion-duration-base) motion-safe:ease-(--motion-ease-standard)"
        style={{
          position: "fixed",
          top: anchorRect.top - 4,
          left: anchorRect.left - 4,
          width: anchorRect.width + 8,
          height: anchorRect.height + 8,
          borderRadius: "10px",
          boxShadow:
            "0 0 0 4px var(--color-panel), 0 0 0 7px var(--color-action), 0 0 0 9999px var(--color-scrim)",
          pointerEvents: "none",
          zIndex: "var(--z-tour-spotlight)",
        }}
      />
      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        tabIndex={-1}
        className="motion-safe:animate-coachmark-in flex flex-col gap-3 rounded-lg border border-border bg-panel p-4 shadow-(--elevation-overlay)"
        style={{ position: "fixed", top: cardTop, left: cardLeft, width: "320px", maxWidth: "calc(100vw - 32px)", zIndex: "var(--z-tour-card)" }}
      >
        <div className="flex items-start gap-2">
          <h2 id={titleId} className="flex-1 text-sm font-bold text-ink">
            {title}
          </h2>
          <button
            type="button"
            aria-label="Close tour"
            onClick={onDismiss}
            className="flex h-11 w-11 flex-none items-center justify-center rounded-md text-ink-tertiary hover:bg-status-neutral-bg hover:text-ink"
          >
            <CloseIcon />
          </button>
        </div>
        <p id={bodyId} className="text-sm text-ink-secondary">
          {body}
        </p>
        <div className="flex items-center justify-between gap-2">
          <StepDots current={stepNumber} total={totalSteps} />
          <div className="flex gap-2">
            {!isFirst && (
              <button
                type="button"
                onClick={onBack}
                className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-3.5 text-sm font-bold text-ink hover:bg-status-neutral-bg"
              >
                Back
              </button>
            )}
            <button
              type="button"
              onClick={onNext}
              className="flex h-11 items-center justify-center rounded-md bg-action px-3.5 text-sm font-bold text-on-action hover:bg-action-hover"
            >
              {isLast ? "Done" : "Next"}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

export function CoachMark() {
  const tour = useTour();
  if (!tour.resolved) return null;

  return createPortal(
    <>
      {/* ux-critic finding, fixed: this used to live INSIDE
          CoachMarkCard, which fully unmounts and remounts every step
          (key={step.id} below) -- a live region whose content arrives
          in the same paint as the region itself is announced
          unreliably across AT/browser combinations (WAI-ARIA authoring
          guidance: the region must already exist before its content
          changes). Rendered here instead, on the CoachMark() level,
          which re-renders but never remounts across step changes, so
          only the TEXT changes -- the reliable pattern. */}
      <span className="sr-only" aria-live="polite">
        Step {tour.stepNumber} of {tour.totalSteps}: {tour.resolved.step.title}
      </span>
      <CoachMarkCard
        key={tour.resolved.step.id}
        anchorEl={tour.resolved.el}
        title={tour.resolved.step.title}
        body={tour.resolved.step.body}
        stepNumber={tour.stepNumber}
        totalSteps={tour.totalSteps}
        isFirst={tour.isFirst}
        isLast={tour.isLast}
        onBack={tour.back}
        onNext={tour.isLast ? tour.dismiss : tour.next}
        onDismiss={tour.dismiss}
      />
    </>,
    document.body,
  );
}
