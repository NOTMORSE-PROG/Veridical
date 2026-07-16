// Button — DESIGN.md §2 (wireframe .pbtn / .pbtn.pri / .pbtn.sm)
import type { ButtonHTMLAttributes } from "react";
import { cx } from "./cx";

export type ButtonVariant = "default" | "primary";
export type ButtonSize = "md" | "sm";

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  default: "border-border-button bg-panel text-ink",
  primary: "border-primary bg-primary text-on-primary",
};

const SIZE_CLASSES: Record<ButtonSize, string> = {
  md: "px-3.5 py-1.5 text-base",
  sm: "px-2.5 py-1 text-xs",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export function Button({
  variant = "default",
  size = "md",
  className,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      className={cx(
        "inline-flex items-center gap-1.5 rounded-control border font-medium whitespace-nowrap disabled:opacity-45",
        VARIANT_CLASSES[variant],
        SIZE_CLASSES[size],
        className,
      )}
      {...rest}
    />
  );
}
