import { Link, useLocation, type LinkProps } from "react-router";
import { LIBRARY_RETURN } from "../config/signalRoutes";
import { ActionLink } from "../ui/ActionLink";
import { readLibraryReturn } from "./libraryReturn";
import { rememberRouteReturnFocus } from "./useRouteFocus";

interface LibraryReturnLinkProps extends Omit<LinkProps, "to" | "state"> {
  variant?: "primary" | "brand" | "secondary" | "quiet";
}

export function LibraryReturnLink({ variant, onClick, ...props }: LibraryReturnLinkProps) {
  const location = useLocation();
  const context = readLibraryReturn(location);
  const to = context?.returnPath ?? LIBRARY_RETURN.pathname;
  const state = context ? {
    comparisonMode: context.comparisonMode, comparisonSelection: context.comparisonSelection,
  } : undefined;
  const handleClick: LinkProps["onClick"] = (event) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey
      || event.shiftKey || event.altKey || !context) return;
    rememberRouteReturnFocus(to, location.pathname + location.search, context.elementId, { armed: true });
  };
  return variant ? <ActionLink {...props} to={to} state={state} variant={variant} onClick={handleClick} />
    : <Link {...props} to={to} state={state} onClick={handleClick} />;
}
