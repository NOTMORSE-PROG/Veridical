import { useEffect, useRef } from "react";
import { useLocation, useSearchParams } from "react-router";
import type { AgreementScopeOut } from "../api/types";
import { FLAG_LIST_INITIAL_COUNT } from "../config/ui";
import { AGREEMENT_SCOPE_COPY, foundLine, outcomeLines, rolePhrase, setAsideLines } from "../domain/agreementScope";
import { instructorAnchor } from "../domain/sourceAnchor";
import { rememberRouteReturnFocus } from "../routing/useRouteFocus";
import { reviewDeskDestination } from "../routing/reviewDeskReturn";
import { ActionLink } from "../ui/ActionLink";
import { Button } from "../ui/Button";

const OPEN_PARAM = "set_aside";
const SHOWN_PARAM = "set_aside_shown";

/** What the internal-agreement check covered: what it found, what it set
 * aside and why, and what the comparison found. It says what was done, so a
 * check with no findings never reads as one that did not run (BUG-617). */
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

  const found = foundLine(scope);
  const setAside = scope.results_section_identified === false ? [] : setAsideLines(scope);
  const outcome = findings === null ? [] : outcomeLines(scope, { findings, undecided, compared, surface: "screen" });
  const count = items.length;
  const visible = items.slice(0, shown);
  const one = count === 1;
  return <section className="signal-agreement-scope" aria-labelledby={`${id}-title`}>
    <h3 id={`${id}-title`}>{AGREEMENT_SCOPE_COPY.heading}</h3>
    <p className="signal-agreement-scope__purpose">{AGREEMENT_SCOPE_COPY.purpose}</p>
    {found && <p><strong>{found}</strong></p>}
    {setAside.length > 0 && <p>{setAside.join(" ")}</p>}
    {scope.results_section_identified === false && <p>{AGREEMENT_SCOPE_COPY.notIdentified}</p>}
    {outcome.length > 0 && <p>{outcome.join(" ")}</p>}
    {interrupted && <p>{AGREEMENT_SCOPE_COPY.interrupted}</p>}
    <p>{AGREEMENT_SCOPE_COPY.limit}</p>
    {scope.absence_leads_withheld && <p>{AGREEMENT_SCOPE_COPY.absence}</p>}
    {scope.results_section_identified == null && <p>{AGREEMENT_SCOPE_COPY.olderNoSections}</p>}
    {(count > 0 || limitsLink) && <div className="signal-control-cluster">
      {count > 0 && <Button variant="secondary" aria-expanded={open} aria-controls={`${id}-list`}
        onClick={() => update((next) => { if (open) { next.delete(OPEN_PARAM); next.delete(SHOWN_PARAM); } else next.set(OPEN_PARAM, "open"); })}>
        {open ? (one ? "Hide the result set aside" : `Hide the ${count} results set aside`) : one ? "Show the result set aside" : `Show the ${count} results set aside`}
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

/** The adviser view's copy: the same coverage, without the statements set
 * aside (the shared copy does not carry them) and without list controls. */
export function AgreementScopeSummary({ scope, findings, undecided, compared }: {
  scope: AgreementScopeOut;
  findings: number;
  undecided: number;
  compared: number | null;
}) {
  const found = foundLine(scope);
  const setAside = scope.results_section_identified === false ? [] : setAsideLines(scope);
  const outcome = outcomeLines(scope, { findings, undecided, compared, surface: "adviser" });
  return <section className="signal-agreement-scope" aria-labelledby="shared-agreement-scope-title">
    <h4 id="shared-agreement-scope-title">{AGREEMENT_SCOPE_COPY.heading}</h4>
    <p className="signal-agreement-scope__purpose">{AGREEMENT_SCOPE_COPY.purpose}</p>
    {found && <p><strong>{found}</strong></p>}
    {setAside.length > 0 && <p>{setAside.join(" ")} {AGREEMENT_SCOPE_COPY.adviserSetAside}</p>}
    {scope.results_section_identified === false && <p>{AGREEMENT_SCOPE_COPY.notIdentified}</p>}
    {outcome.length > 0 && <p>{outcome.join(" ")}</p>}
    <p>{AGREEMENT_SCOPE_COPY.limit}</p>
    {scope.absence_leads_withheld && <p>{AGREEMENT_SCOPE_COPY.absence}</p>}
    {scope.results_section_identified == null && <p>{AGREEMENT_SCOPE_COPY.olderNoSections}</p>}
  </section>;
}
