import { AUTOMATIC_GRADE_COPY, automaticGradeCaution, automaticGradeChip, automaticGradeLines, automaticGradePasses } from "../domain/automaticGrade";
import { RecordedEvidencePage } from "./RecordedEvidencePage";
import { IntegrityModuleNavigation, selectedIntegrityModule, type IntegrityKind } from "./IntegrityModules";
import { ActionAssessmentContext, ReportAssessmentContext } from "./ReportAssessmentContext";
import { criterionAssessmentDetails } from "../domain/criterionAssessment";
import { CriterionSourceCoverage } from "./CriterionSourceCoverage";
import { RuleFactsDetails } from "./RuleFactsDetails";
import { RuleScopeNotice } from "./RuleScopeNotice";
import { coverageItems } from "./reportCoverage";
import { workspaceStepPath } from "./reviewStep";
import { pendingAssessmentRow } from "./pendingAssessmentRow";
import { clearPdfReading } from "../document/usePdfPageText";
import { recoverableReadError } from "./reportReadState";
import { reviewDeskDestination } from "../routing/reviewDeskReturn";
import { useContext, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
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
import { CRITERION_EXCLUSION_COPY, CRITERION_REVIEW_COPY, PLAIN_REASON_WORDING, REVIEW_DRAFT_COPY, criterionReviewNotices, resolutionRequiresReason, recordedInstructorComment } from "../domain/criterionReview";
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
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { clusterFlagFindings, type FlagFindingCluster } from "./flagClusters";
import { useEscalatedItems, useFlags } from "./useReport";
import { ReviewContext, useCriterionReviewScope } from "./criterionReviewContext";
import { SavedCriterionDestination, SavedJudgmentAccess } from "./SavedJudgmentAccess";
import { recordedGradeLabel, criterionRecordId, atLeastSavedJudgment, SAVED_CRITERION_PARAM } from "./savedJudgment";
import { RecordedJudgmentTools } from "./RecordedJudgmentTools";
import { InWorkspaceBar } from "../document/WorkspaceBar";

// Some recorded notes are two sentences joined with ".;" when stored.
function readableNote(text: string) {
  const joined = text.replace(/\.\s*;\s*/g, ". ");
  return PLAIN_REASON_WORDING.reduce((out, [stored, plain]) => out.split(stored).join(plain), joined);
}
import { EmptyState } from "../ui/EmptyState";
import { isLegacyUndecided } from "../domain/undecidedPairs";
import { UndeterminedPairs } from "./UndeterminedPairs";
import { MODULE_EMPTY_STATE, moduleEmptyState } from "./moduleEmptyState";
import { ComparisonLeadList } from "./ComparisonLeadList";
import { comparisonLeadCollection } from "./comparisonLeadCollection";
import { resultDisplay } from "./ResultsTable";
import { useExplicitActionFocus } from "../routing/useExplicitActionFocus";
import { criterionReviewSearch, selectedCriterionId, SELECTED_CRITERION_PARAM } from "./criterionReviewNavigation";
import { useEvidenceManifest } from "./useEvidenceSource";
import type { OpenEvidenceSource } from "./evidenceTypes";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
import { RelatedFindings } from "./RelatedFindings";
import { readinessExclusionLine } from "../domain/readinessExclusion";
import { BulkResolveDialog, BulkSelectionBar } from "./BulkFindingReview";
import { findingSubject, findingTitle } from "../domain/findingSummary";
import { BULK_REVIEW_COPY } from "../domain/bulkReviewCopy";
import type { BulkAction } from "./useBulkResolve";
import { OriginalityCoverageNotice } from "./OriginalityCoverageNotice";
import { originalityCoverageNote } from "./originalityCoverage";
import { CitationRecencySummary } from "./CitationRecencySummary";
import { isRecencyNote, isUncountedRecencyNote, notCountedLabel, sourceAgeNotesLabel } from "../domain/findingSummary";

interface FindingSelection {
  isSelected: (cluster: FlagFindingCluster) => boolean;
  toggle: (cluster: FlagFindingCluster) => void;
  selectMany: (clusters: FlagFindingCluster[]) => void;
  selectAllInCheck: () => void;
}

const GRADE_LIST_COPY = {
  waiting: "Waiting for your judgment",
  waitingNote: "No grade has been accepted yet. Grade it in step 1, Review criteria.",
  gradeIt: "Grade this criterion",
} as const;

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

// Open means neither dismissed nor confirmed: confirming a finding as a real
// issue resolves it, though it keeps counting for readiness.
function isOpenFinding(flag: FlagSummaryOut): boolean {
  return !flag.overridden && !flag.finding_confirmed;
}

function flagMatchesView(flag: FlagSummaryOut, view: FlagView): boolean {
  if (view === "all") return true;
  if (view === "resolved") return !isOpenFinding(flag);
  if (view === "open") return isOpenFinding(flag);
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
  const unresolved = flags.filter(isOpenFinding);
  const viewed = unresolved.filter((flag) => viewedIds.has(flag.id));
  if (unresolved.length === 0 || viewed.length === 0) return null;
  if (unresolved.length === 1) return "Opened this location";
  if (viewed.length === unresolved.length) return `Opened all ${unresolved.length} open locations`;
  return `Opened ${viewed.length} of ${unresolved.length} open locations`;
}

// A finding is open for bulk review while it is neither dismissed nor
// confirmed; resolved findings are changed one at a time.
function isOpenForBulk(cluster: FlagFindingCluster) {
  if (cluster.flags.every(isUncountedRecencyNote)) return false;
  return cluster.flags.some((flag) => !flag.overridden && !flag.finding_confirmed);
}

// V-092: source-age notes a format does not count, listed beside the
// findings with the same cards but outside the findings' counts and bulk
// actions.
function SourceAgeNotes({ flags, viewedIds = EMPTY_VIEWED, showActions = true }: { flags: FlagSummaryOut[]; viewedIds?: ReadonlySet<number>; showActions?: boolean }) {
  const clusters = clusterFlagFindings(flags);
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  const headingId = useId();
  return <section className="signal-source-age-notes" aria-labelledby={headingId}>
    <h3 id={headingId}>{SOURCE_AGE_NOTES_HEADING} ({clusters.length})</h3>
    <p className="signal-field-hint">{SOURCE_AGE_NOTES_HINT}</p>
    <ul className="signal-flag-list">
      {clusters.map((cluster) => <SignalFindingCard key={cluster.key} cluster={cluster} expanded={open.has(cluster.key)} showActions={showActions}
        showResolutionSummary viewedIds={viewedIds}
        onToggle={() => setOpen((current) => { const next = new Set(current); if (next.has(cluster.key)) next.delete(cluster.key); else next.add(cluster.key); return next; })} />)}
    </ul>
  </section>;
}

const SOURCE_AGE_NOTES_HEADING = "Source-age notes";
const SOURCE_AGE_NOTES_HINT = "Notes on references older than this format's recency window. They do not count toward readiness and are not findings.";

function SelectFinding({ cluster, selection }: { cluster: FlagFindingCluster; selection?: FindingSelection }) {
  if (!selection || !isOpenForBulk(cluster)) return null;
  const first = cluster.flags[0];
  // Named on the input itself: the excerpt it repeats is already on the card.
  return <label className="signal-bulk-check">
    <input type="checkbox" checked={selection.isSelected(cluster)} onChange={() => selection.toggle(cluster)}
      aria-label={`Select finding: ${findingTitle(first)}, ${instructorAnchor(first.page_anchor)}: ${findingSubject(first)}`} />
  </label>;
}

const SELECTION_KEY = "veridical.bulk-selection.v1";

// The selection is kept for this browser tab, so checking a finding's
// evidence and coming back does not lose it.
function useStoredSelection(checkRunId: number) {
  const key = `${SELECTION_KEY}.${checkRunId}`;
  const [ids, setIds] = useState<ReadonlySet<number>>(() => {
    try {
      const parsed: unknown = JSON.parse(window.sessionStorage.getItem(key) ?? "[]");
      return new Set(Array.isArray(parsed) ? parsed.filter((id): id is number => Number.isSafeInteger(id)) : []);
    } catch {
      return new Set();
    }
  });
  useEffect(() => {
    try {
      if (ids.size) window.sessionStorage.setItem(key, JSON.stringify([...ids]));
      else window.sessionStorage.removeItem(key);
    } catch {
      // Storage unavailable: the selection lasts only while this page is open.
    }
  }, [ids, key]);
  return [ids, setIds] as const;
}

function SignalFindingCard({
  cluster,
  expanded,
  showActions,
  showResolutionSummary,
  viewedIds,
  onToggle,
  selection,
}: {
  selection?: FindingSelection;
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
          <SelectFinding cluster={cluster} selection={selection} />
          <span className={`signal-severity signal-severity--${first.affects_readiness === false ? "low" : first.severity}`}>{first.affects_readiness === false ? notCountedLabel(first) : SEVERITY_LABEL[first.severity]}</span>
          {!first.overridden && viewedIds.has(first.id) && (
            <span className="signal-viewed-state"><ViewedIcon />Opened</span>
          )}
          {first.overridden && <span className="signal-resolution-state">Resolved by instructor</span>}
          {!first.overridden && first.finding_confirmed && <span className="signal-resolution-state">Confirmed by instructor</span>}
          {first.confirmed_citation_source && <span className="signal-resolution-state">Source confirmed</span>}
        </div>
        <h3 id={headingId} tabIndex={-1}>{problemLabel(first.problem_kind) ?? first.criterion_text ?? "Possible inconsistency"}</h3>
        {readinessExclusionLine(first) && <p className="signal-field-hint">{readinessExclusionLine(first)}</p>}
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
        <SelectFinding cluster={cluster} selection={selection} />
        <span className={`signal-severity signal-severity--${first.affects_readiness === false ? "low" : severity}`}>{first.affects_readiness === false ? notCountedLabel(first) : SEVERITY_LABEL[severity]}</span>
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
      {readinessExclusionLine(first) && <p className="signal-field-hint">{readinessExclusionLine(first)}</p>}
      {flags.some((flag) => flag.citation_metadata_review_warning) && <p className="signal-field-hint"><strong>{CITATION_HISTORY_TITLE}</strong></p>}
      {first.check_kind === "originality_reuse" && first.matched_ref != null && (
        <p className="signal-flag-finding-context">Possible match with archived manuscript #{first.matched_ref}</p>
      )}
      {excerptsMatch
        ? isManuscriptQuote(first)
          ? <blockquote>“{systemFindingCopy(first.evidence_excerpt, first.problem_kind, "evidence")}”</blockquote>
          : <p className="signal-flag-finding-context">{systemFindingCopy(first.evidence_excerpt, first.problem_kind, "evidence")}</p>
        : <p className="signal-flag-finding-context">{isRecencyNote(first) ? `${flags.length} references are older than the recency window. Each one is listed below with its year.` : `${flags.length} manuscript locations point to this possible finding, each with its own excerpt. Review one location below, or open all ${flags.length} to compare them.`}</p>}
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
              {/* When every location quotes the same passage, the card above
                  already shows it; repeating it per location only adds length. */}
              {!excerptsMatch && (isManuscriptQuote(flag)
                ? <blockquote>“{systemFindingCopy(flag.evidence_excerpt, flag.problem_kind, "evidence")}”</blockquote>
                : <p className="signal-flag-finding-context">{systemFindingCopy(flag.evidence_excerpt, flag.problem_kind, "evidence")}</p>)}
              <div className="signal-flag-location__meta">
                <strong>Location {locationIndex + 1}</strong>
                {flag.cited_text && <span>Citation: {flag.cited_text}</span>}
                <span>{instructorAnchor(flag.page_anchor)}</span>
                {mixedSeverity && <span className={`signal-severity signal-severity--${flag.severity}`}>{SEVERITY_LABEL[flag.severity]}</span>}
                {!flag.overridden && viewedIds.has(flag.id) && (
                  <span className="signal-viewed-state"><ViewedIcon />Opened</span>
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
  selection,
}: {
  selection?: FindingSelection;
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
          const openClustersAll = clusterFlagFindings(flags.filter(isOpenFinding));
          const openTotal = openClustersAll.length;
          // Opened means every location was opened, as the bulk dialog counts it.
          const openViewed = openClustersAll.filter((cluster) =>
            cluster.flags.every((flag) => viewedIds.has(flag.id))
          ).length;
          return openTotal > 0 ? (
            // UX review finding (BUG-167 review): another tab marking a
            // flag viewed updates this count via `useViewedFlagIds`'s own
            // cross-tab `storage` listener with nothing announced to a
            // screen reader in THIS tab -- same `role`/`aria-live` as its
            // sibling status line above.
            <p className="signal-field-hint" role="status" aria-live="polite">
              You have opened {openViewed} of {openTotal} open findings.
            </p>
          ) : null;
        })()}
        <p className="signal-field-hint">
          One finding can contain both open and resolved locations, so those two filter counts may overlap. All counts each finding once.
        </p>
        {selection && visible.length > 0 && (() => {
          const openInCheck = canonicalClusters.filter(isOpenForBulk);
          const shownKeys = new Set(visible.map((cluster) => cluster.key));
          const notShown = openInCheck.filter((cluster) => !shownKeys.has(cluster.key)).length;
          return openInCheck.length > 0 && <div className="signal-control-cluster signal-bulk-select">
            <Button variant="quiet" onClick={() => selection.selectMany(visible)}>{BULK_REVIEW_COPY.selectPage}</Button>
            <Button variant="quiet" onClick={selection.selectAllInCheck}>{BULK_REVIEW_COPY.selectCheck(openInCheck.length, notShown)}</Button>
          </div>;
        })()}
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
              selection={selection}
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
  const navigate = useNavigate();
  const review = useCriterionReviewScope(checkRunId);
  const canonical = currentItems && review.assessmentCurrent?.()
    ? pendingAssessmentRow(review.assessment, item, checkRunId) : undefined;
  const assessment = canonical ? criterionAssessmentDetails(canonical, false) : undefined;
  const currentAssessment = review.assessmentCurrent?.() ? review.assessment : undefined;
  const manifestQuery = useEvidenceManifest(checkRunId, currentAssessment?.assessment_revision);
  const manifest = canonical && currentItems && currentAssessment && manifestQuery.isSuccess && manifestQuery.fetchStatus === "idle"
    && manifestQuery.data.assessment_revision === currentAssessment.assessment_revision ? manifestQuery.data : undefined;
  const evidenceContext = {
    title: item.criterion_text,
    explanation: item.reasoning ?? null,
    explanationLabel: assessment?.explanationLabel ?? "Recorded explanation:",
    recordedNote: item.reason,
    references: manifest?.references.filter(ref => ref.criterion_id === item.criterion_id && ref.role === "criterion_evidence") ?? [],
  };
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
    const origin = new URLSearchParams(location.search);
    if (fullReport) origin.set(SELECTED_CRITERION_PARAM, String(item.criterion_id));
    const returnPath = fullReport ? `${location.pathname}?${origin}` : location.pathname + location.search;
    const state = fullReport ? {
      ...(location.state && typeof location.state === "object" && !Array.isArray(location.state)
        ? Object.fromEntries(Object.entries(location.state).filter(([key]) => key !== "criterionRecordReturn" && key !== "pendingCriterionReturn")) : {}),
      pendingCriterionReturn: { kind: "pending", returnPath, elementId: actionId, checkResultId: item.check_result_id },
    } : undefined;
    return { target, state, returnPath };
  };
  const followPendingSource = (event: React.MouseEvent<HTMLAnchorElement>, anchor: string, actionId: string) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (onOpenSource) { event.preventDefault(); onOpenSource(anchor, actionId); return; }
    const { target, state, returnPath } = pendingSource(anchor, actionId);
    if (state) {
      event.preventDefault();
      // Bind the explicit source activation to the criterion actually on screen.
      // Merely displaying the first criterion never creates navigation or focus intent.
      void navigate(`${returnPath}#${actionId}`, { replace: true, state: location.state });
    }
    rememberRouteReturnFocus(returnPath, target, actionId, { fallbackElementId: [`signal-resolution-heading-${item.check_result_id}`, "escalated-heading", "readiness-report-heading"] });
    if (state) void navigate(target, { state });
  };
  const coverageSourceAction = (anchor: string, slot: number) => {
    const actionId = `signal-resolution-coverage-${checkRunId}-${item.check_result_id}-${slot}`;
    const { target, state } = pendingSource(anchor, actionId);
    return <ActionLink id={actionId} state={state} to={target} variant="quiet" onClick={event => followPendingSource(event, anchor, actionId)}>Open in manuscript: {instructorAnchor(anchor)}</ActionLink>;
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
  const collapseRecordedReason = reviewNotices.some(notice => notice.kind === "support")
    && !reviewNotices.some(notice => notice.kind === "source_instructions") && !item.unverified_evidence?.length;
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
        <strong id={reviewStateId}>{CRITERION_REVIEW_COPY.judgmentNeeded}</strong>
        <p>{item.evidence?.length ? CRITERION_REVIEW_COPY.reviewSavedPassages : CRITERION_REVIEW_COPY.reviewManuscript}</p>
        {reviewNotices.filter(notice => notice.kind !== "support").map((notice) => <p key={notice.kind} id={noticeId(notice.kind)} className="signal-resolution-card__vote">{notice.text}</p>)}
        {!collapseRecordedReason && !sharedReasonId && item.reason && <p id={reasonId}>{readableNote(item.reason)}</p>}
        {showInjectionSnippet && (
          <blockquote className="signal-unverified-evidence"><strong id={injectionId}>Text addressed at an automated grader</strong><span>“{item.injection_matched_snippet}”</span></blockquote>
        )}
        {item.unverified_evidence && item.unverified_evidence.length > 0 && (() => {
          // A quote that is in the manuscript but was already another
          // criterion's evidence is not a source mismatch; say which it is.
          const codes = item.reason_codes ?? review?.assessment?.results?.find((row) => row.criterion_id === item.criterion_id)?.reason_codes ?? [];
          const reusedOnly = codes.includes("evidence_already_used") && !codes.includes("evidence_not_located");
          return <div className="signal-unverified-evidence">
            <strong id={unverifiedId}>{reusedOnly ? "Quotes already used for another criterion" : "Could not verify against the source"}</strong>
            {reusedOnly ? <p>These passages are in the manuscript, but each was already used as evidence for a different criterion, so it was not counted again here.</p>
              : codes.includes("evidence_already_used") && <p>Some of these passages are in the manuscript but were already used for another criterion; the others could not be located in the source.</p>}
            {item.unverified_evidence!.map((quote) => <blockquote key={quote}>“{quote}”</blockquote>)}
          </div>;
        })()}
        {assessment?.ruleScopeUnconfirmed && <RuleScopeNotice />}
        {assessment?.sourceCoverage && <CriterionSourceCoverage value={assessment.sourceCoverage} sourceAction={coverageSourceAction} firstSlot={item.evidence?.length ?? 0} criterionText={item.criterion_text} />}
        {reportHasLimits && <p className="signal-field-hint"><ActionLink to={`/report/${checkRunId}?section=limits`} variant="quiet">See assessment limits on the readiness report</ActionLink></p>}
        {assessment?.ruleFacts && <RuleFactsDetails facts={assessment.ruleFacts} />}
        {item.reasoning?.trim() && <div className="signal-resolution-context-note"><h4>Assessment and explanation</h4><strong>{assessment?.explanationLabel ?? "Recorded explanation:"}</strong><p>{item.reasoning}</p></div>}
        {(item.votes.length > 0 || reviewNotices.some(notice => notice.kind === "support") || (collapseRecordedReason && Boolean(item.reason)) || Boolean(sharedReasonId)) && <>
        <Button variant="quiet" aria-expanded={detailsOpen} aria-controls={detailsId}
          aria-label={`${detailsOpen ? "Hide" : "Show"} ${CRITERION_REVIEW_COPY.details} for ${item.criterion_text}`}
          onClick={() => setDetailsOpen((open) => !open)}>
          {detailsOpen ? "Hide" : "Show"} {CRITERION_REVIEW_COPY.details}
        </Button>
        <div id={detailsId} hidden={!detailsOpen}>
          <p><strong>{CRITERION_REVIEW_COPY.detailsTitle}</strong></p>
          {reviewNotices.filter(notice => notice.kind === "support").map((notice) => <p key={notice.kind} id={noticeId(notice.kind)}>{notice.text}</p>)}
          {sharedReasonId ? <p>The shared review reason above applies to this criterion.</p> : collapseRecordedReason && item.reason && <p id={reasonId}>{readableNote(item.reason)}</p>}
          {!item.reasoning?.trim() && Boolean(item.evidence?.length) && !assessment?.ruleFacts && (assessment?.source === "ai" || !item.reason?.trim()) && <p>{assessment?.missingExplanation ?? "No explanation is available in this assessment record."}</p>}
          <p>{CRITERION_REVIEW_COPY.detailsQualification}</p>
          {item.votes.length > 0 ? <ol aria-label={CRITERION_REVIEW_COPY.entriesLabel}>{item.votes.map((vote, index) =>
            <li key={index}><strong>Pass {index + 1}</strong>{" "}<span>{vote ?? CRITERION_REVIEW_COPY.missingEntry}</span></li>,
          )}</ol> : <p>{CRITERION_REVIEW_COPY.noEntries}</p>}
        </div>
        </>}
        <p id={sourceQualificationId} className="signal-field-hint">{RECORDED_GRADE_BASIS.qualification}</p>
        <ActionAssessmentContext id={actionContextId} notices={review.assessmentNotices ?? []} />
      </div>

      <div className="signal-resolution-card__judgment">
      <h4>Your grade</h4>
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
                <ActionLink variant="secondary" to={`/report/${checkRunId}#final-decision`}>Go to the recorded decision</ActionLink></>
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


          {!review.canChoose && <p>Reload the current report, or reopen its recorded decision, before making a judgment.</p>}
          {pending?.needsChoice && <p>Your previous choice is no longer supported. Choose a current option; your unsaved reason is kept.</p>}
          {item.levels && item.levels.length > 0
            ? item.levels.map((level) => <Button key={level.level} variant="secondary" disabled={!review.canChoose} aria-describedby={`${describedBy} ${levelDescriptionId(level.level)}`} onClick={event => choose(event.currentTarget, "mark_level", level.level)}>{level.name}</Button>)
            : <><Button variant="secondary" disabled={!review.canChoose} aria-describedby={describedBy} onClick={event => choose(event.currentTarget, "mark_pass")}>Meets criterion</Button><Button variant="secondary" disabled={!review.canChoose} aria-describedby={describedBy} onClick={event => choose(event.currentTarget, "mark_fail")}>Does not meet</Button></>}
          <details className="signal-exclusion-help"><summary>Cannot judge from this manuscript?</summary><p id={exclusionHintId} className="signal-field-hint">{CRITERION_EXCLUSION_COPY.hint} <span>{CRITERION_EXCLUSION_COPY.wait}</span></p><Button variant="quiet" disabled={!review.canChoose} aria-describedby={`${describedBy} ${exclusionHintId}`} onClick={event => choose(event.currentTarget, "needs_document")}>{CRITERION_EXCLUSION_COPY.action}</Button></details>
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

          {excluding && <div className="signal-copy-flow"><p id={exclusionConsequenceId}>{CRITERION_EXCLUSION_COPY.consequence}</p><p>{CRITERION_EXCLUSION_COPY.cancel}</p></div>}
          {selectedLevel && <div className="signal-copy-flow"><p><strong>Selected level: {selectedLevel.name}</strong></p><p>{selectedLevel.descriptor || "Description not recorded for this level."}</p></div>}
          <label><span>{excluding ? CRITERION_EXCLUSION_COPY.reason : ordinaryGrade ? "Comment (optional)" : "Reason (required)"}</span><textarea ref={reasonRef} disabled={saving} readOnly={!draftCurrent} rows={ordinaryGrade ? 2 : 3} required={requiresReason} value={reason} aria-invalid={reasonInvalid || undefined} aria-describedby={[excluding ? exclusionConsequenceId : null, reasonInvalid ? reasonErrorId : null].filter(Boolean).join(" ") || undefined} onChange={(event) => review.changeReason(item.check_result_id, event.target.value)} /></label>
          <p className="signal-field-hint">{ordinaryGrade ? "Any comment is kept with your instructor judgment and export." : "This reason appears in the instructor report and export."}</p>
          {reasonInvalid && <p id={reasonErrorId} role="alert" className="signal-field-error">{reason.trim() ? `Enter at least ${RESOLUTION_REASON_MIN_LENGTH} characters.` : "Enter a reason before confirming."}</p>}
          {saving && <p role="status">{REVIEW_DRAFT_COPY.saving}</p>}
          {Boolean(pending.error) && <Alert title="Saving could not be confirmed" tone="error" role="alert">Your entered reason is kept. Review the current criterion before trying again.</Alert>}

          <div>{draftCurrent && <Button variant="secondary" disabled={saving || pending?.uncertainRevision !== undefined} onClick={event => cancel(event.currentTarget)}>Cancel</Button>}<Button data-criterion-confirm variant="brand" disabled={saving || !draftCurrent} aria-describedby={`${describedBy}${excluding ? ` ${exclusionConsequenceId}` : ""}`} busy={saving} onClick={confirm}>{excluding ? CRITERION_EXCLUSION_COPY.confirm : ordinaryGrade ? "Save grade" : "Confirm resolution"}</Button></div>
        </div>
      )}
      </div>
      <section className="signal-criterion-packet" aria-label={`Saved evidence for ${item.criterion_text}`}>
        <h4>Saved evidence</h4>
        <p className="signal-field-hint">All passages saved for this criterion. No finer evidence grouping was recorded.</p>
        <div className="signal-criterion-packet__quotations" id={`signal-evidence-packet-${item.check_result_id}`}>
        {item.evidence?.map((entry, index) => {
          const actionId = `signal-resolution-source-${checkRunId}-${item.check_result_id}-${index}`;
          const { target, state } = pendingSource(entry.anchor, actionId);
          const recorded = manifest?.references.find(ref => ref.criterion_id === item.criterion_id
            && ref.role === "criterion_evidence" && ref.index === index && ref.quote === entry.quote && ref.anchor === entry.anchor);
          if (onOpenEvidenceSource) return <figure key={`${entry.anchor}-${index}`}>
            <figcaption><strong>Saved passage {index + 1}</strong><span>{entry.anchor.trim() ? instructorAnchor(entry.anchor) : RECORDED_GRADE_BASIS.missingLocation}</span></figcaption><blockquote>“{entry.quote}”</blockquote>
            {recorded && manifest && <RecordedEvidencePage key={`${manifest.snapshot_id}-${recorded.reference_id}`} checkRunId={checkRunId} snapshot={manifest.snapshot_id} reference={recorded} context={evidenceContext} />}
            <Button id={actionId} variant="secondary" disabled={!recorded || !manifest} onClick={() => {
              if (recorded && manifest) onOpenEvidenceSource({ snapshotId: manifest.snapshot_id, reference: recorded, actionId });
            }}>Show recorded location</Button>
          </figure>;
          return <figure key={`${entry.anchor}-${index}`}><figcaption><strong>Saved passage {index + 1}</strong><span>{entry.anchor.trim() ? instructorAnchor(entry.anchor) : RECORDED_GRADE_BASIS.missingLocation}</span></figcaption><blockquote>“{entry.quote}”</blockquote>{recorded && manifest && <RecordedEvidencePage key={`${manifest.snapshot_id}-${recorded.reference_id}`} checkRunId={checkRunId} snapshot={manifest.snapshot_id} reference={recorded} context={evidenceContext} />}{entry.anchor.trim() ? <><ActionLink id={actionId} variant="quiet" state={state} to={target} aria-label={`Open in manuscript: ${instructorAnchor(entry.anchor)}. ${item.criterion_text}`} onClick={event => followPendingSource(event, entry.anchor, actionId)}>Open in manuscript: {instructorAnchor(entry.anchor)}</ActionLink></> : null}</figure>;
        })}
        {onOpenEvidenceSource && manifestQuery.isFetching && <p role="status">Loading recorded source links.</p>}
        {onOpenEvidenceSource && manifestQuery.isError && <p role="status">Recorded source links could not be verified. <Button variant="quiet" onClick={() => { void manifestQuery.refetch(); }}>Retry source links</Button></p>}
        {!item.evidence?.length && !assessment?.ruleFacts && <p>{!item.reasoning?.trim() ? "No located source passage or assessment explanation is available. Review the manuscript before grading." : RECORDED_GRADE_BASIS.missingExcerpt}</p>}

        </div>
      </section>
    </article>
  );
}

