import { Link, useLocation, type LinkProps } from "react-router";
import { REVIEW_DESK_RETURN } from "../config/signalRoutes";
import { ActionLink } from "../ui/ActionLink";
import { readReviewDeskReturn, validReviewDeskPath } from "./reviewDeskReturn";
import { rememberRouteReturnFocus } from "./useRouteFocus";

interface ReviewDeskReturnLinkProps extends Omit<LinkProps, "to"> {
  fallback?: string;
  variant?: "primary" | "brand" | "secondary" | "quiet";
}

export function ReviewDeskReturnLink({ fallback, variant, onClick, ...props }: ReviewDeskReturnLinkProps) {
  const location = useLocation();
  const context = readReviewDeskReturn(location);
  const to = context?.returnPath ?? validReviewDeskPath(fallback) ?? REVIEW_DESK_RETURN.fallback;
  const handleClick: LinkProps["onClick"] = (event) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey
      || event.shiftKey || event.altKey || !context?.elementId) return;
    rememberRouteReturnFocus(to, location.pathname + location.search, context.elementId, { armed: true });
  };
  return variant ? <ActionLink {...props} to={to} variant={variant} onClick={handleClick} />
    : <Link {...props} to={to} onClick={handleClick} />;
}
