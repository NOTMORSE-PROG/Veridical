// Screen 4v — pre-auth landing (V-055). `/` used to blind-redirect straight
// to /signin with zero content ever painted (Nielsen "visibility of system
// status", Jakob's Law, and the trust-first domain lens all named this a
// real gap — context/RESEARCH.md §14/§20). LandingRoute fast-paths an
// already-authenticated visitor straight to /dashboard without ever
// rendering this page; only a confirmed-anonymous visitor sees it.
import { useEffect, useRef, useState } from "react";
import { Link, Navigate } from "react-router";
import { useMe } from "../auth/useAuth";
import { useRouteFocus } from "../routing/useRouteFocus";

function CheckCircleIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="28"
      height="28"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="9" />
      <polyline points="8 12.5 10.7 15.2 16 9.5" />
    </svg>
  );
}

function AlertTriangleIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="28"
      height="28"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 3.5 21 20H3Z" />
      <line x1="12" y1="9.5" x2="12" y2="14" />
      <line x1="12" y1="16.8" x2="12" y2="16.9" />
    </svg>
  );
}

function CrossCircleIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="28"
      height="28"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="9" />
      <line x1="8.8" y1="8.8" x2="15.2" y2="15.2" />
      <line x1="15.2" y1="8.8" x2="8.8" y2="15.2" />
    </svg>
  );
}

function DocumentCheckIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="24"
      height="24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="5" y="3" width="14" height="18" rx="1.5" />
      <line x1="8" y1="7.5" x2="16" y2="7.5" />
      <polyline points="8.5 13.5 10.7 16 15.5 10.7" />
    </svg>
  );
}

function QuoteMarksIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="24"
      height="24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M6.5 7.5h4v4.5c0 2.8-1.6 4.5-4 4.8" />
      <path d="M14.5 7.5h4v4.5c0 2.8-1.6 4.5-4 4.8" />
    </svg>
  );
}

function BarChartIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="24"
      height="24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <line x1="4" y1="20" x2="20" y2="20" />
      <line x1="7" y1="20" x2="7" y2="13" />
      <line x1="12" y1="20" x2="12" y2="7" />
      <line x1="17" y1="20" x2="17" y2="15.5" />
    </svg>
  );
}

function LayersIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="24"
      height="24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="4" y="4" width="12" height="12" rx="1.5" />
      <path d="M8.5 20H16.5A1.5 1.5 0 0 0 18 18.5V10.5" />
    </svg>
  );
}

const CHECKS = [
  {
    Icon: DocumentCheckIcon,
    title: "Internal agreement",
    body: "Flags where the manuscript's stated objectives, methods, and conclusions do not line up with each other.",
  },
  {
    Icon: QuoteMarksIcon,
    title: "Citation integrity",
    body: "Checks that citations exist, match their references, and support the claims they are attached to.",
  },
  {
    Icon: BarChartIcon,
    title: "Statistical forensics",
    body: "Checks reported numbers and statistics for inconsistencies, using established statistical methods.",
  },
  {
    Icon: LayersIcon,
    title: "Originality and reuse",
    body: "Compares against manuscripts VERIDICAL has already processed for your program.",
  },
];

const STEPS = [
  {
    lead: "Upload your required format.",
    rest: "Any document works. VERIDICAL parses it into criteria for you to review and confirm.",
  },
  {
    lead: "Upload a manuscript.",
    rest: "VERIDICAL checks it against your rubric and runs the four integrity checks above.",
  },
  {
    lead: "Review the readiness report.",
    rest: "Every flag shows its evidence. Confirm, override, or annotate anything before you decide.",
  },
];

const STATUSES = [
  {
    Icon: CheckCircleIcon,
    title: "Ready",
    body: "No blocking flags. The manuscript can be scheduled for defense.",
  },
  {
    Icon: AlertTriangleIcon,
    title: "Conditionally ready",
    body: "Fixable issues found. Ready once addressed.",
  },
  {
    Icon: CrossCircleIcon,
    title: "Not ready",
    body: "Blocking issues found, such as missing sections or high-severity flags.",
  },
];