export function SignalEscalatedPanel({ checkRunId, focused = false, sourceTarget, onOpenSource, onOpenEvidenceSource, assessmentContextExternal = false, savedAccessExternal = false }: { checkRunId: number; sourceTarget?: CriterionSourceTarget; onOpenSource?: CriterionSourceOpen; onOpenEvidenceSource?: OpenEvidenceSource; assessmentContextExternal?: boolean; savedAccessExternal?: boolean; focused?: boolean }) {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const selectedRaw = params.get(SELECTED_CRITERION_PARAM);
  const requestedSelectedId = selectedCriterionId(selectedRaw);
  const { data, isPending, isError, error, fetchStatus, refetch } = useEscalatedItems(checkRunId);
  const accessUnavailable = !recoverableReadError(error);
  const loadedItems = accessUnavailable ? undefined : data;
  const review = useCriterionReviewScope(checkRunId);
  const items = useMemo(() => loadedItems?.filter((item) => !review.confirmed.has(item.criterion_id)), [loadedItems, review.confirmed]);
  const implicitSelection = useRef<{ run: number; id?: number }>({ run: checkRunId });
  if (implicitSelection.current.run !== checkRunId) implicitSelection.current = { run: checkRunId };
  if (focused && selectedRaw === null && implicitSelection.current.id === undefined && items?.length) {
    implicitSelection.current.id = items[0].criterion_id;
  }
  const selectedId = focused && selectedRaw === null ? implicitSelection.current.id ?? null : requestedSelectedId;
  const selectedView = selectedRaw !== null || (focused && selectedId !== null);
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
  const selectionFocus = useRef<{ criterion: number | undefined; claim: FocusContinuation } | undefined>(undefined);
  useLayoutEffect(() => {
    const pending = selectionFocus.current;
    if (!pending || pending.criterion !== (selectedId ?? undefined)) return;
    const target = document.getElementById(selectedItem ? `signal-resolution-heading-${selectedItem.check_result_id}` : "escalated-heading");
    if (!target || target.closest("[hidden]")) return;
    selectionFocus.current = undefined;
    if (pending.claim.focus(target, { preventScroll: true })) {
      (selectedItem ? target.closest(".signal-selected-criterion")?.querySelector(".signal-criterion-navigation") : target)?.scrollIntoView({ block: "start" });
    }
  });
  useEffect(() => () => selectionFocus.current?.claim.dispose(), []);
  const reviewLink = (criterionId?: number) => reviewDeskDestination(
    `${location.pathname === `/report/${checkRunId}` ? location.pathname : `/report/${checkRunId}/document`}?${criterionReviewSearch(location.search, criterionId)}`, location);
  const select = (event: React.MouseEvent<HTMLAnchorElement>, criterionId?: number) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    selectionFocus.current?.claim.dispose();
    selectionFocus.current = { criterion: criterionId, claim: claimFocusContinuation(event.currentTarget) };
    setParams(criterionReviewSearch(location.search, criterionId), { state: location.state });
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
    <section id="review-criteria" tabIndex={-1} data-tour="escalated-panel" className={focused ? "signal-review-section signal-review-section--attention signal-selected-criterion signal-selected-criterion--focused" : selectedView ? "signal-review-section signal-selected-criterion" : "signal-review-section signal-review-section--attention"} aria-labelledby="escalated-heading">
      <div className={selectedView && !focused ? "sr-only" : "signal-review-section__heading"}><div>{focused && !review.decision && <p className="signal-section-kicker">Rubric review</p>}{review.decision && <p className="signal-section-kicker">Preserved assessment</p>}<h2 id="escalated-heading" ref={headingRef} tabIndex={-1}>{review.decision === "returned" ? "Unresolved at time of return" : review.decision ? "Unresolved at time of decision" : "Criteria needing your judgment"}</h2></div>{(!selectedView || focused) && <span>{reviewListStatus}</span>}</div>
      {review.decision ? <p id={`signal-review-instructions-${checkRunId}`} className={selectedView && !focused ? "sr-only" : "signal-review-section__intro"}>These criteria were unresolved when the decision was recorded. Their outcomes, evidence and assessment limits are preserved. Reopen the decision before making further judgments.</p>
        : <p id={`signal-review-instructions-${checkRunId}`} className={selectedView && !focused ? "sr-only" : "signal-review-section__intro"}>{selectedView ? "Read this criterion beside the manuscript, choose a grade, then save. Comments are optional for ordinary grades." : "Open a criterion to review its recorded passages beside the manuscript and record your judgment."} {!selectedView && <span>{RECORDED_GRADE_BASIS.qualification}</span>}</p>}
      {!selectedView && !assessmentContextExternal && <ReportAssessmentContext notices={review.assessmentNotices ?? []} />}
      <p ref={announcementRef} tabIndex={-1} aria-live="polite" className="signal-live-region">{announcement}</p>
      {!accessUnavailable && !savedAccessExternal && <SavedJudgmentAccess checkRunId={checkRunId} hideEmpty />}
      {selectedView && !focused && <InWorkspaceBar>
        <nav aria-label="Criterion review" className="signal-criterion-navigation signal-review-bar__nav">
          <ActionLink variant="secondary" direction="back" className="signal-workspace-back" state={location.state} to={reviewLink()} onClick={event => select(event)}>Back to criteria</ActionLink>
          <span role="status" className="signal-field-hint">{items && selectedIndex >= 0 && <><span>Criterion {selectedIndex + 1} of {items.length}</span>{" · "}</>}<span>{reviewListStatus}</span></span>
          {selectedIndex > 0 && <ActionLink variant="secondary" aria-label="Previous criterion" state={location.state} to={reviewLink(items![selectedIndex - 1].criterion_id)} onClick={event => select(event, items![selectedIndex - 1].criterion_id)}>Previous</ActionLink>}
          {items && selectedIndex >= 0 && selectedIndex < items.length - 1 && <ActionLink variant="secondary" aria-label="Next criterion" state={location.state} to={reviewLink(items[selectedIndex + 1].criterion_id)} onClick={event => select(event, items[selectedIndex + 1].criterion_id)}>Next</ActionLink>}
        </nav>
      </InWorkspaceBar>}
      {selectedView && focused && <nav aria-label="Criterion review" className="signal-criterion-navigation">
        {!focused && <ActionLink variant="quiet" state={location.state} to={reviewLink()} onClick={event => select(event)}>Back to criteria</ActionLink>}
        {selectedIndex > 0 ? <ActionLink variant={focused ? "quiet" : "secondary"} aria-label="Previous criterion" state={location.state} to={reviewLink(items![selectedIndex - 1].criterion_id)} onClick={event => select(event, items![selectedIndex - 1].criterion_id)}>Previous</ActionLink> : focused && <Button variant={focused ? "quiet" : "secondary"} aria-label="Previous criterion" disabled>Previous</Button>}
        <span role="status" className="signal-field-hint">{focused ? selectedIndex >= 0 ? `${selectedIndex + 1} of ${items?.length ?? 0} remaining` : selectedRecord ? "Grade recorded" : reviewListStatus : <>{items && selectedIndex >= 0 && <><span>Criterion {selectedIndex + 1} of {items.length}</span>{" · "}</>}<span>{reviewListStatus}</span></>}</span>
        {items && selectedIndex >= 0 && selectedIndex < items.length - 1 ? <ActionLink variant={focused ? "quiet" : "secondary"} aria-label="Next criterion" state={location.state} to={reviewLink(items[selectedIndex + 1].criterion_id)} onClick={event => select(event, items[selectedIndex + 1].criterion_id)}>Next</ActionLink> : focused && items?.length && selectedRecord ? <ActionLink variant={focused ? "quiet" : "secondary"} aria-label="Next criterion" state={location.state} to={reviewLink(items[0].criterion_id)} onClick={event => select(event, items[0].criterion_id)}>Next</ActionLink> : focused && <Button variant={focused ? "quiet" : "secondary"} aria-label="Next criterion" disabled>Next</Button>}
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
          {!hasItems && !awaitingList && !isPending && !isError && fetchStatus === "idle" && <Alert title="No unresolved criteria" tone="success">{AUTOMATIC_GRADE_COPY.allRecorded}</Alert>}
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
  active = true,
}: {
  // False while its tab is hidden, so the selection bar shows only beside the list.
  active?: boolean;
  checkRunId: number;
  unresolvedHighFlagCount: number;
  originalityScope?: OriginalityScopeValue | null;
  statisticalScope?: StatisticalCheckScopeOut | null;
}) {
  const review = useContext(ReviewContext);
  const [moduleParams, setModuleParams] = useSearchParams();
  const flagsQuery = useFlags(checkRunId);
  const flags = recoverableReadError(flagsQuery.error) ? flagsQuery.data : undefined;
  // Without a chosen check, open the first one that has findings rather than
  // an empty one, so the tab's count matches what is shown.
  const selectedModule = selectedIntegrityModule(moduleParams.get("module")
    ?? flags?.find((flag) => !isOptionalComparisonLead(flag))?.check_kind ?? null);
  const moduleState = review?.assessment?.integrity_modules?.find(module => module.check_kind === selectedModule)?.state ?? "not_recorded";
  const emptyState = review?.assessment ? moduleEmptyState(review.assessment, selectedModule)
    : MODULE_EMPTY_STATE[moduleState as keyof typeof MODULE_EMPTY_STATE] ?? MODULE_EMPTY_STATE.not_recorded;
  const originalityNote = originalityCoverageNote(review?.assessment);
  const { data: me } = useMe();
  const viewedIds = useViewedFlagIds(me?.id);
  // Saved "could not determine" agreement rows make no claim; they are listed
  // once with the pairs the check could not decide, never as findings.
  const allOtherFlags = flags?.filter((flag) => !isOptionalComparisonLead(flag) && !isUncountedRecencyNote(flag) && !isLegacyUndecided(flag));
  const otherFlags = allOtherFlags?.filter(flag => flag.check_kind === selectedModule);
  const undecidedLegacy = flags?.filter(isLegacyUndecided) ?? [];
  const agreementModule = review?.assessment?.integrity_modules?.find(module => module.check_kind === "internal_agreement");
  const undecidedCount = (agreementModule?.undetermined?.count ?? 0) + undecidedLegacy.length;
  const showUndecided = selectedModule === "internal_agreement" && undecidedCount > 0;
  // V-092: source-age notes the format does not count are listed apart from
  // findings, so no finding count, filter or selection includes them.
  const sourceAgeNoteFlags = flags?.filter((flag) => flag.check_kind === selectedModule && isUncountedRecencyNote(flag)) ?? [];
  const optional = comparisonLeadCollection(checkRunId, flagsQuery);
  const flagsLastLoaded = flags !== undefined && (flagsQuery.fetchStatus !== "idle" || flagsQuery.isError);
  const usableFlags = flags !== undefined && (flags.length > 0 || optional.state === "success");
  const hasOptional = selectedModule === "originality_reuse" && Boolean(optional.snapshot?.total);
  const onlyOptional = optional.state === "success" && hasOptional && otherFlags?.length === 0;
  const openFindingCount = clusterFlagFindings(otherFlags?.filter(isOpenFinding) ?? []).length;
  const openSourceAgeNotes = clusterFlagFindings(sourceAgeNoteFlags.filter(isOpenFinding)).length;
  // BUG-167: defaults the FILTER to the worst unresolved severity present,
  // not the declaration order -- see `defaultFlagView`'s own comment for
  // why this is a filter-default decision, not a reorder.
  // V-089: the selection spans checks, so it is kept here, by finding location.
  const [selectedIds, setSelectedIds] = useStoredSelection(checkRunId);
  const [bulkAction, setBulkAction] = useState<BulkAction | null>(null);
  const [bulkMessage, setBulkMessage] = useState("");
  // A typed reason or comment survives closing the dialog.
  const [bulkDrafts, setBulkDrafts] = useState<Record<BulkAction, string>>({ confirm: "", dismiss: "" });
  const allClusters = clusterFlagFindings(allOtherFlags ?? []);
  // A finding resolved elsewhere since it was selected drops out of the selection.
  const selectedClusters = allClusters.filter((cluster) => isOpenForBulk(cluster) && cluster.flags.every((flag) => selectedIds.has(flag.id)));
  const selectedLocations = selectedClusters.reduce((total, cluster) => total + cluster.flags.length, 0);
  const selection: FindingSelection = {
    isSelected: (cluster) => cluster.flags.every((flag) => selectedIds.has(flag.id)),
    toggle: (cluster) => setSelectedIds((current) => {
      const next = new Set(current);
      const on = cluster.flags.every((flag) => next.has(flag.id));
      for (const flag of cluster.flags) { if (on) next.delete(flag.id); else next.add(flag.id); }
      return next;
    }),
    // Both select only open findings, and only in the check on screen.
    selectMany: (clusters) => setSelectedIds((current) => new Set([...current,
      ...clusters.filter(isOpenForBulk).flatMap((cluster) => cluster.flags.map((flag) => flag.id))])),
    selectAllInCheck: () => setSelectedIds((current) => new Set([...current,
      ...clusterFlagFindings(otherFlags ?? []).filter(isOpenForBulk).flatMap((cluster) => cluster.flags.map((flag) => flag.id))])),
  };
  const shownKeys = new Set(clusterFlagFindings(otherFlags ?? []).map((cluster) => cluster.key));
  const selectedElsewhere = selectedClusters.filter((cluster) => !shownKeys.has(cluster.key)).length;
  const initialView = otherFlags ? defaultFlagView(Math.min(unresolvedHighFlagCount,
    otherFlags.filter(flag => !flag.overridden && flag.severity === "high" && flag.affects_readiness !== false).length), otherFlags) : "open";

  return (
    <section id="integrity-signals" tabIndex={-1} className="signal-review-section" aria-labelledby="flags-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Integrity record</p><h2 id="flags-heading" tabIndex={-1}>Integrity signals</h2></div>{usableFlags && !onlyOptional && <span>{flagsLastLoaded ? "Last loaded: " : ""}{openFindingCount} {hasOptional ? "other open" : "open"} finding{openFindingCount === 1 ? "" : "s"}{openSourceAgeNotes > 0 && ` · ${sourceAgeNotesLabel(openSourceAgeNotes)}`}</span>}</div>
      {!onlyOptional && <p className="signal-review-section__intro">These are possible inconsistencies, not accusations. Open a signal to inspect its excerpt, source anchor, and available instructor action.</p>}
      <div className="signal-integrity-review-body">
      <IntegrityModuleNavigation selected={selectedModule} controls={`signal-selected-module-${checkRunId}`}
        modules={review?.assessment?.integrity_modules}
        stateLabels={originalityNote ? { originality_reuse: originalityNote.tag } : undefined}
        counts={new Map(["internal_agreement", "citation_integrity", "statistical_forensics", "originality_reuse"].map(kind => [kind, flags?.filter(flag => flag.check_kind === kind && !isUncountedRecencyNote(flag) && !isLegacyUndecided(flag)).length ?? 0]))}
        onSelect={kind => { const next = new URLSearchParams(moduleParams); next.set("module", kind); next.set("review_step", "findings"); [FLAG_VIEW_PARAM, CLUSTERS_OPEN_PARAM, VISIBLE_COUNT_PARAM].forEach(key => next.delete(key)); setModuleParams(next, { replace: true }); }} />
      <div id={`signal-selected-module-${checkRunId}`}>

      {review && <p role="status" aria-live="polite" className={bulkMessage ? "signal-bulk-result" : "sr-only"}>{bulkMessage}</p>}
      {review && bulkAction && <BulkResolveDialog action={bulkAction} clusters={selectedClusters} checkRunId={checkRunId}
        expectedRevision={review.assessment?.assessment_revision} viewedIds={viewedIds}
        text={bulkDrafts[bulkAction]} onText={(text) => setBulkDrafts((current) => ({ ...current, [bulkAction]: text }))}
        onClose={() => setBulkAction(null)}
        onDone={(message) => { setBulkAction(null); setSelectedIds(new Set()); setBulkDrafts({ confirm: "", dismiss: "" }); setBulkMessage(message); }} />}
      {selectedModule === "statistical_forensics" && <StatisticalCheckScope scope={statisticalScope} />}
      {selectedModule === "citation_integrity" && <CitationRecencySummary recency={review?.assessment?.citation_recency} />}
      {selectedModule === "originality_reuse" && <OriginalityScope scope={originalityScope} showAddAction />}
      {selectedModule === "originality_reuse" && <OriginalityCoverageNotice report={review?.assessment} />}
      {review && active && <BulkSelectionBar slot="before" findings={selectedClusters.length} locations={selectedLocations} elsewhere={selectedElsewhere} decided={Boolean(review.decision)}
        onAction={(action) => { setBulkMessage(""); setBulkAction(action); }}
        onClear={() => { setSelectedIds(new Set()); document.getElementById("flags-heading")?.focus(); }} />}
      {otherFlags && otherFlags.length > 0
        ? (
          <SignalFlagList
            flags={otherFlags}
            showActions
            initialView={initialView}
            trackViewed
            viewedIds={viewedIds}
            selection={review ? selection : undefined}
          />
        )
        : onlyOptional || (selectedModule === "originality_reuse" && originalityNote) ? null : optional.state === "success" ? <EmptyState className="signal-integrity-empty" id={`signal-module-empty-${checkRunId}`} title={emptyState.title}
          action={emptyState.limits ? <ActionLink to={`/report/${checkRunId}?section=limits`} variant="secondary">Open assessment limits</ActionLink> : undefined}><p>{emptyState.description}</p></EmptyState>
          : <p>{optional.state === "error" ? "Other findings could not be loaded." : optional.state === "paused" ? "Waiting to load other findings." : optional.state === "fetching" ? "Loading other findings." : "Other findings are not available."}</p>}
      {showUndecided && <UndeterminedPairs checkRunId={checkRunId} undetermined={agreementModule?.undetermined} legacy={undecidedLegacy}
        incomplete={agreementModule?.state === "incomplete"} />}
      {sourceAgeNoteFlags.length > 0 && <SourceAgeNotes flags={sourceAgeNoteFlags} viewedIds={viewedIds} />}
      {selectedModule === "originality_reuse" && <ComparisonLeadList collection={optional} showActions viewedIds={viewedIds} onRetry={() => { void flagsQuery.refetch(); }} />}
      {review && active && <BulkSelectionBar slot="after" findings={selectedClusters.length} locations={selectedLocations} elsewhere={selectedElsewhere} decided={Boolean(review.decision)}
        onAction={(action) => { setBulkMessage(""); setBulkAction(action); }}
        onClear={() => { setSelectedIds(new Set()); document.getElementById("flags-heading")?.focus(); }} />}
      </div>
      <details className="signal-integrity-review-notes"><summary>How findings affect readiness</summary><p>Across all modules, {unresolvedHighFlagCount} open high-severity findings count for readiness. Saved records and check status are separate; an empty list does not establish that the manuscript passed.</p></details>
      </div>
    </section>
  );
}

