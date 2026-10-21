import { createPath, Link, parsePath, useLocation, type LinkProps } from "react-router";
import { REVIEW_DESK_RETURN } from "../config/signalRoutes";
import { isReviewWorkflowPath, readReviewDeskReturn, reviewDeskDestination } from "./reviewDeskReturn";
import { rememberRouteReturnFocus } from "./useRouteFocus";

/** F8: preserve the original desk only on explicit internal review links. */
export function WorkflowLink({ to, onClick, ...props }: LinkProps) {
  const location = useLocation();
  const destination = reviewDeskDestination(to, location, props.id);
  return <Link {...props} to={destination} onClick={(event) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey
      || event.shiftKey || event.altKey || location.pathname !== REVIEW_DESK_RETURN.pathname) return;
    const href = typeof destination === "string" ? destination : createPath(destination);
    const target = parsePath(href);
    if (!isReviewWorkflowPath(target.pathname ?? "")) return;
    const context = readReviewDeskReturn({ pathname: target.pathname ?? "", search: target.search ?? "" });
    if (context?.elementId) rememberRouteReturnFocus(context.returnPath, (target.pathname ?? "") + (target.search ?? ""), context.elementId);
  }} />;
}