// Staged reveal: nothing visible for the first 400ms (avoids a loading
// flash on the common warm-cache/fast-response case), a spinner after
// that, and the free-tier cold-start message (FEATURES.md §9) after 5s if
// the request is genuinely still in flight.
function LandingPending() {
  const [stage, setStage] = useState<"hidden" | "spinner" | "cold-start">("hidden");

  useEffect(() => {
    const toSpinner = setTimeout(() => setStage("spinner"), 400);
    const toColdStart = setTimeout(() => setStage("cold-start"), 5000);
    return () => {
      clearTimeout(toSpinner);
      clearTimeout(toColdStart);
    };
  }, []);

  const liveText =
    stage === "cold-start"
      ? "Waking up the server. This can take up to a minute on the free tier."
      : "Checking your sign-in status.";

  return (
    <div className="flex min-h-screen flex-col bg-page">
      <p role="status" aria-live="polite" aria-busy="true" className="sr-only">
        {liveText}
      </p>
      <header className="border-b-[3px] border-accent bg-action">
        <div className="mx-auto flex h-14 max-w-[1120px] items-center px-4 sm:h-16 sm:px-6 lg:px-10">
          <div className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="flex h-7 w-7 items-center justify-center rounded-sm bg-accent text-sm font-bold text-ink sm:h-8 sm:w-8"
            >
              V
            </span>
            <span className="text-base font-bold tracking-header text-on-action sm:text-md">
              VERIDICAL
            </span>
          </div>
        </div>
      </header>
      {stage !== "hidden" && (
        <div aria-hidden="true" className="flex flex-1 flex-col items-center justify-center gap-3 px-4 text-center">
          <span className="h-8 w-8 animate-spin rounded-full border-4 border-neutral-200 border-t-neutral-900 motion-reduce:animate-none" />
          <p className="text-sm text-ink-secondary">
            {stage === "cold-start"
              ? "Waking up the server. This can take up to a minute on the free tier."
              : "Loading VERIDICAL."}
          </p>
        </div>
      )}
    </div>
  );
}

export function LandingRoute() {
  const { data: me, isPending, isError } = useMe();

  if (isError) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-page px-4 text-center">
        <p role="status" aria-live="polite" aria-busy="false">
          VERIDICAL is not reachable right now.
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="flex h-11 items-center justify-center rounded-md border border-ink px-4 text-sm font-bold text-ink hover:bg-neutral-100"
        >
          Reload
        </button>
      </div>
    );
  }
  if (isPending) return <LandingPending />;
  if (me) return <Navigate to="/dashboard" replace />;
  return <LandingPage />;
}