export function SignalPublicFlags({
  flags,
  originalityScope,
  checkRunId,
  statisticalScope,
  selectedModule,
  selectedRecordId,
}: {
  flags: FlagSummaryOut[];
  originalityScope?: OriginalityScopeValue | null;
  checkRunId: number;
  statisticalScope?: StatisticalCheckScopeOut | null;
  selectedModule?: IntegrityKind;
  selectedRecordId?: number;
}) {
  const otherFlags = flags.filter((flag) => !isOptionalComparisonLead(flag) && !isUncountedRecencyNote(flag));
  const sharedNoteFlags = flags.filter(isUncountedRecencyNote);
  const optional = comparisonLeadCollection(checkRunId, { data: flags, fetchStatus: "idle", isSuccess: true, isError: false });
  const findingCount = clusterFlagFindings(otherFlags).length;
  const sourceAgeNotes = clusterFlagFindings(sharedNoteFlags).length;
  const visibleOtherFlags = selectedRecordId === undefined ? otherFlags : otherFlags.filter(flag => flag.id === selectedRecordId);
  const selectedOptional = selectedRecordId === undefined || optional.snapshot?.ordinalById.has(selectedRecordId);
  const hasOptional = Boolean(optional.snapshot?.total);
  const onlyOptional = hasOptional && otherFlags.length === 0;
  return (
    <section id="integrity-signals" tabIndex={-1} className="signal-review-section" aria-labelledby="flags-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Integrity record</p><h2 id="flags-heading">Possible inconsistencies</h2></div>{!onlyOptional && <span>{findingCount} {hasOptional ? "other " : ""}finding{findingCount === 1 ? "" : "s"}{sourceAgeNotes > 0 && ` · ${sourceAgeNotesLabel(sourceAgeNotes)}`}</span>}</div>
      {!onlyOptional && <p className="signal-review-section__intro">These are possible inconsistencies, not accusations. This shared view shows the bounded evidence the instructor chose to share; it does not expose the private Audit trail.</p>}
      {(!selectedModule || selectedModule === "statistical_forensics") && <StatisticalCheckScope scope={statisticalScope} />}
      {(!selectedModule || selectedModule === "originality_reuse") && <OriginalityScope scope={originalityScope} />}
      {visibleOtherFlags.length
        ? <SignalFlagList flags={visibleOtherFlags} showActions={false} initialView="all" />
        : onlyOptional || selectedRecordId !== undefined ? null : <p>{selectedModule ? "No shared integrity signals are shown in this module." : "This run produced no shared integrity signals."}</p>}
      {(!selectedModule || selectedModule === "citation_integrity") && selectedRecordId === undefined && sharedNoteFlags.length > 0 && <SourceAgeNotes flags={sharedNoteFlags} showActions={false} />}
      {(!selectedModule || selectedModule === "originality_reuse") && selectedOptional && <ComparisonLeadList collection={optional} showActions={false} selectedRecordId={selectedRecordId} />}
    </section>
  );
}

