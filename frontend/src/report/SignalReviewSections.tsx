import { ActionAssessmentContext, ReportAssessmentContext } from "./ReportAssessmentContext";
import { criterionAssessmentDetails } from "../domain/criterionAssessment";
import { CriterionSourceCoverage } from "./CriterionSourceCoverage";
import { RuleFactsDetails } from "./RuleFactsDetails";
import { RuleScopeNotice } from "./RuleScopeNotice";
import { coverageItems } from "./reportCoverage";
import { focusReportSection } from "./reportSectionNavigation";
import { pendingAssessmentRow } from "./pendingAssessmentRow";
import { clearPdfReading } from "../document/usePdfPageText";
import { recoverableReadError } from "./reportReadState";
import { reviewDeskDestination } from "../routing/reviewDeskReturn";
import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router";
import { CITATION_HISTORY_TITLE } from "../domain/citationHistory";
import type {
  CriterionResultOut,
  EscalatedItemOut,
  EscalationResolution,
  FlagSummaryOut,
  OriginalityScope as OriginalityScopeValue,
  ResultRowCommon,
  StatisticalCheckScopeOut,
} from "../api/types";
import { useMe } from "../auth/useAuth";
import {
  FLAG_LIST_BATCH_COUNT,
  FLAG_LIST_INITIAL_COUNT,
  RESOLUTION_REASON_MIN_LENGTH,
} from "../config/ui";
import { isManuscriptQuote } from "../domain/evidenceDisplay";
import { CRITERION_EXCLUSION_COPY, CRITERION_REVIEW_COPY, REVIEW_DRAFT_COPY, criterionReviewNotices, resolutionRequiresReason } from "../domain/criterionReview";
import { isOptionalComparisonLead } from "../domain/comparisonLeadIdentity";
import { useViewedFlagIds } from "../domain/flagViewed";
import { problemLabel } from "../domain/problemLabel";
import { instructorAnchor } from "../domain/sourceAnchor";
import { RECORDED_GRADE_BASIS } from "../domain/semanticSupport";
import { systemFindingCopy } from "../domain/systemFindingCopy";
import { rememberRouteReturnFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { OriginalityScope } from "./OriginalityScope";
import { StatisticalCheckScope } from "./StatisticalCheckScope";
import { originalityUnassessed } from "./originalityScopeState";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { clusterFlagFindings, type FlagFindingCluster } from "./flagClusters";
import { useEscalatedItems, useFlags } from "./useReport";
import { ReviewContext, useCriterionReviewScope } from "./criterionReviewContext";
import { SavedCriterionDestination, SavedJudgmentAccess } from "./SavedJudgmentAccess";
import { recordedGradeLabel, criterionRecordId, atLeastSavedJudgment, SAVED_CRITERION_PARAM } from "./savedJudgment";
import { RecordedJudgmentTools } from "./RecordedJudgmentTools";
import { ComparisonLeadList } from "./ComparisonLeadList";
import { comparisonLeadCollection } from "./comparisonLeadCollection";
import { resultDisplay } from "./ResultsTable";
import { useExplicitActionFocus } from "../routing/useExplicitActionFocus";
import { criterionReviewSearch, selectedCriterionId, SELECTED_CRITERION_PARAM } from "./criterionReviewNavigation";
import { useEvidenceManifest } from "./useEvidenceSource";
import type { OpenEvidenceSource } from "./evidenceTypes";

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
  if (resolution === "needs_document") return CRITERION_EXCLUSION_COPY.action;
  return item.levels?.find((entry) => entry.level === level)?.name ?? "Selected rubric level";
}

function DiscardReviewDraft({ checkRunId, id }: { checkRunId: number; id: number }) {
  const review = useCriterionReviewScope(checkRunId);
  const [confirm, setConfirm] = useState(false);
  const protectedSave = review.drafts.get(id)?.saving || review.drafts.get(id)?.uncertainRevision !== undefined;
  return <><Button variant="quiet" disabled={protectedSave} onClick={() => setConfirm(true)}>Discard this draft</Button>
    {confirm && <Dialog title="Discard this unsaved criterion draft?" initialFocusTitle onClose={() => setConfirm(false)}
      returnFocusTarget={() => document.getElementById(`signal-resolution-heading-${id}`) ?? document.getElementById("escalated-heading")}
      actions={<><Button variant="secondary" onClick={() => setConfirm(false)}>Keep draft</Button>
        <Button variant="danger" disabled={protectedSave} onClick={() => { review.cancel(id); setConfirm(false); }}>Discard draft</Button></>}>
      <p>This removes the unsaved choice and reason from this review. Recorded judgments and their history are unchanged.</p>
    </Dialog>}</>;
}

export type CriterionSourceTarget = (anchor: string, actionId: string) => string;
export type CriterionSourceOpen = (anchor: string, actionId: string) => void;

