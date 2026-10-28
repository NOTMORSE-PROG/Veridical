import { CriterionAssessment, CriterionEvidencePacket } from "./CriterionEvidencePacket";
import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router";
import type {
  EscalatedItemOut,
  EscalationResolution,
  FlagSummaryOut,
  OriginalityScope as OriginalityScopeValue,
  ResultRowCommon,
  EvidenceItem,
  ReportOut,
} from "../api/types";
import { useMe } from "../auth/useAuth";
import { AI_REVIEW_BASIS } from "../demo/preparedAiReview";
import { criterionAnchorRegion } from "../document/criterionAnchor";
import {
  FLAG_LIST_BATCH_COUNT,
  FLAG_LIST_INITIAL_COUNT,
  RESOLUTION_REASON_MIN_LENGTH,
} from "../config/ui";
import { isManuscriptQuote } from "../domain/evidenceDisplay";
import { useViewedFlagIds } from "../domain/flagViewed";
import { problemLabel } from "../domain/problemLabel";
import { systemFindingCopy } from "../domain/systemFindingCopy";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { clusterFlagFindings, type FlagFindingCluster } from "./flagClusters";
import { IntegrityModuleNavigation, IntegrityModuleSummary } from "./IntegrityModules";
import { CHECK_MODULE_PARAM, integrityModuleCounts, integrityModuleLabel, selectedIntegrityModule } from "./integrityModuleState";
import { useEscalatedItems, useFlags, useResolveEscalation } from "./useReport";

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
  renderEvidence,
}: {
  renderEvidence?: (flag: FlagSummaryOut) => ReactNode;
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
          <span className={`signal-severity signal-severity--${first.severity}`}>{SEVERITY_LABEL[first.severity]}</span>
          {!first.overridden && viewedIds.has(first.id) && (
            <span className="signal-viewed-state"><ViewedIcon />Viewed</span>
          )}
          {first.overridden && <span className="signal-resolution-state">Resolved by instructor</span>}
          {first.confirmed_citation_source && <span className="signal-resolution-state">Source confirmed</span>}
        </div>
        <h3 id={headingId} tabIndex={-1}>{problemLabel(first.problem_kind) ?? first.criterion_text ?? "Possible inconsistency"}</h3>
        {/* BUG-170: a whole-document/chapter/resubmission reuse flag's
            evidence_excerpt is a system-authored template sentence, never
            manuscript text -- <blockquote> claims a quotation that isn't
            one. No separate "reasoning" field exists at this summary
            level, so the same text still renders, just not quote-styled. */}
        {isManuscriptQuote(first)
          ? <blockquote>“{systemFindingCopy(first.evidence_excerpt, first.problem_kind, "evidence")}”</blockquote>
          : <p className="signal-flag-finding-context">{systemFindingCopy(first.evidence_excerpt, first.problem_kind, "evidence")}</p>}
        <p>{first.page_anchor}</p>
        {renderEvidence?.(first)}
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
        <span className={`signal-severity signal-severity--${severity}`}>{SEVERITY_LABEL[severity]}</span>
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
            aria-label={`Review evidence at ${first.page_anchor} (one of ${flags.length} locations)`}
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
                <strong>{flag.page_anchor}</strong>
                {mixedSeverity && <span className={`signal-severity signal-severity--${flag.severity}`}>{SEVERITY_LABEL[flag.severity]}</span>}
                {!flag.overridden && viewedIds.has(flag.id) && (
                  <span className="signal-viewed-state"><ViewedIcon />Viewed</span>
                )}
                {flag.overridden && <span className="signal-resolution-state">Resolved by instructor</span>}
                {flag.confirmed_citation_source && <span className="signal-resolution-state">Source confirmed</span>}
              </div>
              {renderEvidence?.(flag)}
              {showActions && (
                <ActionLink
                  id={`signal-flag-review-${flag.id}`}
                  to={`/flags/${flag.id}`}
                  variant="secondary"
                  aria-label={`Review evidence at ${flag.page_anchor}, location ${locationIndex + 1} of ${flags.length}`}
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
  report,
  originalityScope,
  renderEvidence,
}: {
  renderEvidence?: (flag: FlagSummaryOut) => ReactNode;
  flags: FlagSummaryOut[];
  showActions: boolean;
  initialView: FlagView;
  // BUG-167: both optional, both default to the no-op/off state, so
  // `SignalPublicFlags` (the adviser/share view) needs zero changes --
  // "opened" tracking is an instructor-only convenience, never shown to
  // an external reader.
  trackViewed?: boolean;
  viewedIds?: ReadonlySet<number>;
  report?: ReportOut;
  originalityScope?: OriginalityScopeValue | null;
}) {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const moduleFocus = useRef(false);
  const requestedView = readFlagView(searchParams, initialView);
  const module = selectedIntegrityModule(searchParams.get(CHECK_MODULE_PARAM), flags, ["high", "med", "low"].includes(requestedView) ? requestedView : undefined);
  const moduleFlags = flags.filter(flag => flag.check_kind === module);
  const moduleInitialView = initialView === "all" ? "all" : defaultFlagView(Number(initialView === "high" && moduleFlags.some(flag => !flag.overridden && flag.severity === "high")), moduleFlags);
  function setSearchParams(next: URLSearchParams, options: { replace: boolean }) {
    navigate({ pathname: location.pathname, search: `?${next}`, hash: location.hash }, options);
  }
  useEffect(() => {
    if (searchParams.get(CHECK_MODULE_PARAM) === module) return;
    const next = new URLSearchParams(searchParams);
    next.set(CHECK_MODULE_PARAM, module);
    navigate({ pathname: location.pathname, search: `?${next}`, hash: location.hash }, { replace: true });
  }, [module, searchParams, navigate, location.pathname, location.hash]);
  useEffect(() => {
    if (moduleFocus.current) { document.getElementById("integrity-module-heading")?.focus(); moduleFocus.current = false; }
  }, [module]);
  // BUG-167 (design review finding, live-reproduced before it could break
  // the hero-KPI link below): a `useState` mirror initialized ONCE from
  // `searchParams` goes stale the moment the URL changes any other way
  // (e.g. the hero KPI link setting `flags_view=high` directly) -- the
  // pressed filter button kept showing the PREVIOUS view while the URL
  // already said otherwise. `openClusters` two lines down never had this
  // bug because it was already derived fresh from `searchParams` on every
  // render, no local-state mirror -- `view`/`visibleCount` now match it.
  const view = readFlagView(searchParams, moduleInitialView);
  const visibleCount = readVisibleCount(searchParams, FLAG_LIST_INITIAL_COUNT);
  const [focusClusterKey, setFocusClusterKey] = useState<string>();
  const allClusters = clusterFlagFindings(flags);
  const modules = integrityModuleCounts(allClusters.map(cluster => ({ kind: cluster.flags[0].check_kind, open: cluster.flags.some(flag => !flag.overridden) })));
  const canonicalClusters = allClusters.filter(cluster => cluster.flags[0].check_kind === module);
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
    if (next === moduleInitialView) nextParams.delete(FLAG_VIEW_PARAM);
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
      <IntegrityModuleNavigation modules={modules} selected={module} onSelect={kind => {
        if (kind === module) return;
        moduleFocus.current = true;
        const next = new URLSearchParams(searchParams);
        next.set(CHECK_MODULE_PARAM, kind);
        next.delete(VISIBLE_COUNT_PARAM);
        setSearchParams(next, { replace: false });
      }} />
      <section className="signal-integrity-module-content" aria-labelledby="integrity-module-heading">
      <IntegrityModuleSummary kind={module} report={report} originalityScope={originalityScope} showAddAction={showActions} />
      <div className="signal-flag-tools">
        <p className="signal-field-hint">Filters and counts below apply to {integrityModuleLabel(module)}.</p>
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
        {view === moduleInitialView && moduleInitialView === "high" && (
          <p className="signal-field-hint">
            High severity is shown first for this check. An unresolved high-severity signal makes a report Not Ready by itself. Select Open or All to see every finding in this check.
          </p>
        )}
        {view === moduleInitialView && moduleInitialView === "med" && (
          <p className="signal-field-hint">
            Medium severity is shown first for this check. Select Open or All to see every saved finding in this check.
          </p>
        )}
        <p role="status" aria-live="polite">
          Showing {visible.length} of {filtered.length} {viewLabel} finding{filtered.length === 1 ? "" : "s"} across {locationCount} location{locationCount === 1 ? "" : "s"}.
        </p>
        {trackViewed && filtered.length > 0 && (() => {
          const openClustersAll = clusterFlagFindings(moduleFlags.filter((flag) => !flag.overridden));
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
              renderEvidence={renderEvidence}
            />
          ))}
        </ul>
      ) : (
        <Alert title={`No ${viewLabel} signals`} tone="info">{moduleFlags.length ? "Choose another filter to review the remaining findings in this check." : "No findings were saved for this check. This does not establish that it completed or passed."}</Alert>
      )}

      {remaining > 0 && (
        <div className="signal-flag-more">
          <Button variant="secondary" onClick={showMore}>
            Show {Math.min(FLAG_LIST_BATCH_COUNT, remaining)} more
          </Button>
        </div>
      )}
      </section>
    </>
  );
}