// `readOnly` is the readiness report's record view: grades are shown but
// changed only in manuscript review, beside the manuscript.
export function SignalCriteriaResults({ results, checkRunId, publicView = false, readOnly = false, sourceTarget, onOpenSource, renderEvidence, publicSelection }: {
  results: ResultRowCommon[];
  checkRunId: number;
  publicView?: boolean;
  readOnly?: boolean;
  sourceTarget?: CriterionSourceTarget;
  onOpenSource?: CriterionSourceOpen;
  renderEvidence?: (row: ResultRowCommon) => ReactNode;
  publicSelection?: { position: number; total: number };
}) {
  const AssessmentBody = publicSelection ? "section" : "details";
  const location = useLocation();
  const navigate = useNavigate();
  const review = useContext(ReviewContext);
  const privateScope = !publicView && review?.checkRunId === checkRunId;
  const sourceManifest = useEvidenceManifest(checkRunId, review?.assessment?.assessment_revision, Boolean(privateScope));
  const currentManifest = privateScope && review.assessmentCurrent?.() && sourceManifest.isSuccess
    && !sourceManifest.isError && sourceManifest.fetchStatus === "idle" ? sourceManifest.data : undefined;
  const flagsQuery = useFlags(checkRunId, { enabled: !publicView });
  const findingsTo = location.pathname === `/report/${checkRunId}` ? `/report/${checkRunId}?section=findings` : workspaceStepPath(checkRunId, "findings");
  const rows = results.filter((row) => {
    const confirmed = privateScope ? review.confirmed.get(row.criterion_id) : undefined;
    return !confirmed || atLeastSavedJudgment(row, confirmed);
  });
  const updating = rows.length !== results.length || (privateScope
    && [...review.confirmed.values()].some((saved) => !rows.some((row) => atLeastSavedJudgment(row, saved))));
  return (
    <section className={publicSelection ? "signal-review-section signal-review-section--embedded" : "signal-review-section"} aria-labelledby="criteria-results-heading">
      <div className={publicSelection ? "sr-only" : "signal-review-section__heading"}><div><p className="signal-section-kicker">Criterion record</p><h2 id="criteria-results-heading" tabIndex={-1}>Criterion records</h2></div><span>{publicSelection ? `Criterion ${publicSelection.position} of ${publicSelection.total}` : updating ? "Recorded results updating" : `${rows.length} ${rows.length === 1 ? "record" : "records"}`}</span></div>
      <p className={publicSelection ? "sr-only" : "signal-review-section__intro"}>{publicSelection ? "The selected criterion's recorded assessment and saved evidence are shown below." : "Each row shows the recorded status and any available evidence."} A criterion marked Not assessed has no assessment result. Importance is shown as Low, Medium or High.</p>
      {privateScope && <SavedCriterionDestination checkRunId={checkRunId} results={rows} />}
      {readOnly && !publicView && rows.length > 0 && <div className="signal-review-section__support signal-record-edit"><p className="signal-field-hint">Grades are changed in manuscript review, beside the manuscript.</p><ActionLink to={workspaceStepPath(checkRunId, "grades")} variant="secondary">Change grades in manuscript review</ActionLink></div>}
      {rows.length ? <ol className="signal-result-list">{rows.map((row, index) => {
        if (privateScope && review.revisions.records.get(row.criterion_id)?.unavailable) {
          return <li key={row.criterion_id} className="signal-result-card"><div className="signal-result-card__top" />
            <h3 id={criterionRecordId(checkRunId, row.criterion_id)} tabIndex={-1}>Recorded criterion unavailable</h3>
            <p>This criterion could not be accessed. Its last loaded private details have been cleared.</p>
            <Button variant="secondary" onClick={() => review.revisions.open(row.criterion_id, "current")}>Retry recorded criterion</Button></li>;
        }
        // A criterion still waiting keeps its place in the list, so the count
        // and numbering match the required format; grading it happens in step 1.
        if (row.outcome === "escalated" && !publicView) {
          return <li key={row.criterion_id} className="signal-result-card signal-result-card--waiting"><div className="signal-result-card__top"><span className="signal-result-card__number">{index + 1}</span><span className="signal-outcome signal-outcome--review">{GRADE_LIST_COPY.waiting}</span></div>
            <h3 id={criterionRecordId(checkRunId, row.criterion_id)} tabIndex={-1}>{row.text}</h3>
            <p className="signal-resolution-note">{GRADE_LIST_COPY.waitingNote}</p>
            <ActionLink to={`${workspaceStepPath(checkRunId, "criteria")}&${SELECTED_CRITERION_PARAM}=${row.criterion_id}`} variant="secondary" aria-label={`${GRADE_LIST_COPY.gradeIt}: ${row.text}`}>{GRADE_LIST_COPY.gradeIt}</ActionLink></li>;
        }
        const display = resultDisplay(row);
        const assessment = criterionAssessmentDetails(row, publicView);
        // The adviser view shows how an automatic grade was chosen as well; its rows
        // carry these fields only for grades the instructor did not set.
        const automaticRow = publicView || !row.resolution;
        const gradeCaution = automaticRow ? automaticGradeCaution(row as CriterionResultOut) : null;
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
            variant="secondary"
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
        return <li key={row.criterion_id} className="signal-result-card"><div className="signal-result-card__top"><span className="signal-result-card__number">{publicSelection?.position ?? index + 1}</span><span className={`signal-outcome signal-outcome--${assessment.ruleScopeUnconfirmed && !assessment.instructorRecorded ? "neutral" : gradeCaution ? "warning" : outcome.tone}`}>{assessment.ruleScopeUnconfirmed && !assessment.instructorRecorded ? "Previously recorded: " : ""}{outcome.label}{gradeCaution && <> · {automaticGradeChip(row as CriterionResultOut)}</>}</span><span className={`signal-importance signal-importance--${row.weight_importance === "med" ? "medium" : row.weight_importance}`}>{row.weight_importance === "med" ? "Medium" : `${row.weight_importance.slice(0, 1).toUpperCase()}${row.weight_importance.slice(1)}`} importance</span></div><h3 id={criterionRecordId(checkRunId, row.criterion_id)} tabIndex={-1}>{row.text}</h3>{automaticRow && automaticGradeLines(row as CriterionResultOut).map((line) => <p key={line} className="signal-resolution-note">{line}</p>)}{!publicView && !row.resolution && automaticGradeLines(row as CriterionResultOut).length > 0 && automaticGradePasses(row as CriterionResultOut) && <p className="signal-resolution-note">{automaticGradePasses(row as CriterionResultOut)}</p>}{gradeCaution && <p className="signal-grade-caution"><span aria-hidden="true">{"⚠"}</span>{gradeCaution}</p>}{!publicView && !row.resolution && flagsQuery.data && automaticGradeLines(row as CriterionResultOut).length > 0 && <RelatedFindings evidence={row.evidence} flags={flagsQuery.data} findingsTo={findingsTo} criterionText={row.text} />}{assessment.ruleScopeUnconfirmed && !assessment.instructorRecorded && <RuleScopeNotice />}{!publicView && row.resolution && <p className="signal-resolution-note"><strong>Instructor judgment</strong></p>}{!publicView && row.resolution?.type === "needs_document" && <p className="signal-resolution-note">{CRITERION_EXCLUSION_COPY.recorded}. {CRITERION_EXCLUSION_COPY.noGrade}</p>}{!publicView && row.resolution && <>{row.resolution.type !== "needs_document" && <p className="signal-resolution-note"><strong>Recorded grade:</strong> {recordedGradeLabel(row)}</p>}<p className="signal-resolution-note"><strong>Instructor reason:</strong> {recordedInstructorComment(row.resolution.type, row.resolution.reason) ?? "No instructor reason is available in this record."}</p></>}{/* BUG-065: this summary repeats verbatim once per row -- a screen-reader
                links/buttons list can't tell them apart without a per-criterion
                accessible name, same fix already applied to the flag "Review
                evidence" links and the escalation resolve-button groups. */}
              {privateScope && !readOnly && (row.resolution || (row.type !== "not_assessable" && (row as CriterionResultOut).judgment_revision == null
                && ["structural", "semantic"].includes(row.kind) && ["passed", "failed"].includes(row.outcome)))
                && <RecordedJudgmentTools checkRunId={checkRunId} criterionId={row.criterion_id} criterionText={row.text} canonical={row} />}
              {assessment.ruleScopeUnconfirmed && assessment.instructorRecorded && <RuleScopeNotice earlier />}
              <AssessmentBody className={publicSelection ? "signal-public-criterion-assessment" : undefined}>{publicSelection ? <><h4>Why this grade</h4><p id={evidenceSummaryId} className="signal-field-hint">{assessment.summary}</p></> : <summary id={evidenceSummaryId} aria-label={`${assessment.summary}: ${row.text}`}>{assessment.summary}</summary>}<div className="signal-result-card__evidence">
                {assessment.relationship && <p>{assessment.relationship}</p>}
                {assessment.sourceCoverage && <CriterionSourceCoverage value={assessment.sourceCoverage} sourceAction={sourceAction} firstSlot={row.evidence.length + 1} criterionText={row.text} />}
                {unresolvedAi && <><p><strong>No grade accepted</strong></p><p>{RECORDED_GRADE_BASIS.review}</p><p>{RECORDED_GRADE_BASIS.qualification}</p></>}
                {!assessment.hasDetails ? <p>No assessment details are available in this record.</p> : <>
                {assessment.ruleFacts && <RuleFactsDetails facts={assessment.ruleFacts} earlier={assessment.instructorRecorded} />}
                {row.reasoning?.trim() ? <p><strong>{assessment.explanationLabel}</strong> {row.reasoning}</p> : !assessment.ruleFacts && (unresolvedAi || !row.reason?.trim()) && <p>{unresolvedAi ? RECORDED_GRADE_BASIS.missingExplanation : assessment.missingExplanation}</p>}
                {row.reason?.trim() && <p><strong>Recorded system note:</strong> {row.reason}</p>}
                {!publicSelection && row.evidence.length > 0 && <h4>Recorded manuscript excerpts</h4>}
                {!unresolvedAi && assessment.hasLocations && <p>{RECORDED_GRADE_BASIS.qualification}</p>}
                {renderEvidence ? renderEvidence(row) : row.evidence.length ? row.evidence.map((item, evidenceIndex) => {
                  const hasAnchor = Boolean(item.anchor?.trim());
                  const showAction = hasAnchor && !seenAnchors.has(item.anchor);
                  if (hasAnchor) seenAnchors.add(item.anchor);
                  const recorded = currentManifest?.references.find(ref => ref.criterion_id === row.criterion_id
                    && ref.role === "criterion_evidence" && ref.index === evidenceIndex && ref.quote === item.quote && ref.anchor === item.anchor);
                  return <div className="signal-attached-flow" key={`${item.anchor}-${item.quote}-${evidenceIndex}`}>
                  <blockquote><span>“{item.quote}”</span><cite>{instructorAnchor(item.anchor)}</cite></blockquote>
                    {recorded && currentManifest && <RecordedEvidencePage checkRunId={checkRunId} snapshot={currentManifest.snapshot_id} reference={recorded} context={{
                      title: row.text, explanation: row.reasoning, explanationLabel: assessment.explanationLabel ?? "Recorded explanation:",
                      recordedNote: row.reason, references: currentManifest.references.filter(ref => ref.criterion_id === row.criterion_id && ref.role === "criterion_evidence"),
                    }} />}
                    {!hasAnchor && <p>No manuscript location was recorded for this excerpt.</p>}
                    {!publicView && showAction && sourceAction(item.anchor)}
                  </div>;
                }) : !assessment.ruleFacts && <p>{unresolvedAi ? RECORDED_GRADE_BASIS.missingExcerpt : "No excerpt is available in this assessment record."}</p>}
                {!publicView && row.anchor?.trim() && !excerptAnchors.has(row.anchor) && <div className="signal-attached-flow"><p>Recorded criterion location: {row.anchor}</p>{sourceAction(row.anchor)}</div>}

                </>}
              </div></AssessmentBody></li>;
      })}</ol> : updating ? <p>Recorded results are updating.</p> : <div className="signal-desk-empty"><h3>No criterion records in this section</h3><p>No criterion records are shown here. Review the other report sections for unresolved criteria and assessment limits.</p></div>}
    </section>
  );
}
