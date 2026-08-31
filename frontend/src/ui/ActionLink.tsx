import type { LinkProps } from "react-router";
import { Link } from "react-router";
import { cx } from "../components/cx";

interface ActionLinkProps extends Omit<LinkProps, "className"> {
  className?: string;
  variant?: "primary" | "brand" | "secondary" | "quiet";
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
    </Link>
  );
}