function voteSummary(item: EscalatedItemOut): string {
  if (item.review_basis === AI_REVIEW_BASIS) return `AI review complete. Recorded grade: ${item.review_grade}. Confirm or change the grade after checking the evidence.`;
  if (item.review_basis === "document_review") return "Read the source notes, then record a rubric level.";
  if (item.review_reason === "not_graded") return "The AI did not grade this criterion.";
  if (item.review_reason === "injection_suspected") return "AI passes agreed, but that agreement cannot be trusted here.";
  if (item.votes.length === 0) return "No AI votes were recorded.";
  const realVotes = item.votes.filter((vote): vote is string => vote !== null);
  if (realVotes.length === 0) return `No valid verdict from ${item.votes.length} grading ${item.votes.length === 1 ? "pass" : "passes"}.`;
  if (item.ai_majority_verdict === null) return `Split vote: ${item.votes.map((vote) => vote ?? "no verdict").join(", ")}.`;
  const agreeing = item.votes.filter((vote) => vote === item.ai_majority_verdict).length;
  return `${agreeing} of ${item.votes.length} AI passes reached the same tentative verdict.`;
}

function resolutionLabel(item: EscalatedItemOut, resolution: EscalationResolution, level?: number): string {
  if (resolution === "accept_majority") return `Accept AI suggestion: ${item.ai_majority_verdict}`;
  if (resolution === "mark_pass") return "Mark as meets criterion";
  if (resolution === "mark_fail") return "Mark as does not meet";
  if (resolution === "needs_document") return "Exclude: needs another document";
  return item.levels?.find((entry) => entry.level === level)?.name ?? "Selected rubric level";
}

