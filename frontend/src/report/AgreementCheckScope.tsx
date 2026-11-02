import { useEffect, useRef } from "react";
import { useLocation, useSearchParams } from "react-router";
import type { AgreementScopeOut, AgreementSetAsideOut } from "../api/types";
import { FLAG_LIST_INITIAL_COUNT } from "../config/ui";
import { AGREEMENT_SCOPE_COPY, foundLine, interruptedLine, matchedLine, objectivePlace, objectiveSetAsideLines, outcomeLines, rolePhrase, setAsideLines } from "../domain/agreementScope";
import { instructorAnchor } from "../domain/sourceAnchor";
import { rememberRouteReturnFocus } from "../routing/useRouteFocus";
import { reviewDeskDestination } from "../routing/reviewDeskReturn";
import { ActionLink } from "../ui/ActionLink";
import { Button } from "../ui/Button";

const OPEN_PARAM = "set_aside";
const SHOWN_PARAM = "set_aside_shown";
// BUG-639: the objectives set aside keep their own list state.
const OBJECTIVES_OPEN_PARAM = "objectives_set_aside";
const OBJECTIVES_SHOWN_PARAM = "objectives_set_aside_shown";

/** What the internal-agreement check covered: the comparison's outcome
 * first, then what it found, what it set aside and why. It says what was
 * done, so a check with no findings never reads as one that did not run
 * (BUG-617). */
export function AgreementCheckScope({ checkRunId, scope, findings, undecided, compared, interrupted, limitsLink }: {
  checkRunId: number;
  scope: AgreementScopeOut;
  /** Possible inconsistencies listed below; null while the list is unknown. */
  findings: number | null;
  undecided: number;
  compared: number | null;
  interrupted: boolean;
  limitsLink: boolean;
}) {
  const id = `agreement-scope-${checkRunId}`;
  const items = scope.set_aside ?? [];
  const objectives = scope.objectives_set_aside ?? [];
  const outcome = findings === null ? [] : outcomeLines(scope, { findings, undecided, compared, interrupted, surface: "screen" });
  const count = items.length;
  return <section className="signal-agreement-scope" aria-labelledby={`${id}-title`}>
    {/* Focusable so manuscript review's link can bring the instructor here. */}
    <h3 id={`${id}-title`} tabIndex={-1} className="signal-agreement-scope__title">{AGREEMENT_SCOPE_COPY.heading}</h3>
    <ScopeLines scope={scope} outcome={outcome} compared={compared} interrupted={interrupted} />
    {(count > 0 || objectives.length > 0 || limitsLink) && <div className="signal-control-cluster">
      {objectives.length > 0 && <SetAsideToggle id={`${id}-objectives`} kind="objective" count={objectives.length} openParam={OBJECTIVES_OPEN_PARAM} shownParam={OBJECTIVES_SHOWN_PARAM} />}
      {count > 0 && <SetAsideToggle id={id} kind="result" count={count} openParam={OPEN_PARAM} shownParam={SHOWN_PARAM} />}
      {limitsLink && <ActionLink to={`/report/${checkRunId}?section=limits`} variant="secondary">Open assessment limits</ActionLink>}
    </div>}
    <SetAsideList id={`${id}-objectives`} kind="objective" items={objectives} checkRunId={checkRunId} openParam={OBJECTIVES_OPEN_PARAM} shownParam={OBJECTIVES_SHOWN_PARAM} />
    <SetAsideList id={id} kind="result" items={items} checkRunId={checkRunId} openParam={OPEN_PARAM} shownParam={SHOWN_PARAM} />
  </section>;
}

/** The lines both copies share, in reading order: the outcome first and in
 * bold, then what it covered, its limit, and how the pairs were matched. */
