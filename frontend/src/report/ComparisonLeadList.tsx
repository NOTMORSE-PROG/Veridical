import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router";
import { COMPARISON_LEAD_PAGE_SIZE } from "../config/ui";
import { COMPARISON_LEAD_COPY, COMPARISON_LEAD_HEADING_ID, comparisonLeadFilter, comparisonLeadIdentity, type ComparisonLeadFilter } from "../domain/comparisonLeadIdentity";
import { isManuscriptQuote } from "../domain/evidenceDisplay";
import { problemLabel } from "../domain/problemLabel";
import { instructorAnchor } from "../domain/sourceAnchor";
import { systemFindingCopy } from "../domain/systemFindingCopy";
import { WorkflowLink } from "../routing/WorkflowLink";
import { pendingRouteReturnElement } from "../routing/useRouteFocus";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
import { TrailingChevronIcon } from "../ui/ActionLink";
import { Button } from "../ui/Button";
import type { ComparisonLeadCollection } from "./comparisonLeadCollection";

function positiveInteger(value: string | null | undefined) {
  if (!value || !/^[1-9]\d*$/.test(value)) return undefined;
  const number = Number(value);
  return Number.isSafeInteger(number) ? number : undefined;
}

export function ComparisonLeadList({ collection, showActions, viewedIds, onSelect, onRetry, headingLevel = 3, workspace = false }: {
  collection: ComparisonLeadCollection;
  showActions: boolean;
  viewedIds?: ReadonlySet<number>;
  onSelect?: (id: number) => void;
  onRetry?: () => void;
  headingLevel?: 2 | 3;
  workspace?: boolean;
}) {
  const [params] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { snapshot, state, lastLoaded } = collection;
  const aboutId = useId();
  const [aboutOpen, setAboutOpen] = useState(false);
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const filter = comparisonLeadFilter(params.get("lead_view"));
  const records = snapshot?.records ?? [];
  const filtered = records.filter((record) => filter === "all" || (filter === "resolved" ? record.overridden : !record.overridden));
  const pendingId = pendingRouteReturnElement(location.pathname + location.search)?.match(/^comparison-lead-([1-9]\d*)$/)?.[1];
  const returnContext = useRef<{ path: string; id?: number }>({ path: "" });
  const fullPath = location.pathname + location.search;
  if (returnContext.current.path !== fullPath) returnContext.current = { path: fullPath };
  const pendingRecordId = positiveInteger(pendingId);
  if (pendingRecordId && snapshot?.ordinalById.has(pendingRecordId)) returnContext.current.id = pendingRecordId;
  const returnId = pendingRecordId ?? returnContext.current.id ?? positiveInteger(params.get("lead_return"));
  const returnPosition = filtered.findIndex((record) => record.id === returnId);
  const pages = Math.max(1, Math.ceil(filtered.length / COMPARISON_LEAD_PAGE_SIZE));
  const requestedPage = positiveInteger(params.get("lead_page")) ?? 1;
  // Leave legacy URLs intact on return; reveal the actual registered record
  // before the route hook restores focus. Explicit navigation emits pages.
  const page = returnPosition >= 0 ? Math.floor(returnPosition / COMPARISON_LEAD_PAGE_SIZE) + 1
    : state === "success" ? Math.min(requestedPage, pages) : requestedPage;
  const visible = filtered.slice((page - 1) * COMPARISON_LEAD_PAGE_SIZE, page * COMPARISON_LEAD_PAGE_SIZE);
  const outsideFilter = returnId !== undefined && records.some((record) => record.id === returnId) && returnPosition < 0;
  const [announcement, setAnnouncement] = useState("");
  const requestedFocus = useRef<{ id: number | undefined; from: string; search: string; ownership: FocusContinuation } | undefined>(undefined);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const sectionRef = useRef<HTMLElement>(null);
  const reconciliationOwner = useRef<HTMLElement | null>(null);
  const previousPage = useRef({ path: fullPath, page });
  useLayoutEffect(() => {
    const remember = (event: FocusEvent) => {
      const target = event.target;
      reconciliationOwner.current = target instanceof HTMLElement && sectionRef.current?.contains(target) ? target : null;
    };
    const retire = () => { reconciliationOwner.current = null; };
    const keyIntent = (event: KeyboardEvent) => {
      if (["Tab", "PageDown", "PageUp", "ArrowDown", "ArrowUp", "Home", "End", " "].includes(event.key)) retire();
    };
    document.addEventListener("focusin", remember, true);
    document.addEventListener("pointerdown", retire, true);
    document.addEventListener("keydown", keyIntent, true);
    window.addEventListener("wheel", retire, { passive: true });
    window.addEventListener("blur", retire);
    return () => {
      document.removeEventListener("focusin", remember, true);
      document.removeEventListener("pointerdown", retire, true);
      document.removeEventListener("keydown", keyIntent, true);
      window.removeEventListener("wheel", retire);
      window.removeEventListener("blur", retire);
    };
  }, []);
  useLayoutEffect(() => {
    const owned = reconciliationOwner.current;
    if (owned && !owned.isConnected) {
      reconciliationOwner.current = null;
      const explicit = requestedFocus.current;
      const explicitOwns = explicit?.search === params.toString() && explicit.ownership.owns();
      if (!explicitOwns && document.hasFocus() && (document.activeElement === document.body || !document.activeElement)) headingRef.current?.focus();
    }
    const previous = previousPage.current;
    if (previous.path === fullPath && previous.page !== page && state === "success") {
      setAnnouncement(`Comparison lead records changed. Showing page ${page} of ${pages}.`);
    }
    previousPage.current = { path: fullPath, page };
  }, [fullPath, page, pages, state, snapshot, params]);
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
  function commit(next: URLSearchParams) {
    void navigate({ pathname: location.pathname, search: next.toString(), hash: location.hash }, { replace: true, state: location.state });
  }
  function selectFilter(nextFilter: ComparisonLeadFilter, revealId?: number) {
    const next = new URLSearchParams(params);
    if (nextFilter === "all") next.delete("lead_view"); else next.set("lead_view", nextFilter);
    next.delete("lead_visible"); next.delete("lead_return");
    const targetRecords = records.filter((record) => nextFilter === "all" || (nextFilter === "resolved" ? record.overridden : !record.overridden));
    const position = targetRecords.findIndex((record) => record.id === revealId);
    next.set("lead_page", String(position >= 0 ? Math.floor(position / COMPARISON_LEAD_PAGE_SIZE) + 1 : 1));
    requestedFocus.current?.ownership.dispose(); requestedFocus.current = undefined;
    if (position >= 0 && document.hasFocus()) requestedFocus.current = { id: revealId, from: params.toString(), search: next.toString(), ownership: claimFocusContinuation() };
    setAnnouncement(`${nextFilter === "all" ? "All" : nextFilter === "open" ? "Open" : "Resolved"} comparison leads. ${targetRecords.length} records.`);
    commit(next);
  }
  function selectPage(nextPage: number) {
    const first = filtered[(nextPage - 1) * COMPARISON_LEAD_PAGE_SIZE];
    const next = new URLSearchParams(params);
    next.set("lead_page", String(nextPage)); next.delete("lead_visible"); next.delete("lead_return");
    requestedFocus.current?.ownership.dispose(); requestedFocus.current = undefined;
    if (first && document.hasFocus()) requestedFocus.current = { id: first.id, from: params.toString(), search: next.toString(), ownership: claimFocusContinuation() };
    setAnnouncement(`Comparison leads, page ${nextPage} of ${pages}.`);
    commit(next);
  }
  const progress = state === "fetching" ? snapshot ? "Updating comparison leads. Showing last loaded records." : "Loading comparison leads."
    : state === "paused" ? snapshot ? "Waiting for a connection. Showing last loaded records." : "Waiting for a connection to load comparison leads."
      : state === "error" ? snapshot ? "Comparison leads could not be refreshed. Showing last loaded records." : "Comparison leads could not be loaded."
        : state === "unavailable" ? COMPARISON_LEAD_COPY.unavailable : "";
  const count = `${filtered.length} ${filter === "all" ? "" : filter + " "}${filtered.length === 1 ? "record" : "records"}`;
  const workspaceEmpty = workspace && snapshot?.total === 0 && state === "success";
  const workspaceUnknown = workspace && !snapshot;
  const title = workspaceEmpty ? "No recorded evidence items" : workspaceUnknown ? "Evidence review" : COMPARISON_LEAD_COPY.title;
  return <section ref={sectionRef} className="signal-comparison-index" aria-labelledby={COMPARISON_LEAD_HEADING_ID} aria-busy={state === "fetching"}>
    <div className="signal-comparison-index__heading"><Heading id={COMPARISON_LEAD_HEADING_ID} ref={headingRef} tabIndex={-1}>{title}</Heading>{snapshot && !workspaceEmpty && <span>{count}</span>}</div>
    {!workspaceEmpty && !workspaceUnknown && <p className="signal-comparison-index__qualification">{COMPARISON_LEAD_COPY.qualification}</p>}
    <p className={progress ? "signal-comparison-index__status" : "sr-only"} role={state === "error" ? "alert" : "status"} aria-live="polite" aria-atomic="true">{progress || announcement}</p>
    {state === "error" && onRetry && <Button variant="quiet" onClick={() => { if (document.hasFocus()) headingRef.current?.focus(); onRetry(); }}>Try again</Button>}
    {snapshot && records.length > 0 && <>
      <div className="signal-comparison-toolbar"><div role="group" aria-label="Filter comparison leads" className="signal-comparison-filters">{(["all", "open", "resolved"] as const).map((value) => <button type="button" key={value} aria-pressed={filter === value} onClick={() => selectFilter(value)}>{value[0].toUpperCase() + value.slice(1)}</button>)}</div>
        <button type="button" className="signal-comparison-about-toggle" aria-expanded={aboutOpen} aria-controls={aboutId} onClick={() => setAboutOpen((open) => !open)}>About leads <span aria-hidden="true">{aboutOpen ? "⌃" : "⌄"}</span></button>
      </div>
      <div id={aboutId} hidden={!aboutOpen} className="signal-comparison-index__about"><p>{COMPARISON_LEAD_COPY.about}</p>{showActions && viewedIds && <p>{COMPARISON_LEAD_COPY.viewed}</p>}</div>
      {outsideFilter && <div role="status" className="signal-comparison-index__status"><p>Record #{returnId} is outside the current filter.</p><Button variant="quiet" onClick={() => selectFilter("all", returnId)}>Show all comparison leads</Button></div>}
      {visible.length > 0 ? <ol className="signal-comparison-list">{visible.map((record) => {
        const identity = comparisonLeadIdentity(record, snapshot);
        const actionId = `comparison-lead-${record.id}`;
        const title = problemLabel(record.problem_kind) ?? "Recorded comparison";
        const anchor = instructorAnchor(record.page_anchor);
        const fullName = `${identity.label} ${title}. ${anchor}.`;
        const row = <><span className="signal-comparison-row__marker" aria-hidden="true">{identity.marker}</span><span className="signal-comparison-row__body"><strong>{title}</strong><span className="signal-comparison-row__meta"><span>{anchor}</span><span>Record #{record.id}</span></span>{record.overridden ? <span className="signal-comparison-row__state">Resolved by instructor</span> : showActions && viewedIds?.has(record.id) ? <span className="signal-comparison-row__state">Viewed</span> : null}</span></>;
        return <li key={record.id}>{showActions ? onSelect
          ? <button className="signal-comparison-row" id={actionId} type="button" aria-label={`Review evidence for ${fullName}`} onClick={() => onSelect(record.id)}>{row}<TrailingChevronIcon /></button>
          : <WorkflowLink className="signal-comparison-row" id={actionId} to={`/flags/${record.id}`} aria-label={`Review evidence for ${fullName}`} state={{ routeReturnFocus: { returnPath: location.pathname + location.search, elementId: actionId, fallbackElementId: COMPARISON_LEAD_HEADING_ID } }}>{row}<TrailingChevronIcon /></WorkflowLink>
          : <div className="signal-comparison-public-row"><div className="signal-comparison-row signal-comparison-row--readonly">{row}</div><details className="signal-comparison-evidence"><summary id={actionId} aria-label={`Evidence for ${fullName}`}>Evidence</summary>
            {isManuscriptQuote(record) ? <blockquote>“{systemFindingCopy(record.evidence_excerpt, record.problem_kind, "evidence")}”</blockquote> : <p>{systemFindingCopy(record.evidence_excerpt, record.problem_kind, "evidence")}</p>}
            <p>{anchor}</p>{record.first_upload_context && <p>First-upload context: the comparison archive was limited when this signal was created.</p>}
          </details></div>}</li>;
      })}</ol> : state === "success" && <p className="signal-comparison-index__status">No {filter} comparison leads. <button type="button" onClick={() => selectFilter("all")}>Show all</button></p>}
      {pages > 1 && <nav className="signal-comparison-pagination" aria-label="Comparison lead pages"><button type="button" disabled={page <= 1} onClick={() => selectPage(page - 1)}>Previous</button>{Array.from({ length: pages }, (_, index) => index + 1).map((number) => <button type="button" key={number} aria-label={`Page ${number}`} aria-current={page === number ? "page" : undefined} onClick={() => selectPage(number)}>{number}</button>)}<button type="button" disabled={page >= pages} onClick={() => selectPage(page + 1)}>Next</button></nav>}
    </>}
    {state === "success" && snapshot?.total === 0 && <p>{workspaceEmpty ? "This check has no integrity findings or comparison leads. Review assessment limits and readiness before recording your decision." : COMPARISON_LEAD_COPY.empty}</p>}
    {lastLoaded && <span className="sr-only">Counts describe last loaded records.</span>}
  </section>;
}
