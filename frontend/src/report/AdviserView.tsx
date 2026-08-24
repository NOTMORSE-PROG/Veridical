// Screen 4l — the public, unauthenticated adviser view (F8.7, V-040).
// NOT behind AppShell: no nav, no login, a completely different context
// from the authenticated app -- the reader is an external faculty
// adviser who never touches the rest of VERIDICAL. Structural pattern
// (skip link -> header -> main -> footer) matches Landing.tsx, the
// app's only other pre-auth surface, but deliberately uses the calm
// "workspace" chrome (DESIGN.md §1.4) rather than the dark TIP bar --
// this reader isn't being sold on signing up, they're reading a dense
// report, same purpose as the instructor's own screen.
import { type ReactNode, useEffect, useRef } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "../api/client";
import { Chip } from "../components/Chip";
import { RubricNeedsReviewBanner } from "../components/RubricNeedsReviewBanner";
import { StatusPill } from "../components/StatusPill";
import { TestModeBanner } from "../components/TestModeBanner";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { READINESS_LABEL, READINESS_TONE } from "../domain/readinessTone";
import { DecisionSummary } from "./DecisionSummary";
import { FlagsList } from "./FlagsPanel";
import {
  NEEDS_REVIEW_OUTCOMES,
  ResultsTable,
  excludedFromScoreCount,
  explainer,
  pendingDisclosure,
} from "./ResultsTable";
import { useSharedReport } from "./useShare";

function SpinnerIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="motion-safe:animate-spin motion-reduce:animate-none">
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

// AC: "token unguessable... not indexed" -- the backend already sends
// X-Robots-Tag on the API response, but that header never reaches a
// client that renders the DOM (a headless-browser crawler, or any
// "agent that browses the page") rather than inspecting raw HTTP
// headers. This is a Vite SPA: every route serves the identical
// index.html, which carries no route-specific <meta name="robots">.
// Scoped to this one component's lifetime with its own cleanup -- left
// stray, a subsequent authenticated screen reached in the SAME TAB
// (browser Back after an instructor opens their own share link) would
// silently inherit a stray noindex, the same "effect not cleaned up on
// unmount" bug class this app's own hard rules already call out.
function useNoindexMeta() {
  useEffect(() => {
    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex, nofollow";
    document.head.appendChild(meta);
    return () => {
      document.head.removeChild(meta);
    };
  }, []);
}

function Chrome({ children, showReadOnlyBanner = true }: { children: ReactNode; showReadOnlyBanner?: boolean }) {
  return (
    <div className="flex min-h-screen flex-col bg-page">
      <a
        href="#main-content"
        className="sr-only focus-visible:not-sr-only focus-visible:absolute focus-visible:top-2 focus-visible:left-2 focus-visible:z-(--z-skip-link) focus-visible:rounded-md focus-visible:bg-panel focus-visible:px-4 focus-visible:py-2 focus-visible:text-sm focus-visible:font-medium focus-visible:text-ink"
      >
        Skip to main content
      </a>
      <header className="border-b border-border bg-panel">
        <div className="mx-auto flex h-14 max-w-[900px] items-center px-4 sm:h-16 sm:px-6">
          <span className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="flex h-7 w-7 items-center justify-center rounded-sm bg-action text-sm font-bold text-on-action"
            >
              V
            </span>
            <span className="text-base font-bold tracking-header text-ink sm:text-md">VERIDICAL</span>
          </span>
        </div>
      </header>
      {/* BUG-069 item 5: this used to render unconditionally, including on
          the "Link not found"/"Link no longer available" error states --
          claiming "you're viewing a report shared by the instructor" when
          there is, in fact, no report to view. */}
      {showReadOnlyBanner && (
        <p className="bg-status-info-bg px-4 py-2.5 text-center text-sm text-status-info-text">
          Read-only shared view. You're viewing a report shared by the instructor. No account is
          required, and nothing on this page can be edited.
        </p>
      )}
      <main id="main-content" tabIndex={-1} className="flex-1">
        <div className="mx-auto flex max-w-[900px] flex-col gap-4 p-4 sm:p-6">{children}</div>
      </main>
      <footer className="border-t border-border py-6">
        <div className="mx-auto flex max-w-[900px] flex-col gap-1.5 px-4 text-sm text-ink-tertiary sm:px-6">
          <p>
            VERIDICAL is a capstone project by BSIT students at Technological Institute of the
            Philippines, Manila, not an official T.I.P. system.
          </p>
          <p>
            This page always shows the report's current state, refreshed each time it loads, not a
            snapshot from when it was shared.
          </p>
          <p>This link isn't indexed by search engines. Treat it as private to whoever you send it to.</p>
        </div>
      </footer>
    </div>
  );
}