function SignalResolutionCard({ item, checkRunId, sharedReasonId, sourceTarget, onOpenSource, onOpenEvidenceSource, currentItems }: {
  item: EscalatedItemOut;
  checkRunId: number;
  sharedReasonId?: string;
  sourceTarget?: CriterionSourceTarget;
  onOpenSource?: CriterionSourceOpen;
  onOpenEvidenceSource?: OpenEvidenceSource;
  currentItems: boolean;
}) {
  const location = useLocation();
  const review = useCriterionReviewScope(checkRunId);
  const canonical = currentItems && review.assessmentCurrent?.()
    ? pendingAssessmentRow(review.assessment, item, checkRunId) : undefined;
  const assessment = canonical ? criterionAssessmentDetails(canonical, false) : undefined;
  const currentAssessment = review.assessmentCurrent?.() ? review.assessment : undefined;
  const manifestQuery = useEvidenceManifest(checkRunId, currentAssessment?.assessment_revision, Boolean(onOpenEvidenceSource));
  const manifest = currentAssessment && manifestQuery.isSuccess && manifestQuery.fetchStatus === "idle"
    && manifestQuery.data.assessment_revision === currentAssessment.assessment_revision ? manifestQuery.data : undefined;
  const reportHasLimits = currentAssessment && coverageItems(currentAssessment).length > 0;
  const pendingSource = (anchor: string, actionId: string) => {
    const fullReport = !onOpenSource && location.pathname === `/report/${checkRunId}`;
    const params = new URLSearchParams(location.search);
    if (fullReport) {
      ["finding", "flag", "location", "panel", "match", "criterion_record"].forEach(key => params.delete(key));
      clearPdfReading(params);
      params.set("view", "decision"); params.set("pane", "manuscript"); params.set("anchor", anchor);
    }
    const selectedWorkspace = !onOpenSource && location.pathname === `/report/${checkRunId}/document`;
    if (selectedWorkspace) { params.set("pane", "manuscript"); params.set("anchor", anchor); }
    const target = sourceTarget?.(anchor, actionId) ?? reviewDeskDestination(fullReport
      ? `/report/${checkRunId}/document?${params}`
      : selectedWorkspace ? `/report/${checkRunId}/document?${params}` : `/report/${checkRunId}/document?anchor=${encodeURIComponent(anchor)}`, location);
    const state = fullReport ? {
      ...(location.state && typeof location.state === "object" && !Array.isArray(location.state)
        ? Object.fromEntries(Object.entries(location.state).filter(([key]) => key !== "criterionRecordReturn" && key !== "pendingCriterionReturn")) : {}),
      pendingCriterionReturn: { kind: "pending", returnPath: location.pathname + location.search, elementId: actionId, checkResultId: item.check_result_id },
    } : undefined;
    return { target, state };
  };
  const coverageSourceAction = (anchor: string, slot: number) => {
    const actionId = `signal-resolution-coverage-${checkRunId}-${item.check_result_id}-${slot}`;
    const { target, state } = pendingSource(anchor, actionId);
    return <ActionLink id={actionId} state={state} to={target} variant="quiet" onClick={event => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (onOpenSource) { event.preventDefault(); onOpenSource(anchor, actionId); }
      else rememberRouteReturnFocus(location.pathname + location.search, target, actionId, { fallbackElementId: [`signal-resolution-heading-${item.check_result_id}`, "escalated-heading", "readiness-report-heading"] });
    }}>Open in manuscript: {instructorAnchor(anchor)}</ActionLink>;
  };
  const pending = review.drafts.get(item.check_result_id);
  const requiresReason = pending ? resolutionRequiresReason(pending.resolution) : false;
  const ordinaryGrade = Boolean(pending && !requiresReason);
  const reason = pending?.reason ?? "";
  const attempted = pending?.attempted ?? false;
  const saving = pending?.saving ?? false;
  const draftCurrent = pending ? review.isDraftCurrent(pending) : false;
  const [detailsOpen, setDetailsOpen] = useState(false);
  const reasonInvalid = attempted && requiresReason && reason.trim().length < RESOLUTION_REASON_MIN_LENGTH;
  const reasonErrorId = `signal-resolution-reason-${item.check_result_id}`;
  const criterionHeadingRef = useRef<HTMLHeadingElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const pendingHeadingRef = useRef<HTMLHeadingElement>(null);
  const followAction = useExplicitActionFocus(() => ordinaryGrade ? pendingHeadingRef.current : reasonRef.current ?? criterionHeadingRef.current);
  const followChoice = useExplicitActionFocus(() => ordinaryGrade ? pendingHeadingRef.current : reasonRef.current);
  const requestedReasonFocus = useRef(false);
  useEffect(() => {
    if (pending && requestedReasonFocus.current) {
      requestedReasonFocus.current = false;
      reasonRef.current?.focus();
    }
  }, [pending]);

  function choose(trigger: HTMLElement, resolution: EscalationResolution, level?: number) {
    followChoice(trigger, () => review.choose(item, resolution, level));
  }

  function cancel(trigger: HTMLElement) {
    if (saving) return;
    followAction(trigger, () => review.cancel(item.check_result_id));
  }

  function confirm() {
    requestedReasonFocus.current = draftCurrent && !saving && requiresReason && reason.trim().length < RESOLUTION_REASON_MIN_LENGTH;
    const submittedResolution = pending?.resolution;
    review.confirm(item.check_result_id, (out) => {
      if (submittedResolution === "needs_document") return CRITERION_EXCLUSION_COPY.success;
      const result = out.report.results.find((row) => row.criterion_id === item.criterion_id);
      const resultLabel = result?.level?.name
        ?? OUTCOME_COPY[out.outcome as ResultRowCommon["outcome"]]?.label ?? out.outcome;
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
  const instructionId = `signal-review-instructions-${checkRunId}`;
  const sourceQualificationId = `signal-resolution-source-qualification-${item.check_result_id}`;
  const actionContextId = `signal-resolution-context-${checkRunId}-${item.check_result_id}-${pending && !pending.needsChoice ? "confirm" : "choose"}`;
  const exclusionHintId = `signal-exclusion-hint-${item.check_result_id}`;
  const exclusionConsequenceId = `signal-exclusion-consequence-${item.check_result_id}`;
  const excluding = pending?.resolution === "needs_document";
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
    instructionId,
    sourceQualificationId,
    Boolean(review.assessmentNotices?.length) && actionContextId,
    sharedReasonId || (item.reason && reasonId),
    showInjectionSnippet && injectionId,
    item.unverified_evidence && item.unverified_evidence.length > 0 && unverifiedId,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <article className="signal-resolution-card">
      <div className="signal-resolution-card__context">
        <h3 id={`signal-resolution-heading-${item.check_result_id}`} ref={criterionHeadingRef} tabIndex={-1}>{item.criterion_text}</h3>
        <strong id={reviewStateId}>No grade accepted</strong>
        {reviewNotices.map((notice) => <p key={notice.kind} id={noticeId(notice.kind)} className="signal-resolution-card__vote">{notice.text}</p>)}
        {sharedReasonId
          ? <p>Unresolved. The shared review reason above applies to this criterion.</p>
          : item.reason && <p id={reasonId}>{item.reason}</p>}
        {showInjectionSnippet && (
          <blockquote className="signal-unverified-evidence"><strong id={injectionId}>Text addressed at an automated grader</strong><span>“{item.injection_matched_snippet}”</span></blockquote>
        )}
        {item.unverified_evidence && item.unverified_evidence.length > 0 && (
          <div className="signal-unverified-evidence"><strong id={unverifiedId}>Could not verify against the source</strong>{item.unverified_evidence.map((quote) => <blockquote key={quote}>“{quote}”</blockquote>)}</div>
        )}
        {assessment?.ruleScopeUnconfirmed && <RuleScopeNotice />}
        {assessment?.sourceCoverage && <CriterionSourceCoverage value={assessment.sourceCoverage} sourceAction={coverageSourceAction} firstSlot={item.evidence?.length ?? 0} criterionText={item.criterion_text} />}
        {reportHasLimits && <p className="signal-field-hint"><ActionLink to={{ pathname: `/report/${checkRunId}`, search: location.search, hash: "coverage-statement-heading" }} variant="quiet" onClick={event => focusReportSection(event, location, "coverage-statement-heading")}>Review assessment limits</ActionLink></p>}
        {assessment?.ruleFacts && <RuleFactsDetails facts={assessment.ruleFacts} />}
        {Boolean(item.evidence?.length) && <strong>Recorded passages and locations</strong>}
        {item.evidence?.map((entry, index) => {
          const actionId = `signal-resolution-source-${checkRunId}-${item.check_result_id}-${index}`;
          const { target, state } = pendingSource(entry.anchor, actionId);
          const recorded = manifest?.references.find(ref => ref.criterion_id === item.criterion_id
            && ref.role === "criterion_evidence" && ref.index === index && ref.quote === entry.quote && ref.anchor === entry.anchor);
          if (onOpenEvidenceSource) return <div className="signal-attached-flow" key={`${entry.anchor}-${index}`}>
            <blockquote>“{entry.quote}”</blockquote><p>{entry.anchor.trim() ? instructorAnchor(entry.anchor) : RECORDED_GRADE_BASIS.missingLocation}</p>
            <Button id={actionId} variant="quiet" disabled={!recorded || !manifest} onClick={() => {
              if (recorded && manifest) onOpenEvidenceSource({ snapshotId: manifest.snapshot_id, reference: recorded, actionId });
            }}>Show recorded location</Button>
          </div>;
          return <div className="signal-attached-flow" key={`${entry.anchor}-${index}`}><blockquote>“{entry.quote}”</blockquote>{entry.anchor.trim() ? <><p>{instructorAnchor(entry.anchor)}</p><ActionLink id={actionId} variant="quiet" state={state} to={target} aria-label={`Open in manuscript: ${instructorAnchor(entry.anchor)}. ${item.criterion_text}`} onClick={(event) => {
            if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            if (onOpenSource) {
              event.preventDefault();
              onOpenSource(entry.anchor, actionId);
            } else rememberRouteReturnFocus(location.pathname + location.search, target, actionId, { fallbackElementId: [`signal-resolution-heading-${item.check_result_id}`, "escalated-heading", "readiness-report-heading"] });
          }}>Open in manuscript: {instructorAnchor(entry.anchor)}</ActionLink></> : <p>{RECORDED_GRADE_BASIS.missingLocation}</p>}</div>;
        })}
        {onOpenEvidenceSource && manifestQuery.isFetching && <p role="status">Loading recorded source links.</p>}
        {onOpenEvidenceSource && manifestQuery.isError && <p role="status">Recorded source links could not be verified. <Button variant="quiet" onClick={() => { void manifestQuery.refetch(); }}>Retry source links</Button></p>}
        {!item.evidence?.length && !assessment?.ruleFacts && <p>{!item.reasoning?.trim() ? "No located source passage or assessment explanation is available. Review the manuscript before grading." : RECORDED_GRADE_BASIS.missingExcerpt}</p>}
        {item.reasoning?.trim() ? <div className="signal-copy-flow"><strong>{assessment?.explanationLabel ?? "Recorded explanation:"}</strong><p>{item.reasoning}</p></div>
          : Boolean(item.evidence?.length) && !assessment?.ruleFacts && (assessment?.source === "ai" || !item.reason?.trim()) && <p>{assessment?.missingExplanation ?? "No explanation is available in this assessment record."}</p>}
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

      {/* Keep the status region mounted. Only an explicit choice may continue
          focus to its selected heading or required reason. */}
      <div className="signal-resolution-form__pending" role="status" aria-live="polite">
        {pending && (
          <>
            {ordinaryGrade && draftCurrent && !saving && pending.uncertainRevision === undefined && !pending.needsChoice
              ? <p className="signal-field-hint">Unsaved grade. Your choice and comment are kept while you review this report.</p>
              : <><p className="signal-field-hint">{REVIEW_DRAFT_COPY.retention}</p><p className="signal-section-kicker">{saving ? "Step 2 of 2: saving judgment" : pending.uncertainRevision !== undefined ? "Step 2 of 2: save outcome unconfirmed" : "Step 2 of 2: nothing is saved yet"}</p></>}
            {pending.uncertainRevision !== undefined && <p>Saving could not be confirmed. Your choice and reason are kept. You cannot discard them or leave this review yet. Review the current criterion before trying again.</p>}
            <h4 ref={pendingHeadingRef} tabIndex={-1}>{resolutionLabel(item, pending.resolution, pending.level)}</h4>
            {!draftCurrent && !saving && <div className="signal-copy-flow">
              <strong>{review.decision ? "Recorded decision: review is read-only" : "Current review required"}</strong>
              {review.decision ? <><p>Reopen the decision before reviewing this draft.</p>
                <ActionLink variant="secondary" to={`/report/${checkRunId}#final-decision`} onClick={() => {
                  const target = document.getElementById("decision-heading");
                  target?.focus(); target?.scrollIntoView({ block: "nearest" });
                }}>Go to the recorded decision</ActionLink></>
                : <><p>Review the current criterion before using this draft. Confirming remains a separate step.</p>
                  <Button variant="secondary" onClick={() => review.reviewCurrent(item.check_result_id)}>Review current criterion</Button></>}
              {review.canUseCurrentDraft(pending) && <><p>The current criterion and its evidence are shown above. This report is open for instructor review.</p>
                <Button variant="secondary" onClick={event => followAction(event.currentTarget, () => review.useCurrentDraft(item.check_result_id))}>Use this draft for the current criterion</Button></>}
              <DiscardReviewDraft checkRunId={checkRunId} id={item.check_result_id} />
            </div>}
          </>
        )}
      </div>

      {item.levels && item.levels.length > 0 && (
        <details className="signal-levels">
          <summary aria-label={`View level descriptions for ${item.criterion_text}`}>View level descriptions ({item.levels.length})</summary>
          <ol>{item.levels.map((level) => <li key={level.level}><strong>{level.name}</strong><span id={levelDescriptionId(level.level)}>{level.descriptor || "Description not recorded for this level."}</span></li>)}</ol>
        </details>
      )}
      {!pending || pending.needsChoice ? (
        <div className="signal-resolution-options" aria-label={`Resolve: ${item.criterion_text}`}>
          <p id={sourceQualificationId} className="signal-field-hint">{RECORDED_GRADE_BASIS.qualification}</p>
          <ActionAssessmentContext id={actionContextId} notices={review.assessmentNotices ?? []} />
          {!review.canChoose && <p>Reload the current report, or reopen its recorded decision, before making a judgment.</p>}
          {pending?.needsChoice && <p>Your previous choice is no longer supported. Choose a current option; your unsaved reason is kept.</p>}
          {item.levels && item.levels.length > 0
            ? item.levels.map((level) => <Button key={level.level} variant="secondary" disabled={!review.canChoose} aria-describedby={`${describedBy} ${levelDescriptionId(level.level)}`} onClick={event => choose(event.currentTarget, "mark_level", level.level)}>{level.name}</Button>)
            : <><Button variant="secondary" disabled={!review.canChoose} aria-describedby={describedBy} onClick={event => choose(event.currentTarget, "mark_pass")}>Meets criterion</Button><Button variant="secondary" disabled={!review.canChoose} aria-describedby={describedBy} onClick={event => choose(event.currentTarget, "mark_fail")}>Does not meet</Button></>}
          <div className="signal-copy-flow"><Button variant="quiet" disabled={!review.canChoose} aria-describedby={`${describedBy} ${exclusionHintId}`} onClick={event => choose(event.currentTarget, "needs_document")}>{CRITERION_EXCLUSION_COPY.action}</Button><p id={exclusionHintId} className="signal-field-hint">{CRITERION_EXCLUSION_COPY.hint} <span>{CRITERION_EXCLUSION_COPY.wait}</span></p></div>
          {/* BUG-170: `verdict_unrecognized` means accepting this verdict
              is guaranteed to 409 server-side (the passes agreed on a
              string that still doesn't name any of this criterion's own
              levels) -- offering the button anyway would be the exact
              "offers to accept a verdict it just told you it cannot
              recognise" incoherence this ticket named. `item.reason`
              already explains why, so removing the button loses no
              information. */}
          {item.ai_majority_verdict !== null && !item.verdict_unrecognized && !reviewNotices.some((notice) => notice.kind === "support") && <Button disabled={!review.canChoose} variant={reviewNotices.some((notice) => notice.kind === "source_instructions") ? "quiet" : "secondary"} aria-describedby={describedBy} onClick={event => choose(event.currentTarget, "accept_majority")}>Accept AI suggestion: {item.ai_majority_verdict}</Button>}
        </div>
      ) : (
        <div className="signal-resolution-form">
          <p id={sourceQualificationId} className="signal-field-hint">{RECORDED_GRADE_BASIS.qualification}</p>
          {excluding && <div className="signal-copy-flow"><p id={exclusionConsequenceId}>{CRITERION_EXCLUSION_COPY.consequence}</p><p>{CRITERION_EXCLUSION_COPY.cancel}</p></div>}
          {selectedLevel && <div className="signal-copy-flow"><p><strong>Selected level: {selectedLevel.name}</strong></p><p>{selectedLevel.descriptor || "Description not recorded for this level."}</p></div>}
          <label><span>{excluding ? CRITERION_EXCLUSION_COPY.reason : ordinaryGrade ? "Comment (optional)" : "Reason (required)"}</span><textarea ref={reasonRef} disabled={saving} readOnly={!draftCurrent} rows={ordinaryGrade ? 2 : 3} required={requiresReason} value={reason} aria-invalid={reasonInvalid || undefined} aria-describedby={[excluding ? exclusionConsequenceId : null, reasonInvalid ? reasonErrorId : null].filter(Boolean).join(" ") || undefined} onChange={(event) => review.changeReason(item.check_result_id, event.target.value)} /></label>
          <p className="signal-field-hint">{ordinaryGrade ? "Any comment is kept with your instructor judgment and export." : "This reason appears in the instructor report and export."}</p>
          {reasonInvalid && <p id={reasonErrorId} role="alert" className="signal-field-error">{reason.trim() ? `Enter at least ${RESOLUTION_REASON_MIN_LENGTH} characters.` : "Enter a reason before confirming."}</p>}
          {saving && <p role="status">{REVIEW_DRAFT_COPY.saving}</p>}
          {Boolean(pending.error) && <Alert title="Saving could not be confirmed" tone="error" role="alert">Your entered reason is kept. Review the current criterion before trying again.</Alert>}
          <ActionAssessmentContext id={actionContextId} notices={review.assessmentNotices ?? []} />
          <div>{draftCurrent && <Button variant="secondary" disabled={saving || pending?.uncertainRevision !== undefined} onClick={event => cancel(event.currentTarget)}>Cancel</Button>}<Button data-criterion-confirm variant="brand" disabled={saving || !draftCurrent} aria-describedby={`${describedBy}${excluding ? ` ${exclusionConsequenceId}` : ""}`} busy={saving} onClick={confirm}>{excluding ? CRITERION_EXCLUSION_COPY.confirm : ordinaryGrade ? "Save grade" : "Confirm resolution"}</Button></div>
        </div>
      )}
    </article>
  );
}

export function SignalEscalatedPanel({ checkRunId, sourceTarget, onOpenSource, onOpenEvidenceSource, assessmentContextExternal = false }: { checkRunId: number; sourceTarget?: CriterionSourceTarget; onOpenSource?: CriterionSourceOpen; onOpenEvidenceSource?: OpenEvidenceSource; assessmentContextExternal?: boolean }) {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const selectedRaw = location.pathname.endsWith("/document") ? params.get(SELECTED_CRITERION_PARAM) : null;
  const selectedId = selectedCriterionId(selectedRaw);
  const selectedView = selectedRaw !== null;
  const { data, isPending, isError, error, fetchStatus, refetch } = useEscalatedItems(checkRunId);
  const accessUnavailable = !recoverableReadError(error);
  const loadedItems = accessUnavailable ? undefined : data;
  const review = useCriterionReviewScope(checkRunId);
  const items = useMemo(() => loadedItems?.filter((item) => !review.confirmed.has(item.criterion_id)), [loadedItems, review.confirmed]);
  const awaitingList = loadedItems?.some((item) => review.confirmed.has(item.criterion_id));
  const reviewRef = useRef(review);
  reviewRef.current = review;
  const [announcement, setAnnouncement] = useState("");
  const headingRef = useRef<HTMLHeadingElement>(null);
  const announcementRef = useRef<HTMLParagraphElement>(null);
  const hasItems = Boolean(items?.length);
  const reviewListStatus = fetchStatus === "fetching" ? loadedItems ? "Updating review list" : "Loading review list" : isError ? loadedItems ? "Review list needs refreshing" : "Review list unavailable" : awaitingList ? "Review list needs refreshing" : `${items?.length ?? 0} unresolved`;
  const selectedItem = items?.find(item => item.criterion_id === selectedId);
  const selectedRecord = selectedId === null ? undefined : review.confirmed.get(selectedId)
    ?? review.assessment?.results.find(row => row.criterion_id === selectedId && row.outcome !== "escalated");
  const selectedIndex = items?.findIndex(item => item.criterion_id === selectedId) ?? -1;
  const followSelection = useExplicitActionFocus(() => document.getElementById(
    selectedItem ? `signal-resolution-heading-${selectedItem.check_result_id}` : "escalated-heading"));
  const reviewLink = (criterionId?: number) => reviewDeskDestination(
    `/report/${checkRunId}/document?${criterionReviewSearch(location.search, criterionId)}`, location);
  const select = (event: React.MouseEvent<HTMLAnchorElement>, criterionId?: number) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (!location.pathname.endsWith("/document")) return;
    event.preventDefault();
    followSelection(event.currentTarget, () => setParams(criterionReviewSearch(location.search, criterionId), { state: location.state }));
  };
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
  }, [completion]);

  return (
    <section id="review-criteria" tabIndex={-1} data-tour="escalated-panel" className={selectedView ? "signal-review-section signal-selected-criterion" : "signal-review-section signal-review-section--attention"} aria-labelledby="escalated-heading">
      <div className={selectedView ? "sr-only" : "signal-review-section__heading"}><div>{review.decision && <p className="signal-section-kicker">Preserved assessment</p>}<h2 id="escalated-heading" ref={headingRef} tabIndex={-1}>{review.decision === "returned" ? "Unresolved at time of return" : review.decision ? "Unresolved at time of decision" : "Criteria needing your judgment"}</h2></div>{!selectedView && <span>{reviewListStatus}</span>}</div>
      {review.decision ? <p id={`signal-review-instructions-${checkRunId}`} className={selectedView ? "sr-only" : "signal-review-section__intro"}>These criteria were unresolved when the decision was recorded. Their outcomes, evidence and assessment limits are preserved. Reopen the decision before making further judgments.</p>
        : <p id={`signal-review-instructions-${checkRunId}`} className={selectedView ? "sr-only" : "signal-review-section__intro"}>{selectedView ? "Read this criterion beside the manuscript, choose a grade, then save. Comments are optional for ordinary grades." : "Open a criterion to review its recorded passages beside the manuscript and record your judgment."} {!selectedView && <span>{RECORDED_GRADE_BASIS.qualification}</span>}</p>}
      {!selectedView && !assessmentContextExternal && <ReportAssessmentContext notices={review.assessmentNotices ?? []} />}
      <p ref={announcementRef} tabIndex={-1} aria-live="polite" className="signal-live-region">{announcement}</p>
      {!accessUnavailable && <SavedJudgmentAccess checkRunId={checkRunId} hideEmpty />}
      {selectedView && <nav aria-label="Criterion review" className="signal-criterion-navigation">
        <ActionLink variant="quiet" state={location.state} to={reviewLink()} onClick={event => select(event)}>Back to criteria</ActionLink>
        {selectedIndex > 0 && <ActionLink variant="secondary" aria-label="Previous criterion" state={location.state} to={reviewLink(items![selectedIndex - 1].criterion_id)} onClick={event => select(event, items![selectedIndex - 1].criterion_id)}>Previous</ActionLink>}
        {items && selectedIndex >= 0 && selectedIndex < items.length - 1 && <ActionLink variant="secondary" aria-label="Next criterion" state={location.state} to={reviewLink(items[selectedIndex + 1].criterion_id)} onClick={event => select(event, items[selectedIndex + 1].criterion_id)}>Next</ActionLink>}
        <span role="status" className="signal-field-hint">{reviewListStatus}</span>
      </nav>}
      {!accessUnavailable && [...review.drafts].filter(([id, draft]) => draft.currentResolved && !draft.saving
        && !items?.some(item => item.check_result_id === id) && selectedView && draft.criterionId === selectedId).map(([id, draft]) => <article key={`retained-${id}`} className="signal-resolution-card">
        <h3 id={`signal-resolution-heading-${id}`} tabIndex={-1}>Retained entry: {draft.criterionText}</h3>
        <p>This criterion is no longer in the unresolved list. Your earlier choice and reason are kept here. Review the recorded judgment before continuing.</p>
        <blockquote>{draft.reason || "No reason entered."}</blockquote>
        <Button variant="secondary" onClick={event => review.viewSaved(draft.criterionId, event.currentTarget)}>Review recorded judgment</Button>
        <DiscardReviewDraft checkRunId={checkRunId} id={id} />
      </article>)}
      <div className="signal-attached-flow">
      {isPending && <p role="status">Loading criteria that need review.</p>}
      {isError && <Alert title={loadedItems ? "Could not update criteria needing review" : "Could not load criteria needing review"} tone="warning"><p>{loadedItems ? "Showing the last loaded review items. Retry the list to check which criteria still need attention." : "Retry the review list to check which criteria still need attention."}</p><Button variant="secondary" aria-disabled={fetchStatus === "fetching" || undefined} onClick={() => { if (fetchStatus !== "fetching") void refetch(); }}>Retry review items</Button></Alert>}
      {fetchStatus === "fetching" && !isPending && <p role="status">Updating criteria needing review.</p>}
      {awaitingList && !isError && <Alert title="Your judgment is saved" tone="info">Refresh the review list to confirm whether other criteria still need attention. <Button variant="quiet" onClick={() => { void refetch(); }}>Refresh review items</Button></Alert>}
      {!selectedView && sharedReason && <Alert title="Shared review reason" tone="warning"><p id={sharedReasonId}>{sharedReason}</p></Alert>}
      {selectedView ? selectedItem && !accessUnavailable
        ? <SignalResolutionCard key={`${checkRunId}-${selectedItem.check_result_id}`} item={selectedItem} checkRunId={checkRunId} sourceTarget={sourceTarget} onOpenSource={onOpenSource} onOpenEvidenceSource={onOpenEvidenceSource} currentItems={!isPending && !isError && fetchStatus === "idle"} />
        : !isPending && fetchStatus === "idle" && !isError && <div className="signal-attached-flow" role="status">
          <h3 tabIndex={-1}>{selectedRecord ? selectedRecord.text : "Criterion not available"}</h3>
          <p>{selectedRecord ? "This criterion is no longer unresolved. Review its recorded outcome before making another judgment." : "That criterion is not in this report’s unresolved review list. Choose a criterion from the list."}</p>
          {selectedRecord && <Button variant="secondary" onClick={event => review.viewSaved(selectedRecord.criterion_id, event.currentTarget)}>Review recorded judgment</Button>}
        </div>
        : <>
          {hasItems && <div className="signal-criterion-list">{items?.map(item => <article key={item.check_result_id} className="signal-criterion-list__row">
            <div><h3>{item.criterion_text}</h3><p>{review.drafts.has(item.check_result_id) ? "Unsaved entry kept" : "Needs your judgment"}</p><p>{item.evidence?.length ? `${item.evidence.length} recorded source ${item.evidence.length === 1 ? "passage" : "passages"}` : "No source passage recorded"}</p></div>
            <ActionLink id={`signal-criterion-open-${checkRunId}-${item.check_result_id}`} state={location.state} variant="secondary" to={reviewLink(item.criterion_id)} onClick={event => select(event, item.criterion_id)} aria-label={`Review criterion: ${item.criterion_text}`}>Review criterion</ActionLink>
          </article>)}</div>}
          {!accessUnavailable && [...review.drafts].filter(([id, draft]) => draft.currentResolved && !items?.some(item => item.check_result_id === id)).map(([id, draft]) => <article key={`retained-link-${id}`} className="signal-criterion-list__row"><div><h3>{draft.criterionText}</h3><p>Retained entry: current judgment needs review</p></div><ActionLink variant="secondary" to={reviewLink(draft.criterionId)} onClick={event => select(event, draft.criterionId)}>Review retained entry</ActionLink></article>)}
          {!hasItems && !awaitingList && !isPending && !isError && fetchStatus === "idle" && <Alert title="No unresolved criteria" tone="success">Every criterion has a recorded outcome. Continue to the integrity signals.</Alert>}
        </>}
      </div>
    </section>
  );
}

