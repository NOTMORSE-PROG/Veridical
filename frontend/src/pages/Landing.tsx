// Screen 4v — pre-auth landing (V-055, collapsed to an internal front door
// by V-067). `/` used to blind-redirect straight to /signin with zero
// content ever painted (Nielsen "visibility of system status", Jakob's
// Law, and the trust-first domain lens all named this a real gap —
// context/RESEARCH.md §14/§20). LandingRoute fast-paths an
// already-authenticated visitor straight to /dashboard without ever
// rendering this page; only a confirmed-anonymous visitor sees it.
//
// V-067 (2026-08-18): every visitor is pre-authorized by an administrator
// before they ever reach this page, so the marketing-shaped hero/stat-chips/
// feature-cards/conversion-section content this used to carry had no one
// left to persuade. Rebuilt as a GOV.UK-style start page (ui-designer spec,
// cited in the ticket): name the product, say who it's for, one sign-in
// action, nothing that argues for itself.
import { useEffect, useRef, useState } from "react";
import { Link, Navigate } from "react-router";
import { useMe } from "../auth/useAuth";
import { useRouteFocus } from "../routing/useRouteFocus";

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
      <header className="border-b-[3px] border-accent bg-tip-chrome">
        <div className="mx-auto flex h-14 max-w-[1120px] items-center px-4 sm:h-16 sm:px-6 lg:px-10">
          <div className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="flex h-7 w-7 items-center justify-center rounded-sm bg-accent text-sm font-bold text-on-tip-yellow sm:h-8 sm:w-8"
            >
              V
            </span>
            <span className="text-base font-bold tracking-header text-on-tip-chrome sm:text-md">
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
  useRouteFocus("VERIDICAL", headingRef);

  return (
    <div className="flex min-h-screen flex-col bg-page">
      <a
        href="#main-content"
        className="sr-only focus-visible:not-sr-only focus-visible:absolute focus-visible:top-2 focus-visible:left-2 focus-visible:z-(--z-skip-link) focus-visible:rounded-md focus-visible:bg-panel focus-visible:px-4 focus-visible:py-2 focus-visible:text-sm focus-visible:font-medium focus-visible:text-ink"
      >
        Skip to main content
      </a>

      {/*
       * V-067: the header used to also carry a "Sign in" link, making it
       * one of three identical sign-in targets on the page (Hick's law tax
       * with no benefit — an already-authorized visitor doesn't need a
       * persistent fallback two lines above the one real CTA). Header is
       * identity-only now; the single sign-in action lives in the content
       * block below.
       */}
      <header className="border-b-[3px] border-accent bg-tip-chrome">
        <div className="mx-auto flex h-14 max-w-[1120px] items-center px-4 sm:h-16 sm:px-6 lg:px-10">
          <Link to="/" className="on-dark flex items-center gap-2 rounded-sm">
            <span
              aria-hidden="true"
              className="flex h-7 w-7 items-center justify-center rounded-sm bg-accent text-sm font-bold text-on-tip-yellow sm:h-8 sm:w-8"
            >
              V
            </span>
            <span className="text-base font-bold tracking-header text-on-tip-chrome sm:text-md">
              VERIDICAL
            </span>
          </Link>
        </div>
      </header>

      {/*
       * V-067 (ui-designer spec, 2026-08-18): a GOV.UK-style start page —
       * name the product, say who it's for, one button, nothing that
       * argues for itself. Every visitor here is already pre-authorized by
       * an administrator, so the marketing-shaped hero/stat-chips/feature-
       * cards/conversion-section content this page used to carry (V-055,
       * V-056) had no one left to persuade. That also resolves Track E
       * P3-1 (the stat chips' sr-only/visible text duplication) and P2-1
       * (the "3 readiness tiers" claim contradicting the dashboard's real
       * 4-tile KPI row) as a side effect: nothing on this page makes a
       * tier-count claim anymore.
       */}
      <main
        id="main-content"
        tabIndex={-1}
        className="flex flex-1 flex-col items-center justify-center px-4 py-16 sm:px-6"
      >
        <div className="w-full max-w-[480px] text-center">
          <div className="flex flex-col items-center gap-2">
            <span
              aria-hidden="true"
              className="flex h-10 w-10 items-center justify-center rounded-sm bg-accent text-md font-bold text-on-tip-yellow"
            >
              V
            </span>
            <span className="text-lg font-bold tracking-header text-ink">VERIDICAL</span>
          </div>

          <h1 ref={headingRef} tabIndex={-1} className="mt-6 text-lg font-bold text-ink sm:text-2xl">
            Manuscript readiness checks for capstone instructors.
          </h1>

          <Link
            to="/signin"
            className="mt-6 flex h-12 w-full items-center justify-center rounded-md bg-action px-6 text-base font-bold text-on-action hover:bg-action-hover sm:mx-auto sm:inline-flex sm:w-auto sm:min-w-[200px]"
          >
            Sign in
          </Link>

          <p className="mt-3 text-sm text-ink-secondary">
            Accounts are created by your program administrator.
          </p>
          <p className="mt-2 text-sm text-ink-tertiary">
            A student capstone project at T.I.P. Manila, not an official T.I.P. system.
          </p>
        </div>
      </main>

      <footer className="border-t border-border py-8">
        <div className="mx-auto flex max-w-[1120px] items-center gap-3 px-4 sm:px-6 lg:px-10">
          {/*
           * TIP mark, approved for use by the owner (2026-08-11) with this
           * exact framing requirement: identify VERIDICAL as a student
           * project AT T.I.P., never as an official TIP system. Asset
           * fetched live from tip.edu.ph/assets/Uploads/
           * TIP-INFORMAL-LOGO-04-2.png (2026-08-11) and stored locally —
           * never hotlinked, their Cloudflare 403s non-browser clients.
           */}
          <img
            src="/tip-logo.png"
            alt="Technological Institute of the Philippines logo"
            className="h-10 w-auto flex-none"
            width={202}
            height={140}
          />
          <p className="text-sm text-ink-secondary">
            VERIDICAL is a capstone project by BSIT students at Technological Institute of the
            Philippines, Manila, not an official T.I.P. system.
          </p>
        </div>
      </footer>
    </div>
  );
}
