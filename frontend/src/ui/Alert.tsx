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
        <p className="signal-alert__title">{title}</p>
        {children && <div className="signal-alert__body">{children}</div>}
      </div>
    </div>
  );
});