function LandingPage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("VERIDICAL - Manuscript readiness checks", headingRef);

  return (
    <div className="flex min-h-screen flex-col bg-page">
      <a
        href="#main-content"
        className="sr-only focus-visible:not-sr-only focus-visible:absolute focus-visible:top-2 focus-visible:left-2 focus-visible:z-50 focus-visible:rounded-md focus-visible:bg-panel focus-visible:px-4 focus-visible:py-2 focus-visible:text-sm focus-visible:font-medium focus-visible:text-ink"
      >
        Skip to main content
      </a>

      <header className="border-b-[3px] border-accent bg-action">
        <div className="mx-auto flex h-14 max-w-[1120px] items-center justify-between px-4 sm:h-16 sm:px-6 lg:px-10">
          <div className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="flex h-7 w-7 items-center justify-center rounded-sm bg-accent text-sm font-bold text-ink sm:h-8 sm:w-8"
            >
              V
            </span>
            <span className="text-base font-bold tracking-header text-on-action sm:text-md">
              VERIDICAL
            </span>
          </div>
          <Link
            to="/signin"
            className="on-dark inline-flex h-11 items-center justify-center rounded-md border border-neutral-0 px-4 text-sm font-bold text-on-action hover:bg-neutral-0 hover:text-ink"
          >
            Sign in
          </Link>
        </div>
      </header>

      <main id="main-content" tabIndex={-1}>
        <div className="mx-auto max-w-[1120px] px-4 pt-12 pb-16 sm:px-6 sm:pt-16 sm:pb-20 lg:px-10 lg:pt-20">
          <h1
            ref={headingRef}
            tabIndex={-1}
            className="text-lg font-bold text-ink sm:text-2xl"
          >
            Check capstone manuscripts against your own rubric, with evidence for every flag.
          </h1>
          <p className="mt-4 max-w-[640px] text-base text-ink-secondary sm:text-md">
            VERIDICAL parses whatever format your program already uses, checks structure and
            content against it, and runs four integrity checks. Every result is something you
            can check yourself. You always make the final call.
          </p>
          <Link
            to="/signin"
            className="mt-6 flex h-12 w-full items-center justify-center rounded-md bg-action px-6 text-base font-bold text-on-action hover:bg-action-hover sm:inline-flex sm:w-auto sm:min-w-[200px]"
          >
            Sign in
          </Link>
          <p className="mt-2 text-sm text-ink-secondary">
            Accounts are created by your program administrator.
          </p>
        </div>

        <section aria-labelledby="checks-heading" className="border-t border-border py-12 sm:py-16">
          <div className="mx-auto max-w-[1120px] px-4 sm:px-6 lg:px-10">
            <h2 id="checks-heading" className="text-lg font-bold text-ink">
              What VERIDICAL checks
            </h2>
            <p className="mt-2 max-w-[640px] text-base text-ink-secondary">
              Beyond your rubric's own criteria, every manuscript goes through four integrity
              checks.
            </p>
            <div className="mt-8 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
              {CHECKS.map(({ Icon, title, body }) => (
                <article key={title} className="rounded-lg border border-border bg-panel p-6">
                  <Icon />
                  <h3 className="mt-3 text-base font-bold text-ink">{title}</h3>
                  <p className="mt-1 text-sm text-ink-secondary">{body}</p>
                </article>
              ))}
            </div>
            <p className="mt-6 text-sm text-ink-secondary">
              Every flag names a possible inconsistency, never an accusation, and shows the
              evidence behind it.
            </p>
          </div>
        </section>

        <section
          aria-labelledby="howitworks-heading"
          className="border-t border-border py-12 sm:py-16"
        >
          <div className="mx-auto max-w-[1120px] px-4 sm:px-6 lg:px-10">
            <h2 id="howitworks-heading" className="text-lg font-bold text-ink">
              How it works
            </h2>
            <ol className="mt-8 flex list-none flex-col gap-6 sm:flex-row sm:gap-8">
              {STEPS.map(({ lead, rest }, i) => (
                <li key={lead} className="flex flex-1 gap-4 sm:flex-col sm:gap-3">
                  <span
                    aria-hidden="true"
                    className="flex h-8 w-8 flex-none items-center justify-center rounded-full bg-action text-sm font-bold text-on-action"
                  >
                    {i + 1}
                  </span>
                  <p className="text-sm text-ink-secondary">
                    <span className="font-bold text-ink">{lead}</span> {rest}
                  </p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section
          aria-labelledby="statuses-heading"
          className="border-t border-border py-12 sm:py-16"
        >
          <div className="mx-auto max-w-[1120px] px-4 sm:px-6 lg:px-10">
            <h2 id="statuses-heading" className="text-lg font-bold text-ink">
              How readiness is reported
            </h2>
            <p className="mt-2 max-w-[640px] text-base text-ink-secondary">
              Every check produces one of three statuses. None of them are automatic pass or
              fail decisions. You always make the final call.
            </p>
            <div className="mt-8 grid grid-cols-1 gap-6 sm:grid-cols-3">
              {STATUSES.map(({ Icon, title, body }) => (
                <article key={title} className="rounded-lg border border-border bg-panel p-6">
                  <Icon />
                  <h3 className="mt-3 text-base font-bold text-ink">{title}</h3>
                  <p className="mt-1 text-sm text-ink-secondary">{body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="border-t-[3px] border-accent bg-action py-12 sm:py-16">
          <div className="mx-auto max-w-[1120px] px-4 text-center sm:px-6 lg:px-10">
            <h2 className="text-lg font-bold text-on-action">
              Ready to check your first manuscript?
            </h2>
            <Link
              to="/signin"
              className="on-dark mt-6 flex h-12 w-full items-center justify-center rounded-md bg-neutral-0 px-6 text-base font-bold text-ink hover:bg-neutral-100 sm:mx-auto sm:inline-flex sm:w-auto sm:min-w-[200px]"
            >
              Sign in
            </Link>
            <p className="mt-3 text-sm text-on-action">
              Accounts are created by your program administrator.
            </p>
          </div>
        </section>
      </main>

      <footer className="border-t border-border py-8">
        <div className="mx-auto max-w-[1120px] px-4 sm:px-6 lg:px-10">
          <p className="text-sm text-ink-secondary">
            VERIDICAL is a capstone project by BSIT students at Technological Institute of the
            Philippines, Manila.
          </p>
        </div>
      </footer>
    </div>
  );
}