export function AdviserViewPage() {
  const { token } = useParams<{ token: string }>();
  const { data, isPending, isError, error } = useSharedReport(token ?? "");
  const headingRef = useRef<HTMLHeadingElement>(null);
  const errorRef = useRef<HTMLHeadingElement>(null);
  useNoindexMeta();

  useEffect(() => {
    document.title = "Readiness report (shared) - VERIDICAL";
  }, []);

  useEffect(() => {
    if (isError) errorRef.current?.focus();
  }, [isError]);

  if (isPending) {
    return (
      <Chrome>
        <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink sm:text-xl">
          Readiness report
        </h1>
        <div
          role="status"
          aria-live="polite"
          aria-busy="true"
          className="flex items-center gap-2 rounded-lg border border-border bg-panel p-6 text-sm text-ink-secondary"
        >
          <SpinnerIcon />
          Loading shared report.
        </div>
      </Chrome>
    );
  }

  if (isError || !data) {
    const isNotFound = error instanceof ApiError && error.code === "not_found";
    const isGone = error instanceof ApiError && error.code === "gone";
    const title = isNotFound ? "Link not found" : isGone ? "Link no longer available" : "Something went wrong";
    const message =
      error instanceof ApiError && (isNotFound || isGone)
        ? error.message
        : "This report couldn't be loaded right now.";
    return (
      <Chrome showReadOnlyBanner={false}>
        <h1
          ref={errorRef}
          tabIndex={-1}
          className="text-lg font-bold text-ink sm:text-xl"
        >
          {title}
        </h1>
        <div
          role="alert"
          className="rounded-lg border border-status-attention-text/25 bg-status-attention-bg p-4 text-sm text-status-attention-text"
        >
          <span>{message}</span>
          {isNotFound || isGone ? (
            <> If you believe this is a mistake, contact the instructor who sent you this link.</>
          ) : (
            <>
              {" "}
              <button
                type="button"
                onClick={() => window.location.reload()}
                className="font-medium underline"
              >
                Reload
              </button>
              .
            </>
          )}
        </div>
      </Chrome>
    );
  }

  const { report, flags } = data;
  const identity = manuscriptIdentity(report.manuscript_group_label, report.manuscript_original_filename);
  // ui-designer finding (P1): unlike Report.tsx (which has a separate
  // EscalatedPanel for these), the adviser view has nowhere else to put
  // a pending criterion -- the FULL, unfiltered array goes into the
  // table, and resultDisplay()'s "Awaiting review" cases carry that
  // state inline instead. Filtering these out here, the way Report.tsx
  // does, would silently show an adviser FEWER rows than really exist.
  const pendingCount = report.results.filter((r) => NEEDS_REVIEW_OUTCOMES.has(r.outcome)).length;

  return (
    <Chrome>
      <div>
        <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink sm:text-xl">
          Readiness report
        </h1>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <Chip maxWidthClass="max-w-[240px]" title={identity.primary}>
            {identity.primary}
          </Chip>
          <Chip maxWidthClass="max-w-[240px]" title={report.rubric_title}>
            {report.rubric_title}
          </Chip>
        </div>
      </div>

      <TestModeBanner llmMode={report.llm_mode} />
      <RubricNeedsReviewBanner
        needsReview={report.rubric_needs_review}
        issues={report.rubric_parse_issues}
      />

      <div className="flex flex-wrap items-center gap-3">
        <StatusPill tone={READINESS_TONE[report.status]}>{READINESS_LABEL[report.status]}</StatusPill>
        {report.composite_score !== null && (
          <span className="text-xl font-bold text-ink sm:text-2xl">{report.composite_score}%</span>
        )}
      </div>

      {/* BUG-044 (High): the "Previously X -> now Y" comparison line
          (V-041) used to render here off `report.previous_status`/
          `previous_composite_score` -- both removed from the public
          payload entirely, because that data describes a DIFFERENT
          check run the instructor never shared. Anyone holding this
          link learned a prior, unshared run's score. `PublicReportOut`
          doesn't carry the fields at all now, so this can't silently
          come back the way it silently appeared (V-041 added the fields
          to `ReportOut` for the instructor's own screen and this public
          view inherited them for free, since it was typed as `ReportOut`
          itself at the time). */}

      <p className="rounded-lg bg-status-info-bg px-4 py-2.5 text-sm text-status-info-text">
        {explainer(report, "#flags-heading")}
        {pendingDisclosure(pendingCount, excludedFromScoreCount(report.results))}
      </p>

      <FlagsList flags={flags} linkToDetail={false} />

      <ResultsTable
        results={report.results}
        totalCount={report.results.length}
        awaitingReviewMessage="Every criterion is still awaiting the instructor's review."
      />

      <DecisionSummary report={report} />

      <p className="text-sm text-ink-tertiary">
        <Link to="/" className="text-link underline hover:text-link-hover">
          VERIDICAL
        </Link>{" "}
        checks capstone manuscripts against an instructor's own rubric.
      </p>
    </Chrome>
  );
}
