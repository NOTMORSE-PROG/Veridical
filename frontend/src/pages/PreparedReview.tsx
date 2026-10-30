import { useEffect } from "react";
import { Link, useLocation } from "react-router";
import { PRACTICE_WORKSPACE_URL, practiceDestination } from "../config/practice";
import { ActionLink } from "../ui/ActionLink";

const DISCLOSURE = "Review a published manuscript, its criteria, and source evidence.";
const UNAVAILABLE = "The review workspace is unavailable. You can still sign in to your instructor account.";

export function PracticeAction() {
  const available = practiceDestination(PRACTICE_WORKSPACE_URL, window.location.origin);
  return (
    <div>
      {available ? <ActionLink to="/demo" variant="secondary">Explore the workspace</ActionLink> :
        <button type="button" className="signal-button signal-button--secondary" disabled>Explore the workspace</button>}
      <p className="signal-hero__account-note">{available ? DISCLOSURE : UNAVAILABLE}</p>
    </div>
  );
}

/** Public full-document transition; deliberately independent of account services. */
export function PreparedReviewRoute() {
  const location = useLocation();
  const destination = practiceDestination(
    PRACTICE_WORKSPACE_URL, window.location.origin,
    location.pathname, location.search, location.hash,
  );
  useEffect(() => {
    if (destination) window.location.replace(destination);
  }, [destination]);
  return (
    <div className="signal-theme signal-public-frame">
      <main className="signal-service-error__panel">
        <h1>{destination ? "Opening workspace." : "Review workspace"}</h1>
        <p role="status">{destination ? DISCLOSURE : UNAVAILABLE}</p>
        {destination && <p><a href={destination}>Continue to workspace</a></p>}
        <Link to="/signin">Sign in as instructor</Link>
      </main>
    </div>
  );
}
