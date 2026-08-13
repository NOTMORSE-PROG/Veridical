// A real modal (BUG-020, V-055): the shipped version of this component
// had `position: static`, no focus trap (Tab ejected to <body>), Escape
// did nothing, and no aria-modal/aria-labelledby — a component NAMED
// Modal that wasn't actually one. Built from the WAI-ARIA APG Dialog
// (Modal) pattern (w3.org/WAI/ARIA/apg/patterns/dialog-modal/),
// reimplemented with VERIDICAL's own tokens.
import { type ReactNode, useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { cx } from "./cx";
import { useInertBackground } from "./useInertBackground";

interface ModalProps {
  title: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  onClose?: () => void;
  // V-041: RerunModal's picker + estimate + per-item progress list needs
  // more room than every other modal's fixed 512px. Default "md" is
  // byte-identical to what every existing consumer already renders --
  // opting into "lg" (672px, the same width Progress.tsx's own page
  // wrapper already uses) is a per-modal choice, not a system default.
  size?: "md" | "lg";
}

const SIZE_CLASS: Record<"md" | "lg", string> = {
  md: "max-w-lg",
  lg: "max-w-2xl",
};

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function getFocusable(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

// BUG-020: a Tab-based focus trap alone still leaves background content
// reachable by a screen reader's own virtual/browse-mode cursor (which
// doesn't move DOM focus). Portalling to <body> and marking the app root
// `inert` while any modal is open makes the background genuinely
// unreachable by ANY input mode, not just Tab -- the WAI-ARIA APG's own
// Dialog pattern calls this out as part of "modal", not an extra.
// `useInertBackground` itself lives in ./useInertBackground (V-057) so
// the coach-mark tour shares the same open-count, not a second one.

export function ModalBackdrop({ children }: { children: ReactNode }) {
  useInertBackground();
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/50 p-4">
      {children}
    </div>,
    document.body,
  );
}

export function Modal({ title, children, footer, onClose, size = "md" }: ModalProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  // Focus moves into the dialog on open; returns to whatever opened it on
  // close (standing rule, verified mechanically not eyeballed).
  useEffect(() => {
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    const [first] = dialog ? getFocusable(dialog) : [];
    (first ?? dialog)?.focus();
    return () => {
      previouslyFocused.current?.focus();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Lock background scroll while open (WAI-ARIA APG's own Dialog
  // implementation notes) -- found live, V-055: didn't matter on 4c's
  // short dashboard, but this component is shared with longer screens
  // (4f, 4s) where a scrollable page behind an open modal is a real gap.
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  // Escape closes; Tab/Shift+Tab wraps within the dialog instead of
  // escaping to the page behind it.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        if (onClose) {
          event.stopPropagation();
          onClose();
        }
        return;
      }
      if (event.key !== "Tab") return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = getFocusable(dialog);
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
  }, [onClose]);

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      tabIndex={-1}
      className={cx(
        "flex max-h-[90vh] w-full flex-col gap-4 overflow-y-auto rounded-lg border border-border bg-panel p-6 text-sm shadow-lg",
        SIZE_CLASS[size],
      )}
    >
      <div className="flex items-start gap-3">
        <h2 id={titleId} className="text-md font-bold text-ink">
          {title}
        </h2>
        <span className="flex-1" />
        {onClose !== undefined && (
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="flex h-8 w-8 flex-none items-center justify-center rounded-md text-ink-tertiary hover:bg-status-neutral-bg hover:text-ink"
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              width="18"
              height="18"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            >
              <line x1="5" y1="5" x2="19" y2="19" />
              <line x1="19" y1="5" x2="5" y2="19" />
            </svg>
          </button>
        )}
      </div>
      {children}
      {footer !== undefined && <div className="flex justify-end gap-2">{footer}</div>}
    </div>
  );
}
