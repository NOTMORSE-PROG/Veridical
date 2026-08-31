import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cx } from "../components/cx";

export type SignalButtonVariant = "primary" | "brand" | "secondary" | "quiet" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: SignalButtonVariant;
  busy?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button({
  variant = "primary",
  busy = false,
  disabled,
  className,
  children,
  ...props
}: ButtonProps, ref) {
  return (
    <button
      ref={ref}
      className={cx("signal-button", `signal-button--${variant}`, className)}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...props}
    >
      {children}
    </button>
  );
});
