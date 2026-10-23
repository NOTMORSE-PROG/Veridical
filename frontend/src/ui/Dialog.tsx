import { type ReactNode, type RefObject, useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { useInertBackground } from "../components/useInertBackground";
import { cx } from "../components/cx";

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), summary, [contenteditable="true"], [tabindex]:not([tabindex="-1"])';

function focusable(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(available);
}

function available(target: HTMLElement | null): target is HTMLElement {
  if (!target?.isConnected || target === document.body || target === document.documentElement
    || target.matches(":disabled") || target.closest("[inert], [hidden], [aria-hidden='true']")) return false;
  for (let node: HTMLElement | null = target; node; node = node.parentElement) {
    const style = window.getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse") return false;
    if (node.tagName === "DETAILS" && !node.hasAttribute("open")
      && !node.querySelector(":scope > summary")?.contains(target)) return false;
  }
  return true;
}

function focusTarget(target: HTMLElement | null): boolean {
  if (!available(target)) return false;
  target.focus();
  return document.activeElement === target;
}

export function Dialog({
  title,
  children,
  actions,
  onClose,
  wide = false,
  returnFocusTarget,
  shouldRestoreFocus,
  fallbackFocusRef,
  closeButtonRef,
  initialFocusTitle = false,
}: {
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  onClose?: () => void;
  wide?: boolean;
  // Null preserves the captured trigger. Callers can supply a workflow
  // continuation and a persistent fallback without changing other dialogs.
  returnFocusTarget?: () => HTMLElement | null;
  // An accepted caller may retire restoration after a newer workflow handoff.
  shouldRestoreFocus?: () => boolean;
  fallbackFocusRef?: RefObject<HTMLElement | null>;
  closeButtonRef?: RefObject<HTMLButtonElement | null>;
  initialFocusTitle?: boolean;
}) {
  useInertBackground();
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const focusTitle = useRef(initialFocusTitle);
  // Capture before the portal commits. A child with `autoFocus` can take
  // focus before effects run; capturing inside the effect would then
  // remember the soon-to-be-unmounted field instead of the trigger.
  const returnTarget = useRef<HTMLElement | null>(document.activeElement as HTMLElement | null);
  const returnOptions = useRef({ returnFocusTarget, shouldRestoreFocus, fallback: () => fallbackFocusRef?.current ?? null });
  returnOptions.current.returnFocusTarget = returnFocusTarget;
  returnOptions.current.shouldRestoreFocus = shouldRestoreFocus;
  returnOptions.current.fallback = () => fallbackFocusRef?.current ?? null;

  useEffect(() => {
    const dialog = dialogRef.current;
    const focusReturnTarget = returnTarget.current;
    const options = returnOptions.current;
    const [first] = dialog ? focusable(dialog) : [];
    (focusTitle.current ? titleRef.current : first ?? dialog)?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
      // The inert-background effect releases first. Preserve a newer route,
      // dialog or deliberate outside focus before attempting either target.
      const active = document.activeElement as HTMLElement | null;
      if (available(active) && !dialog?.contains(active)) return;
      if (options.shouldRestoreFocus && !options.shouldRestoreFocus()) return;
      const preferred = options.returnFocusTarget?.() ?? focusReturnTarget;
      if (!focusTarget(preferred)) focusTarget(options.fallback());
    };
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && onClose) {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const items = focusable(dialogRef.current);
      if (items.length === 0) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === titleRef.current)) {
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

  return createPortal(
    <div className="signal-dialog-backdrop">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={cx("signal-dialog", wide && "signal-dialog--wide")}
      >
        <header>
          <h2 id={titleId} ref={titleRef} tabIndex={initialFocusTitle ? -1 : undefined}>{title}</h2>
          {onClose && (
            <button ref={closeButtonRef} type="button" className="signal-icon-button" aria-label="Close" onClick={onClose}>
              <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square">
                <path d="M5 5l14 14M19 5 5 19" />
              </svg>
            </button>
          )}
        </header>
        {children && <div className="signal-dialog__body">{children}</div>}
        {actions && <footer>{actions}</footer>}
      </div>
    </div>,
    document.body,
  );
}
