import type { LinkProps } from "react-router";
import { Link } from "react-router";
import { cx } from "../components/cx";

interface ActionLinkProps extends Omit<LinkProps, "className"> {
  className?: string;
  variant?: "primary" | "brand" | "secondary" | "quiet";
}

// BUG-108: `ActionLink` and `Button` used to build their class from the
// exact same string (`signal-button signal-button--{variant}`), so a
// navigating control and an in-place-action control were pixel-identical
// -- "Review N unresolved criteria" (navigates) and "Start check" (opens a
// modal) rendered as the same button with no way to tell which is which
// before clicking (ux-critic, live, measured via getComputedStyle: same
// background/color/border/height). `variant` encodes EMPHASIS, not idiom;
// nothing expressed idiom at all. This trailing chevron is the fix: it
// rides the emphasis system instead of replacing it, so every current and
// future `ActionLink` gets it for free, `Button` never does, and a shape
// (not just color) marks "this leaves the page" per WCAG 1.4.1. Same
// pattern GOV.UK's own "Start now" button uses for the identical reason.
export function TrailingChevronIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M9 5l7 7-7 7" />
    </svg>
  );
}

export function ActionLink({
  variant = "primary",
  className,
  children,
  ...props
}: ActionLinkProps) {
  return (
    <Link
      className={cx("signal-button", `signal-button--${variant}`, className)}
      {...props}
    >
      {children}
      <TrailingChevronIcon />
    </Link>
  );
}
