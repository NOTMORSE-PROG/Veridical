import { useEffect, useRef, useState } from "react";
import { Link, Navigate } from "react-router";
import { useMe } from "../auth/useAuth";
import { UI_TIMING } from "../config/ui";
import { useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Button } from "../ui/Button";
import { SignalMark, SignalWordmark } from "../ui/SignalMark";

const STAGES = [
  {
    name: "Prepare",
    description: "Upload the required format, then confirm the criteria the system found.",
  },
  {
    name: "Check",
    description: "Add a manuscript and run the structural, semantic, and integrity checks.",
  },
  {
    name: "Review",
    description: "Inspect unresolved criteria and verify every possible issue against evidence.",
  },
  {
    name: "Decide",
    description: "Record the instructor decision, then export or share a read-only report.",
  },
] as const;

function PublicHeader() {
  return (
    <header className="signal-public-header">
      <Link to="/" className="signal-brand-link signal-on-dark" aria-label="VERIDICAL home">
        <SignalMark inverse />
        <SignalWordmark inverse />
      </Link>
      <p className="signal-project-label">
        Student capstone at T.I.P. Manila. Not an official service.
      </p>
    </header>
  );
}

function LandingPending() {
  const [stage, setStage] = useState<"hidden" | "checking" | "delayed">("hidden");

  useEffect(() => {
    const revealChecking = window.setTimeout(
      () => setStage("checking"),
      UI_TIMING.authPendingRevealMs,
    );
    const revealDelayed = window.setTimeout(
      () => setStage("delayed"),
      UI_TIMING.serviceUnavailableRevealMs,
    );
    return () => {
      window.clearTimeout(revealChecking);
      window.clearTimeout(revealDelayed);
    };
  }, []);

  const message =
    stage === "delayed"
      ? "The free server is still starting. Your work has not changed."
      : "Checking your sign-in status.";

  return (
    <div className="signal-theme signal-pending">
      <div className="signal-pending__content">
        <SignalMark inverse />
        <SignalWordmark inverse />
        {stage !== "hidden" && (
          <>
            <div aria-hidden="true" className="signal-pending__rail" />
            <p role="status" aria-live="polite" aria-busy="true">
              {message}
            </p>
          </>
        )}
      </div>
    </div>
  );
}

function ServiceUnavailable({ retry }: { retry: () => void }) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Service unavailable - VERIDICAL", headingRef);

  return (
    <div className="signal-theme signal-service-error">
      <main className="signal-service-error__panel">
        <SignalMark />
        <h1 ref={headingRef} tabIndex={-1}>
          VERIDICAL is temporarily unavailable.
        </h1>
        <p>Your work has not changed. Try the connection again in a moment.</p>
        <div>
          <Button type="button" variant="secondary" onClick={retry}>
            Try again
          </Button>
        </div>
      </main>
    </div>
  );
}

export function LandingRoute() {
  const { data: me, isPending, isError, refetch } = useMe();

  if (isError) return <ServiceUnavailable retry={() => void refetch()} />;
  if (isPending) return <LandingPending />;
  if (me) return <Navigate to="/dashboard" replace />;
  return <LandingPage />;
}

function LandingPage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("VERIDICAL", headingRef);

  return (
    <div className="signal-theme signal-public-frame" data-design="signal">
      <a href="#main-content" className="signal-skip-link">
        Skip to main content
      </a>
      <PublicHeader />

      <main id="main-content" tabIndex={-1} className="signal-landing-grid">
        <section className="signal-hero" aria-labelledby="landing-heading">
          <p className="signal-eyebrow">BSIT capstone project at T.I.P. Manila</p>
          <h1 id="landing-heading" ref={headingRef} tabIndex={-1}>
            Check the manuscript. Keep the decision human.
          </h1>
          <p className="signal-hero__body">
            VERIDICAL helps capstone instructors prepare criteria, check manuscripts, review
            evidence, and record a final decision.
          </p>
          <div className="signal-hero__actions">
            <ActionLink to="/signin" variant="brand">
              Sign in as instructor
            </ActionLink>
            <p className="signal-hero__account-note">
              Accounts are issued by your program administrator.
            </p>
          </div>
        </section>

        <section className="signal-process" aria-labelledby="process-heading">
          <p className="signal-eyebrow">One review path</p>
          <h2 id="process-heading">Four stages from format to final decision.</h2>
          <ol>
            {STAGES.map((stage, index) => (
              <li key={stage.name}>
                <span aria-hidden="true" className="signal-process__number">
                  {index + 1}
                </span>
                <div>
                  <h3>{stage.name}</h3>
                  <p>{stage.description}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>
      </main>

      <footer className="signal-public-footer">
        <p>
          VERIDICAL is an independent capstone project by BSIT students at Technological
          Institute of the Philippines, Manila. It is not an official T.I.P. service.
        </p>
      </footer>
    </div>
  );
}