export function SignalFlagsPanel({
  checkRunId,
  unresolvedHighFlagCount,
  originalityScope,
  statisticalScope,
}: {
  checkRunId: number;
  unresolvedHighFlagCount: number;
  originalityScope?: OriginalityScopeValue | null;
  statisticalScope?: StatisticalCheckScopeOut | null;
}) {
  const flagsQuery = useFlags(checkRunId);
  const flags = recoverableReadError(flagsQuery.error) ? flagsQuery.data : undefined;
  const { data: me } = useMe();
  const viewedIds = useViewedFlagIds(me?.id);
  const otherFlags = flags?.filter((flag) => !isOptionalComparisonLead(flag));
  const optional = comparisonLeadCollection(checkRunId, flagsQuery);
  const flagsLastLoaded = flags !== undefined && (flagsQuery.fetchStatus !== "idle" || flagsQuery.isError);
  const usableFlags = flags !== undefined && (flags.length > 0 || optional.state === "success");
  const hasOptional = Boolean(optional.snapshot?.total);
  const onlyOptional = optional.state === "success" && hasOptional && otherFlags?.length === 0;
  const openFindingCount = clusterFlagFindings(otherFlags?.filter((flag) => !flag.overridden) ?? []).length;
  // BUG-167: defaults the FILTER to the worst unresolved severity present,
  // not the declaration order -- see `defaultFlagView`'s own comment for
  // why this is a filter-default decision, not a reorder.
  const initialView = otherFlags ? defaultFlagView(unresolvedHighFlagCount, otherFlags) : "open";

  return (
    <section id="integrity-signals" tabIndex={-1} className="signal-review-section" aria-labelledby="flags-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Integrity record</p><h2 id="flags-heading" tabIndex={-1}>Integrity signals</h2></div>{usableFlags && !onlyOptional && <span>{flagsLastLoaded ? "Last loaded: " : ""}{openFindingCount} {hasOptional ? "other open" : "open"} finding{openFindingCount === 1 ? "" : "s"}</span>}</div>
      {!onlyOptional && <p className="signal-review-section__intro">These are possible inconsistencies, not accusations. Open a signal to inspect its excerpt, source anchor, and available instructor action.</p>}
      <StatisticalCheckScope scope={statisticalScope} />
      <OriginalityScope scope={originalityScope} showAddAction />
      {otherFlags && otherFlags.length > 0
        ? (
          <SignalFlagList
            flags={otherFlags}
            showActions
            initialView={initialView}
            trackViewed
            viewedIds={viewedIds}
          />
        )
        : onlyOptional ? null : optional.state === "success" ? <Alert title="No integrity signals" tone={originalityUnassessed(originalityScope) ? "info" : "success"}>This run produced no checkable integrity signals.</Alert>
          : <p>{optional.state === "error" ? "Other findings could not be loaded." : optional.state === "paused" ? "Waiting to load other findings." : optional.state === "fetching" ? "Loading other findings." : "Other findings are not available."}</p>}
      <ComparisonLeadList collection={optional} showActions viewedIds={viewedIds} onRetry={() => { void flagsQuery.refetch(); }} />
    </section>
  );
}

