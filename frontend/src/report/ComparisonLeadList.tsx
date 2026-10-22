import { useEffect, useLayoutEffect, useRef } from "react";
import { useLocation, useSearchParams } from "react-router";
import { FLAG_LIST_BATCH_COUNT, FLAG_LIST_INITIAL_COUNT } from "../config/ui";
import { COMPARISON_LEAD_COPY, COMPARISON_LEAD_HEADING_ID, comparisonLeadFilter, comparisonLeadIdentity, type ComparisonLeadFilter } from "../domain/comparisonLeadIdentity";
import { isManuscriptQuote } from "../domain/evidenceDisplay";
import { problemLabel } from "../domain/problemLabel";
import { instructorAnchor } from "../domain/sourceAnchor";
import { systemFindingCopy } from "../domain/systemFindingCopy";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import type { ComparisonLeadCollection } from "./comparisonLeadCollection";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";

export function ComparisonLeadList({ collection, showActions, viewedIds, onSelect, onRetry }: {
  collection: ComparisonLeadCollection;
  showActions: boolean;
  viewedIds?: ReadonlySet<number>;
  onSelect?: (id: number) => void;
  onRetry?: () => void;
}) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const { snapshot, state, lastLoaded } = collection;
  const filter = comparisonLeadFilter(params.get("lead_view"));
  const rawVisible = Number(params.get("lead_visible"));
  const visibleCount = Number.isSafeInteger(rawVisible) && rawVisible > 0 ? rawVisible : FLAG_LIST_INITIAL_COUNT;
  const records = snapshot?.records ?? [];
  const filtered = records.filter((record) => filter === "all" || (filter === "resolved" ? record.overridden : !record.overridden));
  const visible = filtered.slice(0, visibleCount);
  const openCount = records.filter((record) => !record.overridden).length;
  const resolvedCount = records.length - openCount;
  const viewedCount = records.filter((record) => viewedIds?.has(record.id)).length;
  const more = Math.min(FLAG_LIST_BATCH_COUNT, Math.max(0, filtered.length - visible.length));
  const prefix = lastLoaded ? "Last loaded: " : "";
  const requestedFocus = useRef<{ id: number; from: string; search: string; ownership: FocusContinuation } | undefined>(undefined);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => () => { requestedFocus.current?.ownership.dispose(); }, []);

  useLayoutEffect(() => {
    const request = requestedFocus.current;
    if (!request) return;
    if (params.toString() !== request.search) {
      if (params.toString() !== request.from) {
        request.ownership.dispose(); requestedFocus.current = undefined;
      }
      return;
    }
    requestedFocus.current = undefined;
    request.ownership.focus(document.getElementById(`comparison-lead-${request.id}`) ?? headingRef.current);
  });

  function selectFilter(nextFilter: ComparisonLeadFilter) {
    const next = new URLSearchParams(params);
    if (nextFilter === "all") next.delete("lead_view"); else next.set("lead_view", nextFilter);
    next.delete("lead_visible");
    setParams(next, { replace: true });
  }

  function showMore() {
    const firstNew = filtered[visibleCount];
    const next = new URLSearchParams(params);
    next.set("lead_visible", String(visibleCount + FLAG_LIST_BATCH_COUNT));
    requestedFocus.current?.ownership.dispose();
    if (firstNew && document.hasFocus()) requestedFocus.current = { id: firstNew.id, from: params.toString(), search: next.toString(), ownership: claimFocusContinuation() };
    setParams(next, { replace: true });
  }

  const progress = state === "fetching" ? `${snapshot ? "Updating" : "Loading"} comparison lead records.`
    : state === "paused" ? `Waiting to ${snapshot ? "update" : "load"} comparison lead records.`
      : state === "unavailable" ? COMPARISON_LEAD_COPY.unavailable : "";
  return <section aria-labelledby={COMPARISON_LEAD_HEADING_ID} aria-busy={state === "fetching"}>
    <div className="signal-review-section__heading"><h3 id={COMPARISON_LEAD_HEADING_ID} ref={headingRef} tabIndex={-1}>{COMPARISON_LEAD_COPY.title}</h3>
      {snapshot && <span>{prefix}{snapshot.total} recorded comparison {snapshot.total === 1 ? "lead" : "leads"}</span>}
    </div>
    <p>{COMPARISON_LEAD_COPY.intro}</p>
    <p role="status" aria-live="polite" aria-atomic="true">{progress}{snapshot && ` ${lastLoaded ? COMPARISON_LEAD_COPY.lastLoaded + " " : ""}${prefix}Showing ${visible.length} of ${filtered.length} ${filter} comparison leads.`}</p>
    {state === "error" && <Alert title={COMPARISON_LEAD_COPY.failed} tone="error" role="alert">{onRetry && <Button variant="secondary" onClick={() => {
      if (document.hasFocus()) headingRef.current?.focus();
      onRetry();
    }}>Try again</Button>}</Alert>}
    {snapshot && records.length > 0 && <>
      <div role="group" aria-label="Filter optional comparison leads" className="signal-flag-filters">
        {(["all", "open", "resolved"] as const).map((value) => <button type="button" key={value} aria-pressed={filter === value} onClick={() => selectFilter(value)}>
          <span>{value[0].toUpperCase() + value.slice(1)}</span><small>{prefix}{value === "all" ? records.length : value === "open" ? openCount : resolvedCount}</small>
        </button>)}
      </div>
      {viewedIds && <><p>{prefix}Marked viewed: {viewedCount} of {records.length} comparison leads. Resolved: {resolvedCount} of {records.length} comparison leads.</p><p className="signal-field-hint">{COMPARISON_LEAD_COPY.viewed}</p></>}
      {visible.length ? <ol className="signal-flag-list">{visible.map((record) => {
        const identity = comparisonLeadIdentity(record, snapshot);
        const actionId = `comparison-lead-${record.id}`;
        const actionName = `Review evidence for ${identity.label}`;
        return <li key={record.id} className="signal-flag-row">
          <div className="signal-flag-row__summary"><span className="signal-severity signal-severity--low">{COMPARISON_LEAD_COPY.badge}</span>{record.overridden && <span className="signal-resolution-state">Resolved by instructor</span>}{viewedIds?.has(record.id) && <span className="signal-viewed-state">Viewed this record</span>}</div>
          <p>{identity.label}</p><h4>{problemLabel(record.problem_kind) ?? "Recorded comparison"}</h4>
          {isManuscriptQuote(record) ? <blockquote>“{systemFindingCopy(record.evidence_excerpt, record.problem_kind, "evidence")}”</blockquote> : <p>{systemFindingCopy(record.evidence_excerpt, record.problem_kind, "evidence")}</p>}
          <p>{instructorAnchor(record.page_anchor)}</p>
          {record.first_upload_context && <p className="signal-field-hint">First-upload context: the comparison archive was limited when this signal was created.</p>}
          {showActions && (onSelect
            ? <Button id={actionId} variant="secondary" aria-label={actionName} onClick={() => onSelect(record.id)}>Review evidence</Button>
            : <ActionLink id={actionId} variant="secondary" to={`/flags/${record.id}`} aria-label={actionName}
              state={{ routeReturnFocus: { returnPath: location.pathname + location.search, elementId: actionId, fallbackElementId: COMPARISON_LEAD_HEADING_ID } }}>Review evidence</ActionLink>)}
        </li>;
      })}</ol> : state === "success" && <p>No {filter} comparison leads in this view. Select All to see all recorded leads.</p>}
      {more > 0 && <Button variant="secondary" onClick={showMore}>Show {more} more comparison leads</Button>}
    </>}
    {state === "success" && snapshot?.total === 0 && <p>{COMPARISON_LEAD_COPY.empty}</p>}
  </section>;
}
