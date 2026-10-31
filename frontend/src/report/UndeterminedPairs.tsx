import { useEffect, useRef } from "react";
import { useLocation, useSearchParams } from "react-router";
import type { FlagSummaryOut, UndeterminedPairsOut } from "../api/types";
import { FLAG_LIST_INITIAL_COUNT } from "../config/ui";
import { instructorAnchor } from "../domain/sourceAnchor";
import { rememberRouteReturnFocus } from "../routing/useRouteFocus";
import { reviewDeskDestination } from "../routing/reviewDeskReturn";
import { ActionLink } from "../ui/ActionLink";
import { Button } from "../ui/Button";

const OPEN_PARAM = "undecided";
const SHOWN_PARAM = "undecided_shown";

type Item =
  | { kind: "pair"; key: string; intentText: string; intentAnchor: string; outcomeText: string; outcomeAnchor: string; reason: string | null }
  | { kind: "legacy"; key: string; flagId: number; intentText: string; intentAnchor: string };

function itemsFrom(undetermined: UndeterminedPairsOut | null | undefined, legacy: FlagSummaryOut[]): Item[] {
  const pairs: Item[] = (undetermined?.pairs ?? []).map((pair, index) => ({
    kind: "pair", key: `pair-${index}`, intentText: pair.intent_text, intentAnchor: pair.intent_anchor,
    outcomeText: pair.outcome_text, outcomeAnchor: pair.outcome_anchor, reason: pair.reason ?? null,
  }));
  const saved: Item[] = legacy.map((flag) => ({
    kind: "legacy", key: `flag-${flag.id}`, flagId: flag.id, intentText: flag.evidence_excerpt, intentAnchor: flag.page_anchor,
  }));
  return [...pairs, ...saved].sort((a, b) => pageOf(a.intentAnchor) - pageOf(b.intentAnchor));
}

function pageOf(anchor: string): number {
  const match = /(\d+)/.exec(anchor);
  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

/** Objective and result pairs the agreement check compared but could not
 * decide. They make no claim about the manuscript, so they are shown once,
 * after the findings, and never counted as findings. */
export function UndeterminedPairs({ checkRunId, undetermined, legacy, incomplete = false }: {
  checkRunId: number;
  undetermined?: UndeterminedPairsOut | null;
  legacy: FlagSummaryOut[];
  incomplete?: boolean;
}) {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const items = itemsFrom(undetermined, legacy);
  const open = params.get(OPEN_PARAM) === "open";
  const shown = Math.max(FLAG_LIST_INITIAL_COUNT, Number(params.get(SHOWN_PARAM)) || FLAG_LIST_INITIAL_COUNT);
  const focusKey = useRef<string | null>(null);
  const listId = `undecided-pairs-${checkRunId}`;

  useEffect(() => {
    if (!focusKey.current) return;
    document.getElementById(`${listId}-${focusKey.current}`)?.focus();
    focusKey.current = null;
  });

  if (items.length === 0) return null;
  const count = items.length;
  // Older runs saved no compared count; the pairs listed are then all that is known.
  const compared = undetermined ? Math.max(undetermined.compared_count, count) : null;
  const one = count === 1;

  function update(change: (next: URLSearchParams) => void) {
    const next = new URLSearchParams(params);
    change(next);
    setParams(next, { replace: true });
  }

  function showMore() {
    focusKey.current = items[shown]?.key ?? null;
    update((next) => next.set(SHOWN_PARAM, String(shown + FLAG_LIST_INITIAL_COUNT)));
  }

  const visible = items.slice(0, shown);
  return <section className="signal-copy-flow signal-undecided-pairs" aria-labelledby={`${listId}-title`}>
    <p id={`${listId}-title`}>{compared === null
      ? `VERIDICAL could not decide on ${count} objective and result pair${one ? "" : "s"}.`
      : `VERIDICAL could not decide on ${count} of ${compared} objective and result pair${compared === 1 ? "" : "s"} it compared.`}</p>
    <p>For these pairs, the check could not tell whether the result answers the objective. This is not a problem found in the manuscript. If objective coverage matters for this defense, read the pairs and decide yourself.</p>
    {incomplete && <p>This check did not finish, so pairs it never reached are not listed.</p>}
    <Button variant="secondary" aria-expanded={open} aria-controls={listId}
      onClick={() => update((next) => { if (open) { next.delete(OPEN_PARAM); next.delete(SHOWN_PARAM); } else next.set(OPEN_PARAM, "open"); })}>
      {open ? `Hide ${one ? "the pair" : `${count} pairs`}` : one ? "Show the pair" : `Show ${count} pairs`}
    </Button>
    <div id={listId} hidden={!open}>
      {open && <>
        <h4>Pairs to read yourself</h4>
        <ol className="signal-undecided-pairs__list">{visible.map((item, index) => {
          const number = `pair ${index + 1} of ${count}`;
          return <li key={item.key}>
            <h5 id={`${listId}-${item.key}`} tabIndex={-1}>Pair {index + 1} of {count}</h5>
            <Statement label="Statement selected as an objective" text={item.intentText} anchor={item.intentAnchor}
              linkLabel={`Open objective location, ${instructorAnchor(item.intentAnchor)}, ${number}`}
              checkRunId={checkRunId} controlId={`${listId}-${item.key}-objective`} location={location} />
            {item.kind === "pair"
              ? <>
                <Statement label="Statement selected as a result" text={item.outcomeText} anchor={item.outcomeAnchor}
                  linkLabel={`Open result location, ${instructorAnchor(item.outcomeAnchor)}, ${number}`}
                  checkRunId={checkRunId} controlId={`${listId}-${item.key}-result`} location={location} />
                {item.reason && <p className="signal-field-hint">Reason recorded by the check: {item.reason}</p>}
              </>
              : <ActionLink to={`/flags/${item.flagId}`} variant="quiet" aria-label={`Review this pair, ${number}`}>Review this pair</ActionLink>}
          </li>;
        })}</ol>
        <p role="status" className="signal-field-hint">Showing {visible.length} of {count} pairs.</p>
        {visible.length < count && <Button variant="secondary" onClick={showMore}>Show {Math.min(FLAG_LIST_INITIAL_COUNT, count - visible.length)} more</Button>}
      </>}
    </div>
  </section>;
}

export function Statement({ label, text, anchor, linkLabel, checkRunId, controlId, location }: {
  label: string; text: string; anchor: string; linkLabel: string; checkRunId: number; controlId: string;
  location: ReturnType<typeof useLocation>;
}) {
  const sourcePath = reviewDeskDestination(`/report/${checkRunId}/document?anchor=${encodeURIComponent(anchor)}`, location);
  return <div className="signal-attached-flow">
    <p><strong>{label}</strong></p>
    <blockquote className="signal-evidence-quote"><span>“{text}”</span>{anchor.trim() && <cite>{instructorAnchor(anchor)}</cite>}</blockquote>
    {anchor.trim()
      ? <ActionLink id={controlId} to={sourcePath} variant="quiet" aria-label={linkLabel}
        onClick={() => rememberRouteReturnFocus(location.pathname + location.search, sourcePath, controlId)}>Open location</ActionLink>
      : <p>No manuscript location was recorded for this statement.</p>}
  </div>;
}
