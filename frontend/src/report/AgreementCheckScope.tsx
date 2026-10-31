import { useEffect, useRef } from "react";
import { useLocation, useSearchParams } from "react-router";
import type { AgreementScopeOut } from "../api/types";
import { FLAG_LIST_INITIAL_COUNT } from "../config/ui";
import { AGREEMENT_SCOPE_COPY, foundLine, interruptedLine, matchedLine, outcomeLines, rolePhrase, setAsideLines } from "../domain/agreementScope";
import { instructorAnchor } from "../domain/sourceAnchor";
import { rememberRouteReturnFocus } from "../routing/useRouteFocus";
import { reviewDeskDestination } from "../routing/reviewDeskReturn";
import { ActionLink } from "../ui/ActionLink";
import { Button } from "../ui/Button";

const OPEN_PARAM = "set_aside";
const SHOWN_PARAM = "set_aside_shown";

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
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const focusIndex = useRef<number | null>(null);
  const id = `agreement-scope-${checkRunId}`;
  const items = scope.set_aside ?? [];
  const open = params.get(OPEN_PARAM) === "open";
  const shown = Math.max(FLAG_LIST_INITIAL_COUNT, Number(params.get(SHOWN_PARAM)) || FLAG_LIST_INITIAL_COUNT);

  useEffect(() => {
    if (focusIndex.current === null) return;
    document.getElementById(`${id}-item-${focusIndex.current}`)?.focus();
    focusIndex.current = null;
  });

  function update(change: (next: URLSearchParams) => void) {
    const next = new URLSearchParams(params);
    change(next);
    setParams(next, { replace: true });
  }

  const outcome = findings === null ? [] : outcomeLines(scope, { findings, undecided, compared, interrupted, surface: "screen" });
  const count = items.length;
  const visible = items.slice(0, shown);
  const one = count === 1;
  return <section className="signal-agreement-scope" aria-labelledby={`${id}-title`}>
    {/* Focusable so manuscript review's link can bring the instructor here. */}
    <h3 id={`${id}-title`} tabIndex={-1} className="signal-agreement-scope__title">{AGREEMENT_SCOPE_COPY.heading}</h3>
    <ScopeLines scope={scope} outcome={outcome} compared={compared} interrupted={interrupted} />
    {(count > 0 || limitsLink) && <div className="signal-control-cluster">
      {count > 0 && <Button variant="secondary" aria-expanded={open} aria-controls={`${id}-list`}
        onClick={() => update((next) => { if (open) { next.delete(OPEN_PARAM); next.delete(SHOWN_PARAM); } else next.set(OPEN_PARAM, "open"); })}>
        {`${open ? "Hide" : "Show"} ${one ? "the result from another part of the paper" : `the ${count} results from other parts of the paper`}`}
      </Button>}
      {limitsLink && <ActionLink to={`/report/${checkRunId}?section=limits`} variant="secondary">Open assessment limits</ActionLink>}
    </div>}
    {count > 0 && <div id={`${id}-list`} hidden={!open}>
      {open && <>
        <h4>{AGREEMENT_SCOPE_COPY.setAsideListHeading}</h4>
        <p>{AGREEMENT_SCOPE_COPY.setAsideListIntro}</p>
        <ol className="signal-agreement-scope__list">{visible.map((item, index) => {
          const anchor = item.page_anchor?.trim() ?? "";
          const controlId = `${id}-open-${index}`;
          const sourcePath = reviewDeskDestination(`/report/${checkRunId}/document?anchor=${encodeURIComponent(anchor)}`, location);
          const place = rolePhrase(item.role);
          return <li key={`${index}:${item.text}`}>
            <h5 id={`${id}-item-${index}`} tabIndex={-1}>Result {index + 1} of {count}, {place}</h5>
            <blockquote className="signal-evidence-quote"><span>“{item.text}”</span>{anchor && <cite>{instructorAnchor(anchor)}</cite>}</blockquote>
            {anchor
              ? <ActionLink id={controlId} to={sourcePath} variant="quiet"
                aria-label={`Open location, ${instructorAnchor(anchor)}, set-aside result ${index + 1} of ${count}`}
                onClick={() => rememberRouteReturnFocus(location.pathname + location.search, sourcePath, controlId)}>Open location</ActionLink>
              : <p>No manuscript location was recorded for this statement.</p>}
          </li>;
        })}</ol>
        <p role="status" className="signal-field-hint">Showing {visible.length} of {count} results set aside.</p>
        {visible.length < count && <Button variant="secondary" onClick={() => {
          focusIndex.current = visible.length;
          update((next) => next.set(SHOWN_PARAM, String(shown + FLAG_LIST_INITIAL_COUNT)));
        }}>Show {Math.min(FLAG_LIST_INITIAL_COUNT, count - visible.length)} more</Button>}
      </>}
    </div>}
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
    {outcome.length > 0 && <p className="signal-agreement-scope__outcome"><strong>{outcome.join(" ")}</strong></p>}
    {stopped && <p>{stopped}</p>}
    {found && <p>{found}</p>}
    {setAside.length > 0 && <p>{setAside.join(" ")}{adviser && !notIdentified && scope.n_results_set_aside ? ` ${AGREEMENT_SCOPE_COPY.adviserSetAside}` : ""}</p>}
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
  return <section className="signal-agreement-scope" aria-labelledby="shared-agreement-scope-title">
    <h4 id="shared-agreement-scope-title" className="signal-agreement-scope__title">{AGREEMENT_SCOPE_COPY.heading}</h4>
    <ScopeLines scope={scope} outcome={outcome} compared={compared} interrupted={interrupted} adviser />
  </section>;
}