function ScopeLines({ scope, outcome, compared, interrupted, adviser = false }: {
  scope: AgreementScopeOut;
  outcome: string[];
  compared: number | null;
  interrupted: boolean;
  adviser?: boolean;
}) {
  const found = [foundLine(scope), matchedLine(scope)].filter(Boolean).join(" ");
  const notIdentified = scope.results_section_identified === false;
  // With no results section found, nothing was set aside by its part; only
  // the too-short count still applies.
  const lines = setAsideLines(scope);
  const setAside = notIdentified ? (scope.n_results_too_short ? lines.slice(-1) : []) : lines;
  const stopped = interruptedLine(scope, { compared, interrupted, outcomeShown: outcome.length > 0 });
  return <>
    {outcome.length > 0 && <p className="signal-agreement-scope__outcome">{adviser ? outcome.join(" ") : <strong>{outcome.join(" ")}</strong>}</p>}
    {stopped && <p>{stopped}</p>}
    {found && !adviser && <p>{found}</p>}
    {objectiveSetAsideLines(scope).length > 0 && <p>{objectiveSetAsideLines(scope).join(" ")}</p>}
    {adviser && matchedLine(scope) && <p>{matchedLine(scope)}</p>}
    {setAside.length > 0 && <p>{setAside.join(" ")}</p>}
    {/* Said once, after both counts, however many lists were set aside. */}
    {adviser && (Boolean(scope.n_objectives_set_aside) || (!notIdentified && Boolean(scope.n_results_set_aside))) && <p>{AGREEMENT_SCOPE_COPY.adviserSetAside}</p>}
    {notIdentified && <p>{AGREEMENT_SCOPE_COPY.notIdentified}</p>}
    <p>{AGREEMENT_SCOPE_COPY.limit}</p>
    {scope.absence_leads_withheld && <p>{AGREEMENT_SCOPE_COPY.absence}</p>}
    {Boolean(compared) && <details className="signal-agreement-scope__matching">
      <summary>{AGREEMENT_SCOPE_COPY.matchingHeading}</summary>
      <p>{AGREEMENT_SCOPE_COPY.matchingCaveat}</p>
    </details>}
  </>;
}

/** The adviser view's copy: the same coverage, without the statements set
 * aside (the shared copy does not carry them) and without list controls. */