export function SignalPublicFlags({
  flags,
  originalityScope,
  checkRunId,
  statisticalScope,
}: {
  flags: FlagSummaryOut[];
  originalityScope?: OriginalityScopeValue | null;
  checkRunId: number;
  statisticalScope?: StatisticalCheckScopeOut | null;
}) {
  const otherFlags = flags.filter((flag) => !isOptionalComparisonLead(flag));
  const optional = comparisonLeadCollection(checkRunId, { data: flags, fetchStatus: "idle", isSuccess: true, isError: false });
  const findingCount = clusterFlagFindings(otherFlags).length;
  const hasOptional = Boolean(optional.snapshot?.total);
  const onlyOptional = hasOptional && otherFlags.length === 0;
  return (
    <section id="integrity-signals" tabIndex={-1} className="signal-review-section" aria-labelledby="flags-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Integrity record</p><h2 id="flags-heading">Possible inconsistencies</h2></div>{!onlyOptional && <span>{findingCount} {hasOptional ? "other " : ""}finding{findingCount === 1 ? "" : "s"}</span>}</div>
      {!onlyOptional && <p className="signal-review-section__intro">These are possible inconsistencies, not accusations. This shared view shows the bounded evidence the instructor chose to share; it does not expose the private Audit trail.</p>}
      <StatisticalCheckScope scope={statisticalScope} />
      <OriginalityScope scope={originalityScope} />
      {otherFlags.length
        ? <SignalFlagList flags={otherFlags} showActions={false} initialView="all" />
        : onlyOptional ? null : <p>This run produced no shared integrity signals.</p>}
      <ComparisonLeadList collection={optional} showActions={false} />
    </section>
  );
}