function SignalResolutionCard({ item, checkRunId, onResolved, onEditingChange, onEvidenceSelected, onOpenEvidence }: {
  item: EscalatedItemOut;
  checkRunId: number;
  onResolved: (message: string) => void;
  onEditingChange?: (editing: boolean) => void;
  onEvidenceSelected?: (criterionId: number, evidence: EvidenceItem, index: number) => void;
  onOpenEvidence?: (criterionId: number, evidence: EvidenceItem, index: number) => void;
}) {
  const [pending, setPending] = useState<{ resolution: EscalationResolution; level?: number }>();
  const [reason, setReason] = useState("");
  const [attempted, setAttempted] = useState(false);
  const resolve = useResolveEscalation(checkRunId);
  const ordinaryGrade = pending !== undefined && ["mark_level", "mark_pass", "mark_fail"].includes(pending.resolution);
  const requiresReason = pending !== undefined && !ordinaryGrade;
  const reasonInvalid = attempted && requiresReason && reason.trim().length < RESOLUTION_REASON_MIN_LENGTH;
  const reasonErrorId = `signal-resolution-reason-${item.check_result_id}`;
  const criterionHeadingRef = useRef<HTMLHeadingElement>(null);
  const pendingHeadingRef = useRef<HTMLHeadingElement>(null);
  useLayoutEffect(() => {
    if (ordinaryGrade) pendingHeadingRef.current?.focus();
  }, [ordinaryGrade, pending?.resolution, pending?.level]);
  const evidence = item.evidence ?? [];
  const evidenceSelection = useRef(onEvidenceSelected);
  evidenceSelection.current = onEvidenceSelected;
  useEffect(() => {
    const initial = item.evidence?.findIndex(entry => criterionAnchorRegion(entry.anchor) !== null) ?? -1;
    if (initial >= 0) evidenceSelection.current?.(item.criterion_id, item.evidence![initial], initial);
    // Select once per criterion entry. Evidence browsing and form renders must
    // not reset a manuscript page the instructor has deliberately browsed.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [item.check_result_id]);
  function openEvidence(index: number) {
    evidenceSelection.current?.(item.criterion_id, evidence[index], index);
    onOpenEvidence?.(item.criterion_id, evidence[index], index);
  }

  function choose(resolution: EscalationResolution, level?: number) {
    onEditingChange?.(true);
    setPending({ resolution, level });
    setReason("");
    setAttempted(false);
  }

  function cancel() {
    onEditingChange?.(false);
    setPending(undefined);
    // BUG-145 (UX review review): Confirm already moves focus to the
    // section heading on success; Cancel dropped focus to <body> instead
    // of returning it anywhere, losing a keyboard/screen-reader user's
    // place. This heading is outside the pending/options conditional, so
    // it's already mounted -- no need to wait for the next render.
    criterionHeadingRef.current?.focus();
  }

  function confirm() {
    setAttempted(true);
    if (!pending || (requiresReason && reason.trim().length < RESOLUTION_REASON_MIN_LENGTH)) return;
    resolve.mutate(
      {
        checkResultId: item.check_result_id,
        resolution: pending.resolution,
        reason: reason.trim(),
        level: pending.level,
      },
      {
        onSuccess: (out) => {
          onEditingChange?.(false);
          const result = out.report.results.find((row) => row.criterion_id === item.criterion_id);
          const resultLabel = result?.level?.name
            ?? (out.outcome === "not_applicable" ? "excluded because another document is needed" : OUTCOME_COPY[out.outcome as ResultRowCommon["outcome"]]?.label ?? out.outcome);
          onResolved(`Criterion resolved as ${resultLabel}. The readiness band has been recalculated.`);
          setPending(undefined);
        },
      },
    );
  }

  // BUG-132: all four decision buttons below carried no programmatic link
  // to the criterion quote/vote summary/reason/evidence sitting visually
  // above them -- a screen reader user who tabs directly to a button (a
  // normal navigation pattern, not just top-to-bottom reading) heard only
  // the button's own label, none of the context explaining WHY this
  // criterion needs review, and for an injection-suspected item
  // specifically, none of the warning that the AI's own agreement here
  // can't be trusted. Stable ids + a conditional aria-describedby list
  // (only the blocks that actually render, in document order) fixes this
  // on every button, not just the group container -- a container-level
  // aria-label isn't reliably announced on focus entering a nested
  // interactive element, but a button's own aria-describedby always is.
  const voteId = `signal-resolution-vote-${item.check_result_id}`;
  const reasonId = `signal-resolution-context-reason-${item.check_result_id}`;
  const injectionId = `signal-resolution-injection-${item.check_result_id}`;
  const unverifiedId = `signal-resolution-unverified-${item.check_result_id}`;
  const describedBy = [
    voteId,
    item.reason && reasonId,
    item.injection_suspected && item.injection_matched_snippet && injectionId,
    item.unverified_evidence && item.unverified_evidence.length > 0 && unverifiedId,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <article className="signal-resolution-card">
      <div className="signal-resolution-card__context">
        <div><p className="signal-section-kicker">{item.review_basis === AI_REVIEW_BASIS ? "Recorded AI grade" : "Criterion requiring judgment"}</p><h3 ref={criterionHeadingRef} tabIndex={-1}>{item.criterion_text}</h3></div>
        <p id={voteId} className="signal-resolution-card__vote">{voteSummary(item)}</p>
        <CriterionAssessment checkRunId={checkRunId} criterionId={item.criterion_id} title={item.criterion_text} reasoning={item.reason} reasoningId={reasonId} evidence={evidence} />
        {item.injection_suspected && item.injection_matched_snippet && (
          <blockquote id={injectionId} className="signal-unverified-evidence"><strong>Text addressed at an automated grader</strong><span>“{item.injection_matched_snippet}”</span></blockquote>
        )}
        {item.unverified_evidence && item.unverified_evidence.length > 0 && (
          <div id={unverifiedId} className="signal-unverified-evidence"><strong>Could not verify against the source</strong>{item.unverified_evidence.map((quote) => <blockquote key={quote}>“{quote}”</blockquote>)}</div>
        )}
      </div>

      <div className="signal-resolution-card__judgment">
      <h4>Your grade</h4>
      {/* BUG-145 (UX review review, round 2): this region must be mounted
          BEFORE it has content, not created already-populated in the same
          render as `pending` becoming truthy -- a live region announced
          from birth is not reliably picked up by assistive tech (Soueidan,
          "Accessible Notifications with ARIA Live Regions"), and the
          textarea's autoFocus below moves focus past it on the same
          render regardless. Empty and out of layout flow (`:empty` in
          signal.css) whenever nothing is pending. */}
      <div className="signal-resolution-form__pending" role="status" aria-live="polite">
        {pending && (
          <>
            <p className="signal-section-kicker">Step 2 of 2: nothing is saved yet</p>
            <h5 ref={pendingHeadingRef} tabIndex={-1}>{resolutionLabel(item, pending.resolution, pending.level)}</h5>
          </>
        )}
      </div>

      {!pending ? (
        <div className="signal-resolution-options" aria-label={`Resolve: ${item.criterion_text}`}>
          {item.levels && item.levels.length > 0
            ? item.levels.map((level) => <Button key={level.level} variant="secondary" aria-describedby={describedBy} onClick={() => choose("mark_level", level.level)}>{level.name}</Button>)
            : <><Button variant="secondary" aria-describedby={describedBy} onClick={() => choose("mark_pass")}>Meets criterion</Button><Button variant="secondary" aria-describedby={describedBy} onClick={() => choose("mark_fail")}>Does not meet</Button></>}
          <details className="signal-exclusion-help"><summary>Cannot judge from this manuscript?</summary><p>If this criterion genuinely requires a different document, you can exclude it from this readiness calculation. It will remain ungraded. To wait for more evidence, leave it unresolved instead.</p><Button variant="quiet" aria-describedby={describedBy} onClick={() => choose("needs_document")}>Exclude: needs another document</Button></details>
          {/* BUG-170: `verdict_unrecognized` means accepting this verdict
              is guaranteed to 409 server-side (the passes agreed on a
              string that still doesn't name any of this criterion's own
              levels) -- offering the button anyway would be the exact
              "offers to accept a verdict it just told you it cannot
              recognise" incoherence this ticket named. `item.reason`
              already explains why, so removing the button loses no
              information. */}
          {item.ai_majority_verdict !== null && !item.verdict_unrecognized && <Button variant={item.review_reason === "injection_suspected" ? "quiet" : "secondary"} aria-describedby={describedBy} onClick={() => choose("accept_majority")}>Accept AI suggestion: {item.ai_majority_verdict}</Button>}
        </div>
      ) : (
        <div className="signal-resolution-form">
          <label htmlFor={`signal-resolution-input-${item.check_result_id}`}>{requiresReason ? "Reason (required)" : "Comment (optional)"}</label><textarea id={`signal-resolution-input-${item.check_result_id}`} autoFocus={requiresReason} rows={requiresReason ? 3 : 2} value={reason} aria-invalid={reasonInvalid || undefined} aria-describedby={reasonInvalid ? reasonErrorId : undefined} onChange={(event) => { setReason(event.target.value); if (attempted) setAttempted(false); }} />
          <p className="signal-field-hint">{pending.resolution === "needs_document" ? "This criterion will be ungraded and excluded from the readiness calculation. " : ""}{requiresReason ? "This reason is saved in your instructor report and PDF export. It is not included in a shared report." : "Any comment you add is saved in your instructor report and PDF export. It is not included in a shared report."}</p>
          {reasonInvalid && <p id={reasonErrorId} role="alert" className="signal-field-error">{reason.trim() ? `Enter at least ${RESOLUTION_REASON_MIN_LENGTH} characters.` : "Enter a reason before confirming."}</p>}
          {resolve.error && <Alert title="Could not save this resolution" tone="error" role="alert">{resolve.error instanceof Error ? resolve.error.message : "Try again."}</Alert>}
          <div><Button variant="secondary" disabled={resolve.isPending} onClick={cancel}>Cancel</Button><Button variant="brand" busy={resolve.isPending} onClick={confirm}>{ordinaryGrade ? "Save grade" : "Confirm resolution"}</Button></div>
        </div>
      )}
      </div>
      <CriterionEvidencePacket checkRunId={checkRunId} criterionId={item.criterion_id} title={item.criterion_text} reasoning={item.reason} reasoningId={reasonId} evidence={evidence} onOpenEvidence={onOpenEvidence ? openEvidence : undefined} includeAssessment={false} />
    </article>
  );
}

export function SignalEscalatedPanel({ checkRunId, focused = false, onEvidenceSelected, onOpenEvidence, onEditingChange }: { checkRunId: number; focused?: boolean; onEvidenceSelected?: (criterionId: number, evidence: EvidenceItem, index: number) => void; onOpenEvidence?: (criterionId: number, evidence: EvidenceItem, index: number) => void; onEditingChange?: (editing: boolean) => void }) {
  const { data: items, isPending, isError, refetch } = useEscalatedItems(checkRunId);
  const [announcement, setAnnouncement] = useState("");
  const [activeId, setActiveId] = useState<number>();
  const [editing, setEditing] = useState(false);
  const [editedId, setEditedId] = useState<number>();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const announcementRef = useRef<HTMLParagraphElement>(null);
  const hasItems = Boolean(items?.length);
  const aiGradeCount = items?.filter(item => item.review_basis === AI_REVIEW_BASIS).length ?? 0;
  const ungradedCount = (items?.length ?? 0) - aiGradeCount;
  const activeIndex = Math.max(0, items?.findIndex(item => item.check_result_id === activeId) ?? 0);
  const visibleItems = focused ? items?.slice(activeIndex, activeIndex + 1) : items;

  useEffect(() => {
    if (editing && editedId !== undefined && items && !items.some(item => item.check_result_id === editedId)) {
      setEditing(false); setEditedId(undefined); onEditingChange?.(false);
      setAnnouncement("This criterion was updated elsewhere. Your unsaved grade was not submitted. Review the latest record before continuing.");
      headingRef.current?.focus();
    }
  }, [editing, editedId, items, onEditingChange]);

  useEffect(() => {
    if (focused && hasItems) headingRef.current?.focus();
  }, [focused, hasItems, checkRunId]);

  useEffect(() => {
    if (!hasItems && announcement) announcementRef.current?.focus();
  }, [announcement, hasItems]);

  if (isPending) return <div role="status" aria-busy="true" className="signal-desk-loading"><span>Loading criteria that need review…</span><i /><i /></div>;
  if (isError && !items) return <Alert title="Could not load criteria needing review" tone="error" role="alert"><Button variant="secondary" onClick={() => refetch()}>Try again</Button></Alert>;

  return (
    <section id="review-criteria" tabIndex={-1} data-tour="escalated-panel" className="signal-review-section signal-review-section--attention" aria-labelledby="escalated-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Rubric review</p><h2 id="escalated-heading" ref={headingRef} tabIndex={-1}>{aiGradeCount ? "Review recorded AI grades" : "Criteria needing your judgment"}</h2></div><span>{aiGradeCount ? `${aiGradeCount} AI grades ready for review; ${ungradedCount} ungraded` : `${ungradedCount} unresolved`}</span></div>
      <p className="signal-review-section__intro">{aiGradeCount ? "These AI grades are already recorded and included in readiness. You can keep them, or choose a level and save your own grade. Opening evidence or cancelling does not change a grade." : "Choose a grade, add a comment if useful, then save. Exclusions and accepting an AI suggestion still require a reason. Each saved judgment brings the report closer to the final decision."}</p>
      {isError && <Alert title="Could not refresh these criteria" tone="error" role="alert">Your unsaved grade is still here. <Button variant="secondary" onClick={() => refetch()}>Try again</Button></Alert>}
      <p ref={announcementRef} tabIndex={-1} aria-live="polite" className="signal-live-region">{announcement}</p>
      {focused && hasItems && <nav className="signal-criterion-navigation" aria-label={aiGradeCount ? "Review recorded grades" : "Unresolved criteria"}><Button variant="quiet" aria-label="Previous criterion" disabled={editing || activeIndex === 0} onClick={() => { setActiveId(items![activeIndex - 1].check_result_id); headingRef.current?.focus(); }}>Previous</Button><span>{editing ? "Save or cancel to continue" : `${activeIndex + 1} of ${items?.length} ${aiGradeCount ? "available" : "remaining"}`}</span><Button variant="quiet" aria-label="Next criterion" disabled={editing || activeIndex === (items?.length ?? 0) - 1} onClick={() => { setActiveId(items![activeIndex + 1].check_result_id); headingRef.current?.focus(); }}>Next</Button></nav>}
      {hasItems
        ? <div className="signal-resolution-list">{visibleItems?.map((item) => <SignalResolutionCard key={item.check_result_id} item={item} checkRunId={checkRunId} onEvidenceSelected={onEvidenceSelected} onOpenEvidence={onOpenEvidence} onEditingChange={value => { if (focused) { setEditing(value); setEditedId(value ? item.check_result_id : undefined); } onEditingChange?.(value); }} onResolved={(message) => { if (focused) setActiveId(items?.[activeIndex + 1]?.check_result_id ?? items?.[Math.max(0, activeIndex - 1)]?.check_result_id); setAnnouncement(message); headingRef.current?.focus(); }} />)}</div>
        : <Alert title="No unresolved criteria" tone="success">Every criterion has a recorded outcome. Continue to the integrity signals.</Alert>}
    </section>
  );
}

export function SignalFlagsPanel({
  checkRunId,
  unresolvedHighFlagCount,
  originalityScope,
  report,
}: {
  checkRunId: number;
  unresolvedHighFlagCount: number;
  originalityScope?: OriginalityScopeValue | null;
  report?: ReportOut;
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
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Second review task</p><h2 id="flags-heading" tabIndex={-1}>Integrity signals</h2></div><span><span>{openFindingCount} open finding{openFindingCount === 1 ? "" : "s"}</span><small> across all checks</small></span></div>
      <p className="signal-review-section__intro">These are possible inconsistencies, not accusations. Open a signal to inspect its excerpt, source anchor, and available instructor action.</p>
          <SignalFlagList
            flags={flags ?? []}
            showActions
            initialView={initialView}
            trackViewed
            viewedIds={viewedIds}
            report={report}
            originalityScope={originalityScope}
          />
    </section>
  );
}

export function SignalPublicFlags({
  flags,
  originalityScope,
  renderEvidence,
}: {
  renderEvidence?: (flag: FlagSummaryOut) => ReactNode;
  flags: FlagSummaryOut[];
  originalityScope?: OriginalityScopeValue | null;
}) {
  const findingCount = clusterFlagFindings(flags).length;
  return (
    <section id="integrity-signals" tabIndex={-1} className="signal-review-section" aria-labelledby="flags-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Integrity record</p><h2 id="flags-heading" tabIndex={-1}>Possible inconsistencies</h2></div><span>{findingCount} finding{findingCount === 1 ? "" : "s"}</span></div>
      <p className="signal-review-section__intro">These are possible inconsistencies, not accusations. This shared view shows the bounded evidence the instructor chose to share; it does not expose the private Audit trail.</p>
      <SignalFlagList flags={flags} showActions={false} initialView="all" originalityScope={originalityScope} renderEvidence={renderEvidence} />
    </section>
  );
}

export function SignalCriteriaResults({ results, checkRunId, publicView = false, onOpenEvidence }: {
  results: ResultRowCommon[];
  checkRunId: number;
  publicView?: boolean;
  onOpenEvidence?: (criterionId: number, evidence: EvidenceItem, index: number) => void;
}) {
  return (
    <section className="signal-review-section" aria-labelledby="criteria-results-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Criterion record</p><h2 id="criteria-results-heading" tabIndex={-1}>{publicView ? "All criteria" : "All assessed criteria"}</h2></div><span>{results.length} total</span></div>
      <p className="signal-review-section__intro">Each row shows the recorded outcome and the evidence available for checking it. Importance is shown as a band, never as a judgment percentage.</p>
      {results.length ? <ol className="signal-result-list">{results.map((row, index) => {
        const outcome = row.level && (row.outcome === "passed" || row.outcome === "failed")
          ? { label: "Rubric level recorded", tone: "neutral" }
          : OUTCOME_COPY[row.outcome];
        return <li key={row.criterion_id} className="signal-result-card"><div className="signal-result-card__top"><span className="signal-result-card__number">{index + 1}</span><span className={`signal-outcome signal-outcome--${outcome.tone}`}>{outcome.label}</span><span className={`signal-importance signal-importance--${row.weight_importance === "med" ? "medium" : row.weight_importance}`}>{row.weight_importance === "med" ? "Medium" : `${row.weight_importance.slice(0, 1).toUpperCase()}${row.weight_importance.slice(1)}`} importance</span></div><h3>{row.text}</h3>{row.level && <p className="signal-level-result"><strong>Recorded level:</strong> {row.level.name}</p>}{row.basis === AI_REVIEW_BASIS && <p className="signal-resolution-note"><strong>AI review complete.</strong> Review prepared in this session.</p>}{row.resolution && <p className="signal-resolution-note"><strong>{row.resolution.type === "needs_document" ? "Excluded by instructor." : "Grade recorded by instructor."}</strong>{row.resolution.reason && <> {row.resolution.reason}</>}</p>}{/* BUG-065: this summary repeats verbatim once per row -- a screen-reader
                links/buttons list can't tell them apart without a per-criterion
                accessible name, same fix already applied to the flag "Review
                evidence" links and the escalation resolve-button groups. */}
              <details><summary aria-label={`Review evidence and reasoning: ${row.text}`}>Review evidence and reasoning</summary><CriterionEvidencePacket checkRunId={checkRunId} criterionId={row.criterion_id} title={row.text} reasoning={row.reasoning || row.reason} evidence={row.evidence} previews={!publicView} onOpenEvidence={!publicView && onOpenEvidence ? index => onOpenEvidence(row.criterion_id, row.evidence[index], index) : undefined} />{!publicView && !row.evidence.length && row.anchor && <ActionLink to={`/report/${checkRunId}/document?anchor=${encodeURIComponent(row.anchor)}`} variant="quiet">Open recorded page (no supporting quotation saved)</ActionLink>}</details></li>;
      })}</ol> : <div className="signal-desk-empty"><h3>No criterion results yet</h3><p>This report has no assessed criterion rows.</p></div>}
    </section>
  );
}
