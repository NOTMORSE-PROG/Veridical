import { forwardRef, type ReactNode } from "react";
import { cx } from "../components/cx";

interface AlertProps {
  title: string;
  tone?: "info" | "warning" | "error" | "success";
  role?: "alert" | "status";
  tabIndex?: number;
  className?: string;
  children?: ReactNode;
}

// DESIGN.md §4's "System warning" semantic pair requires "warning triangle
// and repair action" -- BUG-127/BUG-128's `ux-critic`/`ui-designer` pass
// found neither existed anywhere in the app. This is the triangle half
// (scoped to tone="warning" only -- info/error/success icons are a real,
// separate gap, not fixed here). 1.75px stroke per DESIGN.md §7's icon
// spec (the codebase's other inline icons currently drift to 2px).
export function WarningTriangleIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 3.5 21.5 20H2.5L12 3.5Z" />
      <path d="M12 9.5v5" />
      <path d="M12 17.25h.01" />
    </svg>
  );
}

export const Alert = forwardRef<HTMLDivElement, AlertProps>(function Alert(
  { title, tone = "info", role, tabIndex, className, children },
  ref,
) {
  return (
    <div
      ref={ref}
      role={role}
      tabIndex={tabIndex}
      className={cx("signal-alert", `signal-alert--${tone}`, className)}
    >
      <span aria-hidden="true" className="signal-alert__marker" />
      <div>
        <p className="signal-alert__title">
          {tone === "warning" && <WarningTriangleIcon />}
          {title}
        </p>
        {children && <div className="signal-alert__body">{children}</div>}
      </div>
    </div>
  );
});