export function SignalCriteriaResults({ results, checkRunId, publicView = false, sourceTarget, onOpenSource }: {
  results: ResultRowCommon[];
  checkRunId: number;
  publicView?: boolean;
  sourceTarget?: CriterionSourceTarget;
  onOpenSource?: CriterionSourceOpen;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const review = useContext(ReviewContext);
  const privateScope = !publicView && review?.checkRunId === checkRunId;
  const rows = results.filter((row) => {
    const confirmed = privateScope ? review.confirmed.get(row.criterion_id) : undefined;
    return !confirmed || atLeastSavedJudgment(row, confirmed);
  });
  const updating = rows.length !== results.length || (privateScope
    && [...review.confirmed.values()].some((saved) => !rows.some((row) => atLeastSavedJudgment(row, saved))));
  return (
    <section className="signal-review-section" aria-labelledby="criteria-results-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Criterion record</p><h2 id="criteria-results-heading" tabIndex={-1}>Criterion records</h2></div><span>{updating ? "Recorded results updating" : `${rows.length} ${rows.length === 1 ? "record" : "records"}`}</span></div>
      <p className="signal-review-section__intro">Each row shows the recorded status and any available evidence. A criterion marked Not assessed has no assessment result. Importance is shown as Low, Medium or High.</p>
      {privateScope && <SavedCriterionDestination checkRunId={checkRunId} results={rows} />}
      {rows.length ? <ol className="signal-result-list">{rows.map((row, index) => {
        if (privateScope && review.revisions.records.get(row.criterion_id)?.unavailable) {
          return <li key={row.criterion_id} className="signal-result-card"><div className="signal-result-card__top" />
            <h3 id={criterionRecordId(checkRunId, row.criterion_id)} tabIndex={-1}>Recorded criterion unavailable</h3>
            <p>This criterion could not be accessed. Its last loaded private details have been cleared.</p>
            <Button variant="secondary" onClick={() => review.revisions.open(row.criterion_id, "current")}>Retry recorded criterion</Button></li>;
        }
        const display = resultDisplay(row);
        const assessment = criterionAssessmentDetails(row, publicView);
        const outcome = !publicView && row.resolution?.type === "mark_level" && !row.level?.name?.trim()
          ? { label: "Recorded grade unavailable", tone: "neutral" }
          : row.level ? { label: `Level: ${display.label}`, tone: "neutral" }
          : display.tone === "caution" ? { label: "Partly meets criterion", tone: "warning" }
          : OUTCOME_COPY[row.outcome];
        const evidenceSummaryId = `signal-criterion-evidence-${checkRunId}-${row.criterion_id}`;
        const unresolvedAi = assessment.source === "ai" && !assessment.instructorRecorded && row.outcome === "escalated";
        const excerptAnchors = new Set(row.evidence.map((item) => item.anchor).filter((anchor) => anchor?.trim()));
        const seenAnchors = new Set<string>();
        const sourceAction = (anchor: string, stableSlot?: number) => {
          const sourceIndex = row.evidence.findIndex((entry) => entry.anchor === anchor);
          const actionId = `signal-saved-source-${checkRunId}-${row.criterion_id}-${stableSlot ?? (sourceIndex < 0 ? row.evidence.length : sourceIndex)}`;
          const destination = sourceTarget?.(anchor, actionId) ?? reviewDeskDestination(`/report/${checkRunId}/document?anchor=${encodeURIComponent(anchor)}`, location);
          const fullReport = location.pathname === `/report/${checkRunId}`;
          const originParams = new URLSearchParams(location.search);
          if (fullReport) originParams.set(SAVED_CRITERION_PARAM, String(row.criterion_id));
          const returnPath = fullReport ? `${location.pathname}?${originParams}` : location.pathname + location.search;
          const sourceState = { ...location.state, criterionRecordReturn: { returnPath, elementId: actionId } };
          return <ActionLink
            id={actionId}
            to={destination}
            state={!onOpenSource ? sourceState : undefined}
            variant="quiet"
            aria-label={`Open in manuscript: ${instructorAnchor(anchor)}. Criterion ${index + 1}: ${row.text}`}
            onClick={(event) => {
              if (onOpenSource && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
                event.preventDefault(); onOpenSource(anchor, actionId);
              } else if (!onOpenSource && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
                if (fullReport) {
                  event.preventDefault();
                  // Keep the browser's previous entry bound to this criterion,
                  // so Back and reload retain the source's actual origin too.
                  void navigate(`${returnPath}#${actionId}`, { replace: true, state: location.state });
                }
                rememberRouteReturnFocus(returnPath, destination, actionId,
                  { fallbackElementId: criterionRecordId(checkRunId, row.criterion_id) });
                if (fullReport) void navigate(destination, { state: sourceState });
              }
            }}
          >Open in manuscript: {instructorAnchor(anchor)}</ActionLink>;
        };
        return <li key={row.criterion_id} className="signal-result-card"><div className="signal-result-card__top"><span className="signal-result-card__number">{index + 1}</span><span className={`signal-outcome signal-outcome--${assessment.ruleScopeUnconfirmed && !assessment.instructorRecorded ? "neutral" : outcome.tone}`}>{assessment.ruleScopeUnconfirmed && !assessment.instructorRecorded ? "Previously recorded: " : ""}{outcome.label}</span><span className={`signal-importance signal-importance--${row.weight_importance === "med" ? "medium" : row.weight_importance}`}>{row.weight_importance === "med" ? "Medium" : `${row.weight_importance.slice(0, 1).toUpperCase()}${row.weight_importance.slice(1)}`} importance</span></div><h3 id={criterionRecordId(checkRunId, row.criterion_id)} tabIndex={-1}>{row.text}</h3>{assessment.ruleScopeUnconfirmed && !assessment.instructorRecorded && <RuleScopeNotice />}{!publicView && row.resolution && <p className="signal-resolution-note"><strong>Instructor judgment</strong></p>}{!publicView && row.resolution?.type === "needs_document" && <p className="signal-resolution-note">{CRITERION_EXCLUSION_COPY.recorded}. {CRITERION_EXCLUSION_COPY.noGrade}</p>}{!publicView && row.resolution && <>{row.resolution.type !== "needs_document" && <p className="signal-resolution-note"><strong>Recorded grade:</strong> {recordedGradeLabel(row)}</p>}<p className="signal-resolution-note"><strong>Instructor reason:</strong> {row.resolution.reason?.trim() ? row.resolution.reason : "No instructor reason is available in this record."}</p></>}{/* BUG-065: this summary repeats verbatim once per row -- a screen-reader
                links/buttons list can't tell them apart without a per-criterion
                accessible name, same fix already applied to the flag "Review
                evidence" links and the escalation resolve-button groups. */}
              {privateScope && (row.resolution || (row.type !== "not_assessable" && (row as CriterionResultOut).judgment_revision == null
                && ["structural", "semantic"].includes(row.kind) && ["passed", "failed"].includes(row.outcome)))
                && <RecordedJudgmentTools checkRunId={checkRunId} criterionId={row.criterion_id} criterionText={row.text} canonical={row} />}
              {assessment.ruleScopeUnconfirmed && assessment.instructorRecorded && <RuleScopeNotice earlier />}
              <details><summary id={evidenceSummaryId} aria-label={`${assessment.summary}: ${row.text}`}>{assessment.summary}</summary><div className="signal-result-card__evidence">
                {assessment.relationship && <p>{assessment.relationship}</p>}
                {assessment.sourceCoverage && <CriterionSourceCoverage value={assessment.sourceCoverage} sourceAction={sourceAction} firstSlot={row.evidence.length + 1} criterionText={row.text} />}
                {unresolvedAi && <><p><strong>No grade accepted</strong></p><p>{RECORDED_GRADE_BASIS.review}</p><p>{RECORDED_GRADE_BASIS.qualification}</p></>}
                {!assessment.hasDetails ? <p>No assessment details are available in this record.</p> : <>
                {assessment.ruleFacts && <RuleFactsDetails facts={assessment.ruleFacts} earlier={assessment.instructorRecorded} />}
                {row.evidence.length > 0 && <h4>Recorded manuscript excerpts</h4>}
                {!unresolvedAi && assessment.hasLocations && <p>{RECORDED_GRADE_BASIS.qualification}</p>}
                {row.evidence.length ? row.evidence.map((item, evidenceIndex) => {
                  const hasAnchor = Boolean(item.anchor?.trim());
                  const showAction = hasAnchor && !seenAnchors.has(item.anchor);
                  if (hasAnchor) seenAnchors.add(item.anchor);
                  return <div className="signal-attached-flow" key={`${item.anchor}-${item.quote}-${evidenceIndex}`}>
                  <blockquote><span>“{item.quote}”</span><cite>{instructorAnchor(item.anchor)}</cite></blockquote>
                    {!hasAnchor && <p>No manuscript location was recorded for this excerpt.</p>}
                    {!publicView && showAction && sourceAction(item.anchor)}
                  </div>;
                }) : !assessment.ruleFacts && <p>{unresolvedAi ? RECORDED_GRADE_BASIS.missingExcerpt : "No excerpt is available in this assessment record."}</p>}
                {!publicView && row.anchor?.trim() && !excerptAnchors.has(row.anchor) && <div className="signal-attached-flow"><p>Recorded criterion location: {row.anchor}</p>{sourceAction(row.anchor)}</div>}
                {row.reasoning?.trim() ? <p><strong>{assessment.explanationLabel}</strong> {row.reasoning}</p> : !assessment.ruleFacts && (unresolvedAi || !row.reason?.trim()) && <p>{unresolvedAi ? RECORDED_GRADE_BASIS.missingExplanation : assessment.missingExplanation}</p>}
                {row.reason?.trim() && <p><strong>Recorded system note:</strong> {row.reason}</p>}
                </>}
              </div></details></li>;
      })}</ol> : updating ? <p>Recorded results are updating.</p> : <div className="signal-desk-empty"><h3>No criterion records in this section</h3><p>No criterion records are shown here. Review the other report sections for unresolved criteria and assessment limits.</p></div>}
    </section>
  );
}
