// BUG-222: an unmatched route previously fell through to React Router's
// default ErrorBoundary -- a raw, developer-addressed screen ("Hey
// developer 👋...") that any bookmarked, renamed, or mistyped URL could
// reach. This is the app's own route for that case, wired as App.tsx's
// catch-all (`path="*"`), reusing the same panel treatment
// LandingRoute's ServiceUnavailable state already established for "this
// isn't the screen you expected."
import { useRef } from "react";
import { useMe } from "../auth/useAuth";
import { useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { SignalMark } from "../ui/SignalMark";

export function NotFoundPage() {
  const { data: me } = useMe();
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Page not found - VERIDICAL", headingRef);

  return (
    <div className="signal-theme signal-service-error">
      <main className="signal-service-error__panel">
        <SignalMark />
        <h1 ref={headingRef} tabIndex={-1}>
          This page doesn't exist.
        </h1>
        <p>
          The link may be outdated, or the address may have been typed incorrectly. Nothing has
          been changed or lost.
        </p>
        <div>
          <ActionLink to={me ? "/dashboard" : "/signin"} variant="primary">
            {me ? "Go to Review Desk" : "Go to sign in"}
          </ActionLink>
        </div>
      </main>
    </div>
  );
}
