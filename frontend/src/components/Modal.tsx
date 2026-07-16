// Modal + dim backdrop — DESIGN.md §1 modal radius/shadow (wireframe
// .pmwrap / .pmodal). Presentational for now: portal/overlay behavior lands
// with the first screen that opens one (V-012+).
import type { ReactNode } from "react";

interface ModalProps {
  title: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  onClose?: () => void;
}

export function ModalBackdrop({ children }: { children: ReactNode }) {
  return <div className="flex justify-center bg-backdrop p-7">{children}</div>;
}

export function Modal({ title, children, footer, onClose }: ModalProps) {
  return (
    <div
      role="dialog"
      className="flex flex-1 flex-col gap-3 rounded-modal border border-modal-border bg-panel p-4 shadow-modal"
    >
      <div className="flex items-center">
        <h2 className="text-md font-bold">{title}</h2>
        <span className="flex-1" />
        {onClose !== undefined && (
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="text-ink-faint"
          >
            ✕
          </button>
        )}
      </div>
      {children}
      {footer !== undefined && (
        <div className="flex justify-end gap-2">{footer}</div>
      )}
    </div>
  );
}
