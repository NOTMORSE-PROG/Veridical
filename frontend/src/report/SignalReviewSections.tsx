import { reviewDeskDestination } from "../routing/reviewDeskReturn";
import { useEffect, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router";
import { CITATION_HISTORY_TITLE } from "../domain/citationHistory";
import type {
  EscalatedItemOut,
  EscalationResolution,
  FlagSummaryOut,
  OriginalityScope as OriginalityScopeValue,
  ResultRowCommon,
} from "../api/types";
import { useMe } from "../auth/useAuth";
import {
  FLAG_LIST_BATCH_COUNT,
  FLAG_LIST_INITIAL_COUNT,
  RESOLUTION_REASON_MIN_LENGTH,
} from "../config/ui";
import { isManuscriptQuote } from "../domain/evidenceDisplay";
import { CRITERION_REVIEW_COPY, REVIEW_DRAFT_COPY, criterionReviewNotices } from "../domain/criterionReview";
import { useViewedFlagIds } from "../domain/flagViewed";
import { problemLabel } from "../domain/problemLabel";
import { instructorAnchor } from "../domain/sourceAnchor";
import { RECORDED_GRADE_BASIS } from "../domain/semanticSupport";
import { systemFindingCopy } from "../domain/systemFindingCopy";
import { rememberRouteReturnFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { OriginalityScope } from "./OriginalityScope";
import { originalityUnassessed } from "./originalityScopeState";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { clusterFlagFindings, type FlagFindingCluster } from "./flagClusters";
import { useEscalatedItems, useFlags } from "./useReport";
import { useCriterionReviewScope } from "./criterionReviewContext";
import { resultDisplay } from "./ResultsTable";

const OUTCOME_COPY: Record<ResultRowCommon["outcome"], { label: string; tone: string }> = {
  passed: { label: "Meets criterion", tone: "meets" },
  failed: { label: "Does not meet", tone: "does-not-meet" },
  escalated: { label: "Needs review", tone: "review" },
  not_applicable: { label: "Not applicable", tone: "neutral" },
  unverifiable: { label: "Unverifiable", tone: "neutral" },
  api_down: { label: "Not assessed: service unavailable", tone: "warning" },
  quota_exhausted: { label: "Not assessed: capacity reached", tone: "warning" },
};

const SEVERITY_LABEL: Record<FlagSummaryOut["severity"], string> = {
  high: "High severity",
  med: "Medium severity",
  low: "Low severity",
};

type FlagView = "open" | "high" | "med" | "low" | "resolved" | "all";

// BUG-167: a module-level constant so the default prop value doesn't
// allocate a new Set every render for the common case (SignalPublicFlags,
// which never tracks viewed state at all).
const EMPTY_VIEWED: ReadonlySet<number> = new Set();

const FLAG_VIEWS: ReadonlyArray<{ id: FlagView; label: string }> = [
  { id: "open", label: "Open" },
  { id: "high", label: "High" },
  { id: "med", label: "Medium" },
  { id: "low", label: "Low" },
  { id: "resolved", label: "Resolved" },
  { id: "all", label: "All" },
];

function flagMatchesView(flag: FlagSummaryOut, view: FlagView): boolean {
  if (view === "all") return true;
  if (view === "resolved") return flag.overridden;
  if (view === "open") return !flag.overridden;
  return !flag.overridden && flag.severity === view;
}

const CLUSTERS_OPEN_PARAM = "flags_clusters_open";
const FLAG_VIEW_PARAM = "flags_view";
// BUG-167: "Show N more" used to be plain component state, so returning
// from a flag's own detail page (either via browser Back or an in-app
// link) always reset it -- silently discarding however many times the
// instructor had already expanded the list, on top of whatever scroll/
// focus problem also applied. Persisted the same way `view`/
// `openClusters` already are, so a genuine remount (this IS one: leaving
// `/report/:id` for `/flags/:id` unmounts this whole list) restores it.
const VISIBLE_COUNT_PARAM = "flags_visible";

function readFlagView(searchParams: URLSearchParams, fallback: FlagView): FlagView {
  const candidate = searchParams.get(FLAG_VIEW_PARAM);
  return FLAG_VIEWS.some((item) => item.id === candidate) ? candidate as FlagView : fallback;
}

function clustersForView(
  clusters: FlagFindingCluster[],
  view: FlagView,
): FlagFindingCluster[] {
  return clusters.flatMap((cluster) => {
    const matching = cluster.flags.filter((flag) => flagMatchesView(flag, view));
    return matching.length ? [{ key: cluster.key, flags: matching }] : [];
  });
}

function worstSeverity(flags: FlagSummaryOut[]): FlagSummaryOut["severity"] {
  const rank: Record<FlagSummaryOut["severity"], number> = { high: 3, med: 2, low: 1 };
  return flags.reduce(
    (worst, flag) => rank[flag.severity] > rank[worst] ? flag.severity : worst,
    flags[0].severity,
  );
}

// BUG-167: the list's DECLARATION order stays fixed check-kind-first
// (BUG-033's own deliberate "flags never jump around" precedent) -- what
// changes is which FILTER is selected by default, so what forces the
// verdict doesn't require 11+ screens of scrolling past lower-severity
// findings to reach. Keyed off `unresolvedHighFlagCount` (the same
// aggregate `report/scoring.py`'s `high_flag_count > 0 => not_ready` gate
// itself used), not recomputed from the raw flags array here -- that
// backend value is authoritative for "does an unresolved high exist,"
// and `FlagSummaryOut` doesn't expose the same has_verdict filter the
// scoring engine applies, so recomputing client-side could silently
// disagree with it in an edge case.
function defaultFlagView(unresolvedHighFlagCount: number, flags: FlagSummaryOut[]): FlagView {
  if (unresolvedHighFlagCount > 0) return "high";
  if (flags.some((flag) => !flag.overridden && flag.severity === "med")) return "med";
  return "open";
}

function readOpenClusters(searchParams: URLSearchParams): Set<string> {
  const raw = searchParams.get(CLUSTERS_OPEN_PARAM);
  return raw ? new Set(raw.split(",")) : new Set();
}

function readVisibleCount(searchParams: URLSearchParams, fallback: number): number {
  const raw = searchParams.get(VISIBLE_COUNT_PARAM);
  const parsed = raw ? Number(raw) : NaN;
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

// BUG-167: a bespoke inline SVG, matching `SeverityTag.tsx`'s existing
// convention for this codebase's small status icons.
function ViewedIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="14" height="14">
      <path
        d="M4 12.5 L10 18 L20 6"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// BUG-167: only ever compares against UNRESOLVED locations -- an
// overridden flag already shows "Resolved by instructor"/"Source
// confirmed", which strictly supersedes "was this looked at" (a flag
// cannot be overridden without having been opened first), so Viewed and
// the resolution-state pills are mutually exclusive by construction,
// never competing for attention on the same row.
function viewedSummary(flags: FlagSummaryOut[], viewedIds: ReadonlySet<number>): string | null {
  const unresolved = flags.filter((flag) => !flag.overridden);
  const viewed = unresolved.filter((flag) => viewedIds.has(flag.id));
  if (unresolved.length === 0 || viewed.length === 0) return null;
  if (unresolved.length === 1) return "Viewed this location";
  if (viewed.length === unresolved.length) return `Viewed all ${unresolved.length} open locations`;
  return `Viewed ${viewed.length} of ${unresolved.length} open locations`;
}

function SignalFindingCard({
  cluster,
  expanded,
  showActions,
  showResolutionSummary,
  viewedIds,
  onToggle,
}: {
  cluster: FlagFindingCluster;
  expanded: boolean;
  showActions: boolean;
  showResolutionSummary: boolean;
  viewedIds: ReadonlySet<number>;
  onToggle: () => void;
}) {
  const { flags } = cluster;
  // BUG-167: `location.search` (not just `.pathname`) is part of the
  // return path -- this list's own filter/expanded/pagination state lives
  // in query params, and `FlagDetail.tsx`'s breadcrumb navigates back to
  // this EXACT string, so returning by breadcrumb click restores the same
  // view an instructor left, not a bare, stripped-down `/report/:id`.
  const location = useLocation();
  const first = flags[0];
  const severity = worstSeverity(flags);
  const resolvedCount = flags.filter((flag) => flag.overridden).length;
  const confirmedCount = flags.filter((flag) => flag.confirmed_citation_source).length;
  const allResolved = resolvedCount === flags.length;
  const headingId = `signal-flag-finding-${cluster.key}`;
  const locationsId = `signal-flag-locations-${cluster.key}`;
  const excerptsMatch = new Set(flags.map((flag) => flag.evidence_excerpt.trim())).size === 1;
  const mixedSeverity = new Set(flags.map((flag) => flag.severity)).size > 1;

  if (flags.length === 1) {
    return (
      <li className={first.overridden ? "signal-flag-row signal-flag-row--resolved" : "signal-flag-row"}>
        <div className="signal-flag-row__summary">
          <span className={`signal-severity signal-severity--${first.affects_readiness === false ? "low" : first.severity}`}>{first.affects_readiness === false ? "Review lead: not scored" : SEVERITY_LABEL[first.severity]}</span>
          {!first.overridden && viewedIds.has(first.id) && (
            <span className="signal-viewed-state"><ViewedIcon />Viewed</span>
          )}
          {first.overridden && <span className="signal-resolution-state">Resolved by instructor</span>}
          {first.confirmed_citation_source && <span className="signal-resolution-state">Source confirmed</span>}
        </div>
        <h3 id={headingId} tabIndex={-1}>{problemLabel(first.problem_kind) ?? first.criterion_text ?? "Possible inconsistency"}</h3>
      {flags.some((flag) => flag.citation_metadata_review_warning) && <p className="signal-field-hint"><strong>{CITATION_HISTORY_TITLE}</strong></p>}
        {/* BUG-170: a whole-document/chapter/resubmission reuse flag's
            evidence_excerpt is a system-authored template sentence, never
            manuscript text -- <blockquote> claims a quotation that isn't
            one. No separate "reasoning" field exists at this summary
            level, so the same text still renders, just not quote-styled. */}
        {isManuscriptQuote(first)
          ? <blockquote>“{systemFindingCopy(first.evidence_excerpt, first.problem_kind, "evidence")}”</blockquote>
          : <p className="signal-flag-finding-context">{systemFindingCopy(first.evidence_excerpt, first.problem_kind, "evidence")}</p>}
        <p>{instructorAnchor(first.page_anchor)}</p>
        {first.first_upload_context && <p className="signal-field-hint">First-upload context: the comparison archive was limited when this signal was created.</p>}
        {showActions && (
          <ActionLink
            id={`signal-flag-review-${first.id}`}
            to={`/flags/${first.id}`}
            variant="secondary"
            state={{ routeReturnFocus: { returnPath: location.pathname + location.search, elementId: `signal-flag-review-${first.id}` } }}
          >
            Review evidence
          </ActionLink>
        )}
      </li>
    );
  }

  return (
    <li className={allResolved ? "signal-flag-row signal-flag-row--resolved" : "signal-flag-row"}>
      <div className="signal-flag-row__summary">
        <span className={`signal-severity signal-severity--${first.affects_readiness === false ? "low" : severity}`}>{first.affects_readiness === false ? "Review lead: not scored" : SEVERITY_LABEL[severity]}</span>
        {viewedSummary(flags, viewedIds) && (
          <span className="signal-viewed-state"><ViewedIcon />{viewedSummary(flags, viewedIds)}</span>
        )}
        {showResolutionSummary && resolvedCount > 0 && (
          <span className="signal-resolution-state">
            {allResolved
              ? `All ${flags.length} locations resolved by instructor`
              : `${resolvedCount} of ${flags.length} locations resolved by instructor`}
          </span>
        )}
        {confirmedCount > 0 && <span className="signal-resolution-state">{confirmedCount} source-confirmed location{confirmedCount === 1 ? "" : "s"}</span>}
      </div>
      <h3 id={headingId} tabIndex={-1}>{problemLabel(first.problem_kind) ?? first.criterion_text ?? "Possible inconsistency"}</h3>
      {flags.some((flag) => flag.citation_metadata_review_warning) && <p className="signal-field-hint"><strong>{CITATION_HISTORY_TITLE}</strong></p>}
      {first.check_kind === "originality_reuse" && first.matched_ref != null && (
        <p className="signal-flag-finding-context">Possible match with archived manuscript #{first.matched_ref}</p>
      )}
      {excerptsMatch
        ? isManuscriptQuote(first)
          ? <blockquote>“{systemFindingCopy(first.evidence_excerpt, first.problem_kind, "evidence")}”</blockquote>
          : <p className="signal-flag-finding-context">{systemFindingCopy(first.evidence_excerpt, first.problem_kind, "evidence")}</p>
        : <p className="signal-flag-finding-context">{flags.length} manuscript locations point to this possible finding, each with its own excerpt. Review one location below, or open all {flags.length} to compare them.</p>}
      {first.first_upload_context && <p className="signal-field-hint">First-upload context: the comparison archive was limited when this signal was created.</p>}
      <div className="signal-flag-row__actions">
        {/* BUG-169: a multi-location cluster used to hide EVERY evidence
            link behind the disclosure below -- fixed for all severities,
            not just high, since the asymmetry is a location-count problem,
            not a severity one. "-summary-" in the id avoids colliding with
            flags[0]'s own per-location link once this cluster expands.
            Deliberately says "one of N", never "first" -- `first` is just
            array-position `flags[0]`, and the API re-sorts unresolved
            before overridden, so which flag sits at index 0 can change
            after a sibling gets resolved (UX review, live-reproduced:
            reversing a 2-flag cluster's array order moved this link's
            target and anchor text with it). Claiming a stable ordinal an
            instructor could reasonably rely on ("I already checked the
            first one") when the underlying order isn't stable would be
            exactly the false precision ground rule 8 forbids for numbers,
            applied to a word instead. */}
        {showActions && (
          <ActionLink
            id={`signal-flag-review-summary-${first.id}`}
            to={`/flags/${first.id}`}
            variant="secondary"
            // Deliberately NOT the same template the per-location links
            // use ("Review evidence at {anchor}, location {i} of {N}") --
            // once this cluster expands, flags[0]'s own per-location link
            // gets exactly that string, and two links pointing at the
            // same flag with the IDENTICAL accessible name is real,
            // avoidable ambiguity for anyone navigating by link name.
            aria-label={`Review evidence at ${instructorAnchor(first.page_anchor)} (one of ${flags.length} locations)`}
            state={{
              routeReturnFocus: {
                returnPath: location.pathname + location.search,
                elementId: `signal-flag-review-summary-${first.id}`,
              },
            }}
          >
            Review evidence
          </ActionLink>
        )}
        <Button
          variant="quiet"
          aria-expanded={expanded}
          aria-controls={locationsId}
          onClick={onToggle}
        >
          {expanded ? "Hide" : "Show"} {flags.length} locations
        </Button>
      </div>
      {expanded && (
        <ol id={locationsId} className="signal-flag-location-list">
          {flags.map((flag, locationIndex) => (
            <li key={flag.id} className={flag.overridden ? "signal-flag-location signal-flag-location--resolved" : "signal-flag-location"}>
              {isManuscriptQuote(flag)
                ? <blockquote>“{systemFindingCopy(flag.evidence_excerpt, flag.problem_kind, "evidence")}”</blockquote>
                : <p className="signal-flag-finding-context">{systemFindingCopy(flag.evidence_excerpt, flag.problem_kind, "evidence")}</p>}
              <div className="signal-flag-location__meta">
                <strong>{instructorAnchor(flag.page_anchor)}</strong>
                {mixedSeverity && <span className={`signal-severity signal-severity--${flag.severity}`}>{SEVERITY_LABEL[flag.severity]}</span>}
                {!flag.overridden && viewedIds.has(flag.id) && (
                  <span className="signal-viewed-state"><ViewedIcon />Viewed</span>
                )}
                {flag.overridden && <span className="signal-resolution-state">Resolved by instructor</span>}
                {flag.confirmed_citation_source && <span className="signal-resolution-state">Source confirmed</span>}
              </div>
              {showActions && (
                <ActionLink
                  id={`signal-flag-review-${flag.id}`}
                  to={`/flags/${flag.id}`}
                  variant="secondary"
                  aria-label={`Review evidence at ${instructorAnchor(flag.page_anchor)}, location ${locationIndex + 1} of ${flags.length}`}
                  state={{ routeReturnFocus: { returnPath: location.pathname + location.search, elementId: `signal-flag-review-${flag.id}` } }}
                >
                  Review evidence
                </ActionLink>
              )}
            </li>
          ))}
        </ol>
      )}
    </li>
  );
}

function SignalFlagList({
  flags,
  showActions,
  initialView,
  trackViewed = false,
  viewedIds = EMPTY_VIEWED,
}: {
  flags: FlagSummaryOut[];
  showActions: boolean;
  initialView: FlagView;
  // BUG-167: both optional, both default to the no-op/off state, so
  // `SignalPublicFlags` (the adviser/share view) needs zero changes --
  // "opened" tracking is an instructor-only convenience, never shown to
  // an external reader.
  trackViewed?: boolean;
  viewedIds?: ReadonlySet<number>;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  // BUG-167 (design review finding, live-reproduced before it could break
  // the hero-KPI link below): a `useState` mirror initialized ONCE from
  // `searchParams` goes stale the moment the URL changes any other way
  // (e.g. the hero KPI link setting `flags_view=high` directly) -- the
  // pressed filter button kept showing the PREVIOUS view while the URL
  // already said otherwise. `openClusters` two lines down never had this
  // bug because it was already derived fresh from `searchParams` on every
  // render, no local-state mirror -- `view`/`visibleCount` now match it.
  const view = readFlagView(searchParams, initialView);
  const visibleCount = readVisibleCount(searchParams, FLAG_LIST_INITIAL_COUNT);
  const [focusClusterKey, setFocusClusterKey] = useState<string>();
  const canonicalClusters = clusterFlagFindings(flags);
  const filtered = clustersForView(canonicalClusters, view);
  const visible = filtered.slice(0, visibleCount);
  const remaining = Math.max(0, filtered.length - visible.length);
  const locationCount = filtered.reduce((total, cluster) => total + cluster.flags.length, 0);
  const openClusters = readOpenClusters(searchParams);
  const viewLabel = FLAG_VIEWS.find((item) => item.id === view)?.label.toLocaleLowerCase() ?? "selected";

  useEffect(() => {
    if (!focusClusterKey) return;
    document.getElementById(`signal-flag-finding-${focusClusterKey}`)?.focus();
    setFocusClusterKey(undefined);
  }, [focusClusterKey, visibleCount]);

  function selectView(next: FlagView) {
    const nextParams = new URLSearchParams(searchParams);
    if (next === initialView) nextParams.delete(FLAG_VIEW_PARAM);
    else nextParams.set(FLAG_VIEW_PARAM, next);
    nextParams.delete(VISIBLE_COUNT_PARAM);
    setSearchParams(nextParams, { replace: true });
  }

  function toggleCluster(key: string) {
    const nextOpen = readOpenClusters(searchParams);
    if (nextOpen.has(key)) nextOpen.delete(key);
    else nextOpen.add(key);
    const next = new URLSearchParams(searchParams);
    if (nextOpen.size) next.set(CLUSTERS_OPEN_PARAM, [...nextOpen].join(","));
    else next.delete(CLUSTERS_OPEN_PARAM);
    setSearchParams(next, { replace: true });
  }

  function showMore() {
    const firstNew = filtered[visibleCount];
    const nextCount = visibleCount + FLAG_LIST_BATCH_COUNT;
    setFocusClusterKey(firstNew?.key);
    const nextParams = new URLSearchParams(searchParams);
    if (nextCount === FLAG_LIST_INITIAL_COUNT) nextParams.delete(VISIBLE_COUNT_PARAM);
    else nextParams.set(VISIBLE_COUNT_PARAM, String(nextCount));
    setSearchParams(nextParams, { replace: true });
  }

  return (
    <>
      <div className="signal-flag-tools">
        <div role="group" aria-label="Filter integrity signals" className="signal-flag-filters">
          {FLAG_VIEWS.map((item) => {
            const count = clustersForView(canonicalClusters, item.id).length;
            return (
              <button
                key={item.id}
                type="button"
                aria-label={`${item.label}: ${count} finding${count === 1 ? "" : "s"}`}
                aria-pressed={view === item.id}
                onClick={() => selectView(item.id)}
              >
                <span>{item.label}</span>
                <small>{count}</small>
              </button>
            );
          })}
        </div>
        {view === initialView && initialView === "high" && (
          <p className="signal-field-hint">
            High severity is shown first. Filters use recorded severity, including review leads that do not change readiness. The readiness band also reflects criterion results and incomplete checks. Select Open or All to see every signal.
          </p>
        )}
        {view === initialView && initialView === "med" && (
          <p className="signal-field-hint">
            Medium severity is shown first. Filters use recorded severity, including review leads that do not change readiness. The readiness band also reflects criterion results and incomplete checks. Select Open or All to see every signal.
          </p>
        )}
        <p role="status" aria-live="polite">
          Showing {visible.length} of {filtered.length} {viewLabel} finding{filtered.length === 1 ? "" : "s"} across {locationCount} location{locationCount === 1 ? "" : "s"}.
        </p>
        {trackViewed && filtered.length > 0 && (() => {
          const openClustersAll = clusterFlagFindings(flags.filter((flag) => !flag.overridden));
          const openTotal = openClustersAll.length;
          const openViewed = openClustersAll.filter((cluster) =>
            cluster.flags.some((flag) => viewedIds.has(flag.id))
          ).length;
          return openTotal > 0 ? (
            // UX review finding (BUG-167 review): another tab marking a
            // flag viewed updates this count via `useViewedFlagIds`'s own
            // cross-tab `storage` listener with nothing announced to a
            // screen reader in THIS tab -- same `role`/`aria-live` as its
            // sibling status line above.
            <p className="signal-field-hint" role="status" aria-live="polite">
              You have reviewed evidence for {openViewed} of {openTotal} open findings.
            </p>
          ) : null;
        })()}
        <p className="signal-field-hint">
          One finding can contain both open and resolved locations, so those two filter counts may overlap. All counts each finding once.
        </p>
      </div>

      {visible.length ? (
        <ul className="signal-flag-list">
          {visible.map((cluster) => (
            <SignalFindingCard
              key={cluster.key}
              cluster={cluster}
              expanded={openClusters.has(cluster.key)}
              showActions={showActions}
              showResolutionSummary={view === "all"}
              viewedIds={viewedIds}
              onToggle={() => toggleCluster(cluster.key)}
            />
          ))}
        </ul>
      ) : (
        <Alert title={`No ${viewLabel} signals`} tone="info">Choose another filter to review the remaining integrity record.</Alert>
      )}

      {remaining > 0 && (
        <div className="signal-flag-more">
          <Button variant="secondary" onClick={showMore}>
            Show {Math.min(FLAG_LIST_BATCH_COUNT, remaining)} more
          </Button>
        </div>
      )}
    </>
  );
}

function resolutionLabel(item: EscalatedItemOut, resolution: EscalationResolution, level?: number): string {
  if (resolution === "accept_majority") return `Accept AI suggestion: ${item.ai_majority_verdict}`;
  if (resolution === "mark_pass") return "Mark as meets criterion";
  if (resolution === "mark_fail") return "Mark as does not meet";
  if (resolution === "needs_document") return "Exclude: needs another document";
  return item.levels?.find((entry) => entry.level === level)?.name ?? "Selected rubric level";
}

export type CriterionSourceTarget = (anchor: string, actionId: string) => string;
export type CriterionSourceOpen = (anchor: string, actionId: string) => void;

function SignalResolutionCard({ item, checkRunId, sharedReasonId, sourceTarget, onOpenSource }: {
  item: EscalatedItemOut;
  checkRunId: number;
  sharedReasonId?: string;
  sourceTarget?: CriterionSourceTarget;
  onOpenSource?: CriterionSourceOpen;
}) {
  const location = useLocation();
  const review = useCriterionReviewScope(checkRunId);
  const pending = review.drafts.get(item.check_result_id);
  const reason = pending?.reason ?? "";
  const attempted = pending?.attempted ?? false;
  const saving = pending?.saving ?? false;
  const [detailsOpen, setDetailsOpen] = useState(false);
  const reasonInvalid = attempted && reason.trim().length < RESOLUTION_REASON_MIN_LENGTH;
  const reasonErrorId = `signal-resolution-reason-${item.check_result_id}`;
  const criterionHeadingRef = useRef<HTMLHeadingElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const requestedReasonFocus = useRef(false);
  useEffect(() => {
    if (pending && requestedReasonFocus.current) {
      requestedReasonFocus.current = false;
      reasonRef.current?.focus();
    }
  }, [pending]);

  function choose(resolution: EscalationResolution, level?: number) {
    requestedReasonFocus.current = true;
    review.choose(item, resolution, level);
  }

  function cancel() {
    if (saving) return;
    review.cancel(item.check_result_id);
    // The persistent criterion heading keeps the explicit Cancel action
    // from dropping keyboard focus when the reason form disappears.
    criterionHeadingRef.current?.focus();
  }

  function confirm() {
    review.confirm(item.check_result_id, (out) => {
      const result = out.report.results.find((row) => row.criterion_id === item.criterion_id);
      const resultLabel = result?.level?.name
        ?? (out.outcome === "not_applicable" ? "excluded because another document is needed" : OUTCOME_COPY[out.outcome as ResultRowCommon["outcome"]]?.label ?? out.outcome);
      return `Criterion resolved as ${resultLabel}. The readiness band has been recalculated.`;
    });
  }

  // Direct keyboard navigation to a grading action must retain its
  // visible review limitations. Optional raw diagnostics are not its basis.
  const reviewStateId = `signal-resolution-state-${item.check_result_id}`;
  const detailsId = `signal-resolution-details-${item.check_result_id}`;
  const reasonId = `signal-resolution-context-reason-${item.check_result_id}`;
  const injectionId = `signal-resolution-injection-${item.check_result_id}`;
  const unverifiedId = `signal-resolution-unverified-${item.check_result_id}`;
  const qualificationId = `signal-resolution-basis-${item.check_result_id}`;
  const reviewNotices = criterionReviewNotices(item);
  const noticeId = (kind: string) => `signal-resolution-notice-${item.check_result_id}-${kind}`;
  const showInjectionSnippet = Boolean(
    (item.injection_suspected || item.review_reason === "injection_suspected")
    && item.injection_matched_snippet?.trim(),
  );
  const levelDescriptionId = (level: number) => `signal-resolution-level-${item.check_result_id}-${level}`;
  const selectedLevel = pending?.resolution === "mark_level"
    ? item.levels?.find((level) => level.level === pending.level) : undefined;
  const describedBy = [
    reviewStateId,
    ...reviewNotices.map((notice) => noticeId(notice.kind)),
    qualificationId,
    sharedReasonId || (item.reason && reasonId),
    showInjectionSnippet && injectionId,
    item.unverified_evidence && item.unverified_evidence.length > 0 && unverifiedId,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <article className="signal-resolution-card">
      <div className="signal-resolution-card__context">
        <div><p className="signal-section-kicker">Criterion requiring judgment</p><h3 id={`signal-resolution-heading-${item.check_result_id}`} ref={criterionHeadingRef} tabIndex={-1}>{item.criterion_text}</h3></div>
        {reviewNotices.map((notice) => <p key={notice.kind} id={noticeId(notice.kind)} className="signal-resolution-card__vote">{notice.text}</p>)}
        <div className="signal-copy-flow"><strong id={reviewStateId}>No grade accepted</strong><p>{RECORDED_GRADE_BASIS.review}</p><p id={qualificationId}>{RECORDED_GRADE_BASIS.qualification}</p></div>
        {sharedReasonId
          ? <p>Unresolved. The shared review reason above applies to this criterion.</p>
          : item.reason && <p id={reasonId}>{item.reason}</p>}
        {showInjectionSnippet && (
          <blockquote id={injectionId} className="signal-unverified-evidence"><strong>Text addressed at an automated grader</strong><span>“{item.injection_matched_snippet}”</span></blockquote>
        )}
        {item.unverified_evidence && item.unverified_evidence.length > 0 && (
          <div id={unverifiedId} className="signal-unverified-evidence"><strong>Could not verify against the source</strong>{item.unverified_evidence.map((quote) => <blockquote key={quote}>“{quote}”</blockquote>)}</div>
        )}
        {item.reasoning?.trim() ? <div className="signal-copy-flow"><strong>Recorded AI explanation</strong><p>{item.reasoning}</p></div> : <p>{RECORDED_GRADE_BASIS.missingExplanation}</p>}
        {Boolean(item.evidence?.length) && <strong>Recorded passages and locations</strong>}
        {item.evidence?.map((entry, index) => {
          const actionId = `signal-resolution-source-${checkRunId}-${item.check_result_id}-${index}`;
          const target = sourceTarget?.(entry.anchor, actionId) ?? reviewDeskDestination(`/report/${checkRunId}/document?anchor=${encodeURIComponent(entry.anchor)}`, location);
          return <div className="signal-attached-flow" key={`${entry.anchor}-${index}`}><blockquote>“{entry.quote}”</blockquote>{entry.anchor.trim() ? <><p>{instructorAnchor(entry.anchor)}</p><ActionLink id={actionId} variant="quiet" to={target} aria-label={`Open in manuscript: ${instructorAnchor(entry.anchor)}. ${item.criterion_text}`} onClick={(event) => {
            if (onOpenSource && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
              event.preventDefault();
              onOpenSource(entry.anchor, actionId);
            } else if (!onOpenSource) rememberRouteReturnFocus(location.pathname + location.search, target, actionId);
          }}>Open in manuscript: {instructorAnchor(entry.anchor)}</ActionLink></> : <p>{RECORDED_GRADE_BASIS.missingLocation}</p>}</div>;
        })}
        {!item.evidence?.length && <p>{RECORDED_GRADE_BASIS.missingExcerpt}</p>}
        <Button variant="quiet" aria-expanded={detailsOpen} aria-controls={detailsId}
          aria-label={`${detailsOpen ? "Hide" : "Show"} ${CRITERION_REVIEW_COPY.details} for ${item.criterion_text}`}
          onClick={() => setDetailsOpen((open) => !open)}>
          {detailsOpen ? "Hide" : "Show"} {CRITERION_REVIEW_COPY.details}
        </Button>
        <div id={detailsId} hidden={!detailsOpen}>
          <p><strong>{CRITERION_REVIEW_COPY.detailsTitle}</strong></p>
          <p>{CRITERION_REVIEW_COPY.detailsQualification}</p>
          {item.votes.length > 0 ? <ol aria-label={CRITERION_REVIEW_COPY.entriesLabel}>{item.votes.map((vote, index) =>
            <li key={index}><strong>Entry {index + 1}</strong><span>{vote ?? CRITERION_REVIEW_COPY.missingEntry}</span></li>,
          )}</ol> : <p>{CRITERION_REVIEW_COPY.noEntries}</p>}
        </div>
      </div>

      {/* Mount the status region before its content changes. Explicit choice
          focuses the reason field; restoring a draft must not steal focus. */}
      <div className="signal-resolution-form__pending" role="status" aria-live="polite">
        {pending && (
          <>
            <p className="signal-section-kicker">Step 2 of 2: nothing is saved yet</p>
            <h4>{resolutionLabel(item, pending.resolution, pending.level)}</h4>
          </>
        )}
      </div>

      {item.levels && item.levels.length > 0 && (
        <details className="signal-levels">
          <summary aria-label={`View level descriptions for ${item.criterion_text}`}>View level descriptions ({item.levels.length})</summary>
          <ol>{item.levels.map((level) => <li key={level.level}><strong>{level.name}</strong><span id={levelDescriptionId(level.level)}>{level.descriptor || "Description not recorded for this level."}</span></li>)}</ol>
        </details>
      )}
      {!pending ? (
        <div className="signal-resolution-options" aria-label={`Resolve: ${item.criterion_text}`}>
          {item.levels && item.levels.length > 0
            ? item.levels.map((level) => <Button key={level.level} variant="secondary" aria-describedby={`${describedBy} ${levelDescriptionId(level.level)}`} onClick={() => choose("mark_level", level.level)}>{level.name}</Button>)
            : <><Button variant="secondary" aria-describedby={describedBy} onClick={() => choose("mark_pass")}>Meets criterion</Button><Button variant="secondary" aria-describedby={describedBy} onClick={() => choose("mark_fail")}>Does not meet</Button></>}
          <Button variant="quiet" aria-describedby={describedBy} onClick={() => choose("needs_document")}>Needs another document</Button>
          {/* BUG-170: `verdict_unrecognized` means accepting this verdict
              is guaranteed to 409 server-side (the passes agreed on a
              string that still doesn't name any of this criterion's own
              levels) -- offering the button anyway would be the exact
              "offers to accept a verdict it just told you it cannot
              recognise" incoherence this ticket named. `item.reason`
              already explains why, so removing the button loses no
              information. */}
          {item.ai_majority_verdict !== null && !item.verdict_unrecognized && !reviewNotices.some((notice) => notice.kind === "support") && <Button variant={reviewNotices.some((notice) => notice.kind === "source_instructions") ? "quiet" : "secondary"} aria-describedby={describedBy} onClick={() => choose("accept_majority")}>Accept AI suggestion: {item.ai_majority_verdict}</Button>}
        </div>
      ) : (
        <div className="signal-resolution-form">
          {selectedLevel && <div className="signal-copy-flow"><p><strong>Selected level: {selectedLevel.name}</strong></p><p>{selectedLevel.descriptor || "Description not recorded for this level."}</p></div>}
          <label><span>Reason (required)</span><textarea ref={reasonRef} disabled={saving} rows={3} value={reason} aria-invalid={reasonInvalid || undefined} aria-describedby={reasonInvalid ? reasonErrorId : undefined} onChange={(event) => review.changeReason(item.check_result_id, event.target.value)} /></label>
          <p className="signal-field-hint">This reason appears in the instructor report and export.</p>
          {reasonInvalid && <p id={reasonErrorId} role="alert" className="signal-field-error">{reason.trim() ? `Enter at least ${RESOLUTION_REASON_MIN_LENGTH} characters.` : "Enter a reason before confirming."}</p>}
          {saving && <p role="status">{REVIEW_DRAFT_COPY.saving}</p>}
          {Boolean(pending.error) && <Alert title="Could not save this resolution" tone="error" role="alert">{pending.error instanceof Error ? pending.error.message : "Try again."}</Alert>}
          <div><Button variant="secondary" disabled={saving} onClick={cancel}>Cancel</Button><Button variant="brand" disabled={saving} aria-describedby={describedBy} busy={saving} onClick={confirm}>Confirm resolution</Button></div>
        </div>
      )}
    </article>
  );
}

export function SignalEscalatedPanel({ checkRunId, sourceTarget, onOpenSource }: { checkRunId: number; sourceTarget?: CriterionSourceTarget; onOpenSource?: CriterionSourceOpen }) {
  const { data: items, isPending, isError, refetch } = useEscalatedItems(checkRunId);
  const review = useCriterionReviewScope(checkRunId);
  const reviewRef = useRef(review);
  reviewRef.current = review;
  const [announcement, setAnnouncement] = useState("");
  const headingRef = useRef<HTMLHeadingElement>(null);
  const announcementRef = useRef<HTMLParagraphElement>(null);
  const hasItems = Boolean(items?.length);
  // Share only identical recorded reasons, never inferred causes or
  // item-specific evidence. A single item keeps its reason in its card.
  const sharedReason = items && items.length > 1 && items[0].reason?.trim()
    && items.every((item) => item.reason === items[0].reason)
    ? items[0].reason : null;
  const sharedReasonId = `signal-shared-review-reason-${checkRunId}`;

  useEffect(() => {
    if (items && !isPending && !isError) reviewRef.current.reconcile(items);
  }, [items, isPending, isError]);
  const completion = review.completion;
  useEffect(() => {
    if (!completion) return;
    setAnnouncement(completion.message);
    const origin = completion.focusFrom;
    if (origin && (document.activeElement === origin || (!origin.isConnected && document.activeElement === document.body))) {
      (hasItems ? headingRef.current : announcementRef.current)?.focus();
    } else if (!hasItems && document.activeElement === headingRef.current) announcementRef.current?.focus();
  }, [completion, hasItems]);

  if (isPending) return <div role="status" aria-busy="true" className="signal-desk-loading"><span>Loading criteria that need review…</span><i /><i /></div>;
  if (isError) return <Alert title="Could not load criteria needing review" tone="error" role="alert"><Button variant="secondary" onClick={() => refetch()}>Try again</Button></Alert>;

  return (
    <section id="review-criteria" tabIndex={-1} data-tour="escalated-panel" className="signal-review-section signal-review-section--attention" aria-labelledby="escalated-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">First review task</p><h2 id="escalated-heading" ref={headingRef} tabIndex={-1}>Criteria needing your judgment</h2></div><span>{items?.length ?? 0} unresolved</span></div>
      <p className="signal-review-section__intro">VERIDICAL did not settle these items. Review the evidence and record your own reasoned resolution; the final decision remains unavailable until they are resolved.</p>
      <p className="signal-field-hint">{REVIEW_DRAFT_COPY.retention}</p>
      <p ref={announcementRef} tabIndex={-1} aria-live="polite" className="signal-live-region">{announcement}</p>
      {sharedReason && <Alert title="Shared review reason" tone="warning"><p id={sharedReasonId}>{sharedReason}</p></Alert>}
      {hasItems
        ? <div className="signal-resolution-list">{items?.map((item) => <SignalResolutionCard key={`${checkRunId}-${item.check_result_id}`} item={item} checkRunId={checkRunId} sourceTarget={sourceTarget} onOpenSource={onOpenSource} sharedReasonId={sharedReason ? sharedReasonId : undefined} />)}</div>
        : <Alert title="No unresolved criteria" tone="success">Every criterion has a recorded outcome. Continue to the integrity signals.</Alert>}
    </section>
  );
}

export function SignalFlagsPanel({
  checkRunId,
  unresolvedHighFlagCount,
  originalityScope,
}: {
  checkRunId: number;
  unresolvedHighFlagCount: number;
  originalityScope?: OriginalityScopeValue | null;
}) {
  const { data: flags, isPending, isError, refetch } = useFlags(checkRunId);
  const { data: me } = useMe();
  const viewedIds = useViewedFlagIds(me?.id);
  if (isPending) return <div role="status" aria-busy="true" className="signal-desk-loading"><span>Loading integrity signals…</span><i /><i /></div>;
  if (isError) return <Alert title="Could not load integrity signals" tone="error" role="alert"><Button variant="secondary" onClick={() => refetch()}>Try again</Button></Alert>;
  const openFindingCount = clusterFlagFindings(flags?.filter((flag) => !flag.overridden) ?? []).length;
  // BUG-167: defaults the FILTER to the worst unresolved severity present,
  // not the declaration order -- see `defaultFlagView`'s own comment for
  // why this is a filter-default decision, not a reorder.
  const initialView = flags ? defaultFlagView(unresolvedHighFlagCount, flags) : "open";

  return (
    <section id="integrity-signals" tabIndex={-1} className="signal-review-section" aria-labelledby="flags-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Second review task</p><h2 id="flags-heading" tabIndex={-1}>Integrity signals</h2></div><span>{openFindingCount} open finding{openFindingCount === 1 ? "" : "s"}</span></div>
      <p className="signal-review-section__intro">These are possible inconsistencies, not accusations. Open a signal to inspect its excerpt, source anchor, and available instructor action.</p>
      <OriginalityScope scope={originalityScope} showAddAction />
      {flags?.length
        ? (
          <SignalFlagList
            flags={flags}
            showActions
            initialView={initialView}
            trackViewed
            viewedIds={viewedIds}
          />
        )
        : <Alert title="No integrity signals" tone={originalityUnassessed(originalityScope) ? "info" : "success"}>This run produced no checkable integrity signals.</Alert>}
    </section>
  );
}

export function SignalPublicFlags({
  flags,
  originalityScope,
}: {
  flags: FlagSummaryOut[];
  originalityScope?: OriginalityScopeValue | null;
}) {
  const findingCount = clusterFlagFindings(flags).length;
  return (
    <section id="integrity-signals" tabIndex={-1} className="signal-review-section" aria-labelledby="flags-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Integrity record</p><h2 id="flags-heading">Possible inconsistencies</h2></div><span>{findingCount} finding{findingCount === 1 ? "" : "s"}</span></div>
      <p className="signal-review-section__intro">These are possible inconsistencies, not accusations. This shared view shows the bounded evidence the instructor chose to share; it does not expose the private Audit trail.</p>
      <OriginalityScope scope={originalityScope} />
      {flags.length
        ? <SignalFlagList flags={flags} showActions={false} initialView="all" />
        : <Alert title="No integrity signals" tone={originalityUnassessed(originalityScope) ? "info" : "success"}>This run produced no shared integrity signals.</Alert>}
    </section>
  );
}

export function SignalCriteriaResults({ results, checkRunId, publicView = false }: {
  results: ResultRowCommon[];
  checkRunId: number;
  publicView?: boolean;
}) {
  const location = useLocation();
  return (
    <section className="signal-review-section" aria-labelledby="criteria-results-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Criterion record</p><h2 id="criteria-results-heading" tabIndex={-1}>Criterion records</h2></div><span>{results.length} {results.length === 1 ? "record" : "records"}</span></div>
      <p className="signal-review-section__intro">Each row shows the recorded status and any available evidence. A criterion marked Not assessed has no assessment result. Importance is shown as Low, Medium or High.</p>
      {results.length ? <ol className="signal-result-list">{results.map((row, index) => {
        const display = resultDisplay(row);
        const outcome = row.level ? { label: `Level: ${display.label}`, tone: "neutral" }
          : display.tone === "caution" ? { label: "Partly meets criterion", tone: "warning" }
          : OUTCOME_COPY[row.outcome];
        const evidenceSummaryId = `signal-criterion-evidence-${checkRunId}-${row.criterion_id}`;
        const unresolvedAi = row.kind === "semantic" && row.basis === "llm" && !row.resolution && row.resolved !== true && row.outcome === "escalated";
        const excerptAnchors = new Set(row.evidence.map((item) => item.anchor).filter((anchor) => anchor?.trim()));
        const seenAnchors = new Set<string>();
        const sourceAction = (anchor: string) => {
          const destination = reviewDeskDestination(`/report/${checkRunId}/document?anchor=${encodeURIComponent(anchor)}`, location);
          return <ActionLink
            to={destination}
            variant="quiet"
            aria-label={`Open in manuscript: ${instructorAnchor(anchor)}. Criterion ${index + 1}: ${row.text}`}
            onClick={() => rememberRouteReturnFocus(location.pathname + location.search, destination, evidenceSummaryId)}
          >Open in manuscript: {instructorAnchor(anchor)}</ActionLink>;
        };
        return <li key={row.criterion_id} className="signal-result-card"><div className="signal-result-card__top"><span className="signal-result-card__number">{index + 1}</span><span className={`signal-outcome signal-outcome--${outcome.tone}`}>{outcome.label}</span><span className={`signal-importance signal-importance--${row.weight_importance === "med" ? "medium" : row.weight_importance}`}>{row.weight_importance === "med" ? "Medium" : `${row.weight_importance.slice(0, 1).toUpperCase()}${row.weight_importance.slice(1)}`} importance</span></div><h3>{row.text}</h3>{row.resolution && <p className="signal-resolution-note"><strong>Instructor resolution:</strong> {row.resolution.reason}</p>}{/* BUG-065: this summary repeats verbatim once per row -- a screen-reader
                links/buttons list can't tell them apart without a per-criterion
                accessible name, same fix already applied to the flag "Review
                evidence" links and the escalation resolve-button groups. */}
              <details><summary id={evidenceSummaryId} aria-label={`Review evidence and reasoning: ${row.text}`}>Review evidence and reasoning</summary><div className="signal-result-card__evidence">
                {unresolvedAi && <><p><strong>No grade accepted</strong></p><p>{RECORDED_GRADE_BASIS.review}</p><p>{RECORDED_GRADE_BASIS.qualification}</p></>}
                {row.evidence.length ? row.evidence.map((item, evidenceIndex) => {
                  const hasAnchor = Boolean(item.anchor?.trim());
                  const showAction = hasAnchor && !seenAnchors.has(item.anchor);
                  if (hasAnchor) seenAnchors.add(item.anchor);
                  return <div className="signal-attached-flow" key={`${item.anchor}-${item.quote}-${evidenceIndex}`}>
                  <blockquote><span>“{item.quote}”</span><cite>{instructorAnchor(item.anchor)}</cite></blockquote>
                    {!hasAnchor && <p>No manuscript location was recorded for this excerpt.</p>}
                    {!publicView && showAction && sourceAction(item.anchor)}
                  </div>;
                }) : <p>{unresolvedAi ? RECORDED_GRADE_BASIS.missingExcerpt : "No verified excerpt was recorded for this criterion."}</p>}
                {!publicView && row.anchor?.trim() && !excerptAnchors.has(row.anchor) && <div className="signal-attached-flow"><p>Recorded criterion location: {row.anchor}</p>{sourceAction(row.anchor)}</div>}
                {row.reasoning?.trim() ? <p><strong>{unresolvedAi ? "Recorded AI explanation:" : "Recorded reasoning:"}</strong> {row.reasoning}</p> : unresolvedAi && <p>{RECORDED_GRADE_BASIS.missingExplanation}</p>}
                {row.reason && <p><strong>System note:</strong> {row.reason}</p>}
              </div></details></li>;
      })}</ol> : <div className="signal-desk-empty"><h3>No criterion records in this section</h3><p>No criterion records are shown here. Review the other report sections for unresolved criteria and assessment limits.</p></div>}
    </section>
  );
}
