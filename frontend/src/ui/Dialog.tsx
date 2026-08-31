import { type ReactNode, useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { useInertBackground } from "../components/useInertBackground";
import { cx } from "../components/cx";

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusable(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE));
}

export function Dialog({
  title,
  children,
  actions,
  onClose,
  wide = false,
}: {
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  onClose?: () => void;
  wide?: boolean;
}) {
  useInertBackground();
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  // Capture before the portal commits. A child with `autoFocus` can take
  // focus before effects run; capturing inside the effect would then
  // remember the soon-to-be-unmounted field instead of the trigger.
  const returnTarget = useRef<HTMLElement | null>(document.activeElement as HTMLElement | null);

  useEffect(() => {
    const dialog = dialogRef.current;
    const focusReturnTarget = returnTarget.current;
    const [first] = dialog ? focusable(dialog) : [];
    (first ?? dialog)?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
      focusReturnTarget?.focus();
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
          <h2 id={titleId}>{title}</h2>
          {onClose && (
            <button type="button" className="signal-icon-button" aria-label="Close" onClick={onClose}>
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