export function AgreementScopeSummary({ scope, findings, undecided, compared, interrupted }: {
  scope: AgreementScopeOut;
  findings: number;
  undecided: number;
  compared: number | null;
  interrupted: boolean;
}) {
  const outcome = outcomeLines(scope, { findings, undecided, compared, interrupted, surface: "adviser" });
  // The numbers first, as facts to scan; the sentences that qualify them after.
  const facts: [string, string][] = [
    ...(scope.n_objectives_extracted != null && scope.n_objectives_set_aside
      ? [["Objectives found", String(scope.n_objectives_extracted)], ["Objectives kept", String(scope.n_objectives ?? 0)], ["Objectives set aside", String(scope.n_objectives_set_aside)]] as [string, string][]
      : scope.n_objectives != null ? [["Objectives found", String(scope.n_objectives)] as [string, string]] : []),
    ...(scope.n_results != null ? [["Results found", String(scope.n_results)] as [string, string]] : []),
    ...(compared != null ? [["Pairs compared", String(compared)] as [string, string]] : []),
    ...(undecided > 0 ? [["Pairs not decided", String(undecided)] as [string, string]] : []),
  ];
  return <section className="signal-agreement-scope" aria-labelledby="shared-agreement-scope-title">
    <h4 id="shared-agreement-scope-title" className="signal-agreement-scope__title">{AGREEMENT_SCOPE_COPY.heading}</h4>
    <dl className="signal-fact-tiles">{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <ScopeLines scope={scope} outcome={outcome} compared={compared} interrupted={interrupted} adviser />
  </section>;
}

const LIST_COPY = {
  result: { one: "the result from another part of the paper", many: (n: number) => `the ${n} results from other parts of the paper`,
    heading: AGREEMENT_SCOPE_COPY.setAsideListHeading, intro: AGREEMENT_SCOPE_COPY.setAsideListIntro, item: "Result", noun: "set-aside result", status: "results set aside" },
  objective: { one: "the objective from another part of the paper", many: (n: number) => `the ${n} objectives from other parts of the paper`,
    heading: AGREEMENT_SCOPE_COPY.objectiveSetAsideListHeading, intro: AGREEMENT_SCOPE_COPY.objectiveSetAsideListIntro, item: "Objective", noun: "set-aside objective", status: "objectives set aside" },
} as const;

function useListState(openParam: string, shownParam: string) {
  const [params, setParams] = useSearchParams();
  const open = params.get(openParam) === "open";
  const shown = Math.max(FLAG_LIST_INITIAL_COUNT, Number(params.get(shownParam)) || FLAG_LIST_INITIAL_COUNT);
  function update(change: (next: URLSearchParams) => void) {
    const next = new URLSearchParams(params);
    change(next);
    setParams(next, { replace: true });
  }
  return { open, shown, update };
}

function SetAsideToggle({ id, kind, count, openParam, shownParam }: { id: string; kind: keyof typeof LIST_COPY; count: number; openParam: string; shownParam: string }) {
  const { open, update } = useListState(openParam, shownParam);
  const copy = LIST_COPY[kind];
  return <Button variant="secondary" aria-expanded={open} aria-controls={`${id}-list`}
    onClick={() => update((next) => { if (open) { next.delete(openParam); next.delete(shownParam); } else next.set(openParam, "open"); })}>
    {`${open ? "Hide" : "Show"} ${count === 1 ? copy.one : copy.many(count)}`}
  </Button>;
}

/** The statements set aside, eight at a time, each with its place and a link
 * to its location. Same list for objectives and results. */
function SetAsideList({ id, kind, items, checkRunId, openParam, shownParam }: {
  id: string; kind: keyof typeof LIST_COPY; items: AgreementSetAsideOut[]; checkRunId: number; openParam: string; shownParam: string;
}) {
  const location = useLocation();
  const { open, shown, update } = useListState(openParam, shownParam);
  const focusIndex = useRef<number | null>(null);
  useEffect(() => {
    if (focusIndex.current === null) return;
    document.getElementById(`${id}-item-${focusIndex.current}`)?.focus();
    focusIndex.current = null;
  });
  const count = items.length;
  if (!count) return null;
  const copy = LIST_COPY[kind];
  const visible = items.slice(0, shown);
  return <div id={`${id}-list`} className="signal-agreement-scope__set-aside" hidden={!open}>
    {open && <>
      <h4>{copy.heading}</h4>
      <p>{copy.intro}</p>
      <ol className="signal-agreement-scope__list">{visible.map((item, index) => {
        const anchor = item.page_anchor?.trim() ?? "";
        const controlId = `${id}-open-${index}`;
        const sourcePath = reviewDeskDestination(`/report/${checkRunId}/document?anchor=${encodeURIComponent(anchor)}`, location);
        const place = item.chapter?.trim() || (kind === "objective" ? objectivePlace(item.role) : rolePhrase(item.role));
        return <li key={`${index}:${item.text}`}>
          <h5 id={`${id}-item-${index}`} tabIndex={-1}>{copy.item} {index + 1} of {count}, {place}</h5>
          <blockquote className="signal-evidence-quote"><span>“{item.text}”</span>{anchor && <cite>{instructorAnchor(anchor)}</cite>}</blockquote>
          {anchor
            ? <ActionLink id={controlId} to={sourcePath} variant="quiet"
              aria-label={`Open location, ${instructorAnchor(anchor)}, ${copy.noun} ${index + 1} of ${count}`}
              onClick={() => rememberRouteReturnFocus(location.pathname + location.search, sourcePath, controlId)}>Open location</ActionLink>
            : <p>No manuscript location was recorded for this statement.</p>}
        </li>;
      })}</ol>
      <p role="status" className="signal-field-hint">Showing {visible.length} of {count} {copy.status}.</p>
      {visible.length < count && <Button variant="secondary" onClick={() => {
        focusIndex.current = visible.length;
        update((next) => next.set(shownParam, String(shown + FLAG_LIST_INITIAL_COUNT)));
      }}>Show {Math.min(FLAG_LIST_INITIAL_COUNT, count - visible.length)} more</Button>}
    </>}
  </div>;
}
