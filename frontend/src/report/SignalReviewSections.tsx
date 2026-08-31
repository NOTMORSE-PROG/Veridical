import { useEffect, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router";
import type {
  EscalatedItemOut,
  EscalationResolution,
  FlagSummaryOut,
  ResultRowCommon,
} from "../api/types";
import {
  FLAG_LIST_BATCH_COUNT,
  FLAG_LIST_INITIAL_COUNT,
  RESOLUTION_REASON_MIN_LENGTH,
} from "../config/ui";
import { problemLabel } from "../domain/problemLabel";
import { systemFindingCopy } from "../domain/systemFindingCopy";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { clusterFlagFindings, type FlagFindingCluster } from "./flagClusters";
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

function readOpenClusters(searchParams: URLSearchParams): Set<string> {
  const raw = searchParams.get(CLUSTERS_OPEN_PARAM);
  return raw ? new Set(raw.split(",")) : new Set();
}

function SignalFindingCard({
  cluster,
  expanded,
  showActions,
  showResolutionSummary,
  onToggle,
}: {
  cluster: FlagFindingCluster;
  expanded: boolean;
  showActions: boolean;
  showResolutionSummary: boolean;
  onToggle: () => void;
}) {
  const { flags } = cluster;
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
          {first.overridden && <span className="signal-resolution-state">Resolved by instructor</span>}
          {first.confirmed_citation_source && <span className="signal-resolution-state">Source confirmed</span>}
        </div>
        <h3 id={headingId} tabIndex={-1}>{problemLabel(first.problem_kind) ?? first.criterion_text ?? "Possible inconsistency"}</h3>
        <blockquote>“{systemFindingCopy(first.evidence_excerpt, first.problem_kind, "evidence")}”</blockquote>
        <p>{first.page_anchor}</p>
        {first.first_upload_context && <p className="signal-field-hint">First-upload context: the comparison archive was limited when this signal was created.</p>}
        {showActions && (
          <ActionLink
            id={`signal-flag-review-${first.id}`}
            to={`/flags/${first.id}`}
            variant="secondary"
            state={{ routeReturnFocus: { returnPath: location.pathname, elementId: `signal-flag-review-${first.id}` } }}
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
        ? <blockquote>“{systemFindingCopy(first.evidence_excerpt, first.problem_kind, "evidence")}”</blockquote>
        : <p className="signal-flag-finding-context">{flags.length} manuscript locations point to this possible finding. Open the locations to verify each excerpt.</p>}
      {first.first_upload_context && <p className="signal-field-hint">First-upload context: the comparison archive was limited when this signal was created.</p>}
      <Button
        variant="quiet"
        aria-expanded={expanded}
        aria-controls={locationsId}
        onClick={onToggle}
      >
        {expanded ? "Hide" : "Show"} {flags.length} locations
      </Button>
      {expanded && (
        <ol id={locationsId} className="signal-flag-location-list">
          {flags.map((flag, locationIndex) => (
            <li key={flag.id} className={flag.overridden ? "signal-flag-location signal-flag-location--resolved" : "signal-flag-location"}>
              <blockquote>“{systemFindingCopy(flag.evidence_excerpt, flag.problem_kind, "evidence")}”</blockquote>
              <div className="signal-flag-location__meta">
                <strong>{flag.page_anchor}</strong>
                {mixedSeverity && <span className={`signal-severity signal-severity--${flag.severity}`}>{SEVERITY_LABEL[flag.severity]}</span>}
                {flag.overridden && <span className="signal-resolution-state">Resolved by instructor</span>}
                {flag.confirmed_citation_source && <span className="signal-resolution-state">Source confirmed</span>}
              </div>
              {showActions && (
                <ActionLink
                  id={`signal-flag-review-${flag.id}`}
                  to={`/flags/${flag.id}`}
                  variant="secondary"
                  aria-label={`Review evidence at ${flag.page_anchor}, location ${locationIndex + 1} of ${flags.length}`}
                  state={{ routeReturnFocus: { returnPath: location.pathname, elementId: `signal-flag-review-${flag.id}` } }}
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
}: {
  flags: FlagSummaryOut[];
  showActions: boolean;
  initialView: FlagView;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [view, setView] = useState<FlagView>(() => readFlagView(searchParams, initialView));
  const [visibleCount, setVisibleCount] = useState(FLAG_LIST_INITIAL_COUNT);
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
    setView(next);
    setVisibleCount(FLAG_LIST_INITIAL_COUNT);
    const nextParams = new URLSearchParams(searchParams);
    if (next === initialView) nextParams.delete(FLAG_VIEW_PARAM);
    else nextParams.set(FLAG_VIEW_PARAM, next);
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
    setVisibleCount((count) => count + FLAG_LIST_BATCH_COUNT);
    setFocusClusterKey(firstNew?.key);
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
        <p role="status" aria-live="polite">
          Showing {visible.length} of {filtered.length} {viewLabel} finding{filtered.length === 1 ? "" : "s"} across {locationCount} location{locationCount === 1 ? "" : "s"}.
        </p>
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

function voteSummary(item: EscalatedItemOut): string {
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

function SignalResolutionCard({ item, checkRunId, onResolved }: {
  item: EscalatedItemOut;
  checkRunId: number;
  onResolved: (message: string) => void;
}) {
  const [pending, setPending] = useState<{ resolution: EscalationResolution; level?: number }>();
  const [reason, setReason] = useState("");
  const [attempted, setAttempted] = useState(false);
  const resolve = useResolveEscalation(checkRunId);
  const reasonInvalid = attempted && reason.trim().length < RESOLUTION_REASON_MIN_LENGTH;
  const reasonErrorId = `signal-resolution-reason-${item.check_result_id}`;

  function choose(resolution: EscalationResolution, level?: number) {
    setPending({ resolution, level });
    setReason("");
    setAttempted(false);
  }

  function confirm() {
    setAttempted(true);
    if (!pending || reason.trim().length < RESOLUTION_REASON_MIN_LENGTH) return;
    resolve.mutate(
      {
        checkResultId: item.check_result_id,
        resolution: pending.resolution,
        reason: reason.trim(),
        level: pending.level,
      },
      {
        onSuccess: (out) => {
          const result = out.report.results.find((row) => row.criterion_id === item.criterion_id);
          const resultLabel = result?.level?.name
            ?? (out.outcome === "not_applicable" ? "excluded because another document is needed" : OUTCOME_COPY[out.outcome as ResultRowCommon["outcome"]]?.label ?? out.outcome);
          onResolved(`Criterion resolved as ${resultLabel}. The readiness band has been recalculated.`);
          setPending(undefined);
        },
      },
    );
  }

  return (
    <article className="signal-resolution-card">
      <div className="signal-resolution-card__context">
        <div><p className="signal-section-kicker">Criterion requiring judgment</p><h3>{item.criterion_text}</h3></div>
        <p className="signal-resolution-card__vote">{voteSummary(item)}</p>
        {item.reason && <p>{item.reason}</p>}
        {item.injection_suspected && item.injection_matched_snippet && (
          <blockquote className="signal-unverified-evidence"><strong>Text addressed at an automated grader</strong><span>“{item.injection_matched_snippet}”</span></blockquote>
        )}
        {item.unverified_evidence && item.unverified_evidence.length > 0 && (
          <div className="signal-unverified-evidence"><strong>Could not verify against the source</strong>{item.unverified_evidence.map((quote) => <blockquote key={quote}>“{quote}”</blockquote>)}</div>
        )}
      </div>

      {!pending ? (
        <div className="signal-resolution-options" aria-label={`Resolve: ${item.criterion_text}`}>
          {item.levels && item.levels.length > 0
            ? item.levels.map((level) => <Button key={level.level} variant="secondary" onClick={() => choose("mark_level", level.level)}>{level.name}</Button>)
            : <><Button variant="secondary" onClick={() => choose("mark_pass")}>Meets criterion</Button><Button variant="secondary" onClick={() => choose("mark_fail")}>Does not meet</Button></>}
          <Button variant="quiet" onClick={() => choose("needs_document")}>Needs another document</Button>
          {item.ai_majority_verdict !== null && <Button variant={item.review_reason === "injection_suspected" ? "quiet" : "secondary"} onClick={() => choose("accept_majority")}>Accept AI suggestion: {item.ai_majority_verdict}</Button>}
        </div>
      ) : (
        <div className="signal-resolution-form">
          <p><strong>Pending resolution:</strong> {resolutionLabel(item, pending.resolution, pending.level)}</p>
          <label><span>Reason (required)</span><textarea autoFocus rows={3} value={reason} aria-invalid={reasonInvalid || undefined} aria-describedby={reasonInvalid ? reasonErrorId : undefined} onChange={(event) => { setReason(event.target.value); if (attempted) setAttempted(false); }} /></label>
          <p className="signal-field-hint">This reason appears in the report, export, and any active share link.</p>
          {reasonInvalid && <p id={reasonErrorId} role="alert" className="signal-field-error">{reason.trim() ? `Enter at least ${RESOLUTION_REASON_MIN_LENGTH} characters.` : "Enter a reason before confirming."}</p>}
          {resolve.error && <Alert title="Could not save this resolution" tone="error" role="alert">{resolve.error instanceof Error ? resolve.error.message : "Try again."}</Alert>}
          <div><Button variant="secondary" disabled={resolve.isPending} onClick={() => setPending(undefined)}>Cancel</Button><Button variant="brand" busy={resolve.isPending} onClick={confirm}>Confirm resolution</Button></div>
        </div>
      )}
    </article>
  );
}

export function SignalEscalatedPanel({ checkRunId }: { checkRunId: number }) {
  const { data: items, isPending, isError, refetch } = useEscalatedItems(checkRunId);
  const [announcement, setAnnouncement] = useState("");
  const headingRef = useRef<HTMLHeadingElement>(null);
  const announcementRef = useRef<HTMLParagraphElement>(null);
  const hasItems = Boolean(items?.length);

  useEffect(() => {
    if (!hasItems && announcement) announcementRef.current?.focus();
  }, [announcement, hasItems]);

  if (isPending) return <div role="status" aria-busy="true" className="signal-desk-loading"><span>Loading criteria that need review…</span><i /><i /></div>;
  if (isError) return <Alert title="Could not load criteria needing review" tone="error" role="alert"><Button variant="secondary" onClick={() => refetch()}>Try again</Button></Alert>;

  return (
    <section id="review-criteria" tabIndex={-1} data-tour="escalated-panel" className="signal-review-section signal-review-section--attention" aria-labelledby="escalated-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">First review task</p><h2 id="escalated-heading" ref={headingRef} tabIndex={-1}>Criteria needing your judgment</h2></div><span>{items?.length ?? 0} unresolved</span></div>
      <p className="signal-review-section__intro">VERIDICAL did not settle these items. Review the evidence and record your own reasoned resolution; the final decision remains unavailable until they are resolved.</p>
      <p ref={announcementRef} tabIndex={-1} aria-live="polite" className="signal-live-region">{announcement}</p>
      {hasItems
        ? <div className="signal-resolution-list">{items?.map((item) => <SignalResolutionCard key={item.check_result_id} item={item} checkRunId={checkRunId} onResolved={(message) => { setAnnouncement(message); headingRef.current?.focus(); }} />)}</div>
        : <Alert title="No unresolved criteria" tone="success">Every criterion has a recorded outcome. Continue to the integrity signals.</Alert>}
    </section>
  );
}

export function SignalFlagsPanel({ checkRunId }: { checkRunId: number }) {
  const { data: flags, isPending, isError, refetch } = useFlags(checkRunId);
  if (isPending) return <div role="status" aria-busy="true" className="signal-desk-loading"><span>Loading integrity signals…</span><i /><i /></div>;
  if (isError) return <Alert title="Could not load integrity signals" tone="error" role="alert"><Button variant="secondary" onClick={() => refetch()}>Try again</Button></Alert>;
  const openFindingCount = clusterFlagFindings(flags?.filter((flag) => !flag.overridden) ?? []).length;

  return (
    <section id="integrity-signals" tabIndex={-1} className="signal-review-section" aria-labelledby="flags-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Second review task</p><h2 id="flags-heading" tabIndex={-1}>Integrity signals</h2></div><span>{openFindingCount} open finding{openFindingCount === 1 ? "" : "s"}</span></div>
      <p className="signal-review-section__intro">These are possible inconsistencies, not accusations. Open a signal to inspect its excerpt, source anchor, and available instructor action.</p>
      {flags?.length
        ? <SignalFlagList flags={flags} showActions initialView="open" />
        : <Alert title="No integrity signals" tone="success">This run produced no checkable integrity signals.</Alert>}
    </section>
  );
}

export function SignalPublicFlags({ flags }: { flags: FlagSummaryOut[] }) {
  const findingCount = clusterFlagFindings(flags).length;
  return (
    <section id="integrity-signals" tabIndex={-1} className="signal-review-section" aria-labelledby="flags-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Integrity record</p><h2 id="flags-heading">Possible inconsistencies</h2></div><span>{findingCount} finding{findingCount === 1 ? "" : "s"}</span></div>
      <p className="signal-review-section__intro">These are possible inconsistencies, not accusations. This shared view shows the bounded evidence the instructor chose to share; it does not expose the private Audit trail.</p>
      {flags.length
        ? <SignalFlagList flags={flags} showActions={false} initialView="all" />
        : <Alert title="No integrity signals" tone="success">This run produced no shared integrity signals.</Alert>}
    </section>
  );
}

export function SignalCriteriaResults({ results, checkRunId, publicView = false }: {
  results: ResultRowCommon[];
  checkRunId: number;
  publicView?: boolean;
}) {
  return (
    <section className="signal-review-section" aria-labelledby="criteria-results-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Criterion record</p><h2 id="criteria-results-heading" tabIndex={-1}>All assessed criteria</h2></div><span>{results.length} total</span></div>
      <p className="signal-review-section__intro">Each row shows the recorded outcome and the evidence available for checking it. Importance is shown as a band, never as a judgment percentage.</p>
      {results.length ? <ol className="signal-result-list">{results.map((row, index) => {
        const outcome = OUTCOME_COPY[row.outcome];
        return <li key={row.criterion_id} className="signal-result-card"><div className="signal-result-card__top"><span className="signal-result-card__number">{index + 1}</span><span className={`signal-outcome signal-outcome--${outcome.tone}`}>{outcome.label}</span><span className={`signal-importance signal-importance--${row.weight_importance === "med" ? "medium" : row.weight_importance}`}>{row.weight_importance === "med" ? "Medium" : `${row.weight_importance.slice(0, 1).toUpperCase()}${row.weight_importance.slice(1)}`} importance</span></div><h3>{row.text}</h3>{row.level && <p className="signal-level-result"><strong>Recorded level:</strong> {row.level.name}</p>}{row.resolution && <p className="signal-resolution-note"><strong>Instructor resolution:</strong> {row.resolution.reason}</p>}<details><summary>Review evidence and reasoning</summary><div className="signal-result-card__evidence">{row.evidence.length ? row.evidence.map((item) => <blockquote key={`${item.anchor}-${item.quote}`}><span>“{item.quote}”</span><cite>{item.anchor}</cite></blockquote>) : <p>No verified excerpt was recorded for this criterion.</p>}{row.reasoning && <p><strong>Recorded reasoning:</strong> {row.reasoning}</p>}{row.reason && <p><strong>System note:</strong> {row.reason}</p>}{!publicView && row.anchor && <ActionLink to={`/report/${checkRunId}/document?anchor=${encodeURIComponent(row.anchor)}`} variant="quiet">Open manuscript anchor</ActionLink>}</div></details></li>;
      })}</ol> : <div className="signal-desk-empty"><h3>No criterion results yet</h3><p>This report has no assessed criterion rows.</p></div>}
    </section>
  );
}
