import { useEffect, useRef } from "react";
import { useLocation, useSearchParams } from "react-router";
import type { FlagSummaryOut, UndeterminedPairsOut } from "../api/types";
import { FLAG_LIST_INITIAL_COUNT } from "../config/ui";
import { instructorAnchor } from "../domain/sourceAnchor";
import { rememberRouteReturnFocus } from "../routing/useRouteFocus";
import { reviewDeskDestination } from "../routing/reviewDeskReturn";
import { ActionLink } from "../ui/ActionLink";
import { Button } from "../ui/Button";
import type { EvidenceManifest } from "./evidenceTypes";
import { SharedPageButton } from "./SharedEvidencePacket";

const OPEN_PARAM = "undecided";
const SHOWN_PARAM = "undecided_shown";
// The adviser's cards count objectives, not pairs, so they keep their own count.
const GROUPS_PARAM = "undecided_objectives_shown";

type Item =
  | { kind: "pair"; key: string; intentText: string; intentAnchor: string; outcomeText: string; outcomeAnchor: string; reason: string | null }
  | { kind: "legacy"; key: string; flagId: number; intentText: string; intentAnchor: string };

type SavedPair = { intent_text: string; intent_anchor: string | null; outcome_text: string; outcome_anchor: string | null };

function itemsFrom(undetermined: UndeterminedPairsOut | null | undefined, legacy: FlagSummaryOut[], legacyPairs?: ReadonlyMap<number, SavedPair>): Item[] {
  const pairs: Item[] = (undetermined?.pairs ?? []).map((pair, index) => ({
    kind: "pair", key: `pair-${index}`, intentText: pair.intent_text, intentAnchor: pair.intent_anchor,
    outcomeText: pair.outcome_text, outcomeAnchor: pair.outcome_anchor, reason: pair.reason ?? null,
  }));
  // An older row whose saved pair is known shows both statements, like a
  // newer one; otherwise only its saved excerpt.
  const saved: Item[] = legacy.map((flag): Item => {
    const pair = legacyPairs?.get(flag.id);
    return pair
      ? { kind: "pair", key: `flag-${flag.id}`, intentText: pair.intent_text, intentAnchor: pair.intent_anchor ?? "", outcomeText: pair.outcome_text, outcomeAnchor: pair.outcome_anchor ?? "", reason: null }
      : { kind: "legacy", key: `flag-${flag.id}`, flagId: flag.id, intentText: flag.evidence_excerpt, intentAnchor: flag.page_anchor };
  });
  return [...pairs, ...saved].sort((a, b) => pageOf(a.intentAnchor) - pageOf(b.intentAnchor));
}

function pageOf(anchor: string): number {
  const match = /(\d+)/.exec(anchor);
  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

/** Objective and result pairs the agreement check compared but could not
 * decide. They make no claim about the manuscript, so they are shown once,
 * after the findings, and never counted as findings. */
export function UndeterminedPairs({ checkRunId, undetermined, legacy, incomplete = false, shared = false, legacyPairs, evidence }: {
  /** The shared link's original pages, when the adviser view has them. */
  evidence?: SharedPairsEvidence;
  /** Saved pairs for older rows, by record id, where the link carries them. */
  legacyPairs?: ReadonlyMap<number, SavedPair>;
  checkRunId: number;
  /** The adviser's read-only copy: the statements, with no links into the instructor's pages. */
  shared?: boolean;
  undetermined?: UndeterminedPairsOut | null;
  legacy: FlagSummaryOut[];
  incomplete?: boolean;
}) {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const items = itemsFrom(undetermined, legacy, legacyPairs);
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

  if (shared) {
    const groupsShown = Math.max(SHARED_GROUPS_SHOWN, Number(params.get(GROUPS_PARAM)) || SHARED_GROUPS_SHOWN);
    return <SharedPairs listId={listId} items={items} open={open} shown={groupsShown} evidence={evidence}
      onToggle={() => update((next) => { if (open) { next.delete(OPEN_PARAM); next.delete(GROUPS_PARAM); } else next.set(OPEN_PARAM, "open"); })}
      onMore={() => {
        focusKey.current = `objective-${groupsShown}`;
        update((next) => next.set(GROUPS_PARAM, String(groupsShown + SHARED_GROUPS_SHOWN)));
      }} />;
  }
  const visible = items.slice(0, shown);
  return <section className="signal-copy-flow signal-undecided-pairs" aria-labelledby={`${listId}-title`}>
    <p id={`${listId}-title`}>{compared === null
      ? `VERIDICAL could not decide on ${count} objective and result pair${one ? "" : "s"}.`
      : `VERIDICAL could not decide on ${count} of ${compared} objective and result pair${compared === 1 ? "" : "s"} it compared.`}</p>
    <p>For these pairs, the check could not tell whether the result answers the objective. This is not a problem found in the manuscript. {shared ? "They are listed here so you can read them." : "If objective coverage matters for this defense, read the pairs and decide yourself."}</p>
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
              checkRunId={checkRunId} controlId={`${listId}-${item.key}-objective`} location={location} link={!shared} />
            {item.kind === "pair"
              ? <>
                <Statement label="Statement selected as a result" text={item.outcomeText} anchor={item.outcomeAnchor}
                  linkLabel={`Open result location, ${instructorAnchor(item.outcomeAnchor)}, ${number}`}
                  checkRunId={checkRunId} controlId={`${listId}-${item.key}-result`} location={location} link={!shared} />
                {item.reason && <p className="signal-field-hint">Reason recorded by the check: {item.reason}</p>}
              </>
              : !shared && <ActionLink to={`/flags/${item.flagId}`} variant="quiet" aria-label={`Review this pair, ${number}`}>Review this pair</ActionLink>}
          </li>;
        })}</ol>
        <p role="status" className="signal-field-hint">Showing {visible.length} of {count} pairs.</p>
        {visible.length < count && <Button variant="secondary" onClick={showMore}>Show {Math.min(FLAG_LIST_INITIAL_COUNT, count - visible.length)} more</Button>}
      </>}
    </div>
  </section>;
}

export function Statement({ label, text, anchor, linkLabel, checkRunId, controlId, location, link = true }: {
  label: string; text: string; anchor: string; linkLabel: string; checkRunId: number; controlId: string;
  location: ReturnType<typeof useLocation>;
  /** Off on the shared view, which cannot open the instructor's manuscript review. */
  link?: boolean;
}) {
  const sourcePath = reviewDeskDestination(`/report/${checkRunId}/document?anchor=${encodeURIComponent(anchor)}`, location);
  return <div className="signal-attached-flow">
    <p><strong>{label}</strong></p>
    <blockquote className="signal-evidence-quote"><span>“{text}”</span>{anchor.trim() && <cite>{instructorAnchor(anchor)}</cite>}</blockquote>
    {!link ? null : anchor.trim()
      ? <ActionLink id={controlId} to={sourcePath} variant="quiet" aria-label={linkLabel}
        onClick={() => rememberRouteReturnFocus(location.pathname + location.search, sourcePath, controlId)}>Open location</ActionLink>
      : <p>No manuscript location was recorded for this statement.</p>}
  </div>;
}

// How many objective groups the adviser sees before asking for more.
const SHARED_GROUPS_SHOWN = 4;

/** The adviser's copy: each objective once, with the results it was compared
 * with under it, as compact cards a few at a time. Read-only. */
function SharedPairs({ listId, items, open, shown, onToggle, onMore, evidence }: {
  listId: string; items: Item[]; open: boolean; shown: number; evidence?: SharedPairsEvidence;
  onToggle: () => void; onMore: () => void;
}) {
  const groups = new Map<string, { text: string; anchor: string; results: { text: string; anchor: string }[] }>();
  for (const item of items) {
    const key = `${item.intentText}|${item.intentAnchor}`;
    const group = groups.get(key) ?? { text: item.intentText, anchor: item.intentAnchor, results: [] };
    if (item.kind === "pair" && !group.results.some(result => result.text === item.outcomeText && result.anchor === item.outcomeAnchor)) group.results.push({ text: item.outcomeText, anchor: item.outcomeAnchor });
    groups.set(key, group);
  }
  const all = [...groups.values()];
  const visible = all.slice(0, shown);
  const count = items.length;
  return <section className="signal-shared-pairs" aria-labelledby={`${listId}-title`}>
    <div className="signal-shared-pairs__head">
      <div>
        <p id={`${listId}-title`} className="signal-shared-pairs__title">{count} {count === 1 ? "pair" : "pairs"} the check could not decide</p>
        <p className="signal-field-hint">{all.length} {all.length === 1 ? "objective" : "objectives"} involved. Not findings; they do not change readiness.</p>
      </div>
      <Button variant="secondary" aria-expanded={open} aria-controls={listId} onClick={onToggle}>{open ? "Hide" : "Show"} the {all.length === 1 ? "objective" : "objectives"}</Button>
    </div>
    <div id={listId} hidden={!open}>
      {open && <>
        {evidence?.failed && <p className="signal-field-hint">Original pages could not be loaded. The quotations above are what VERIDICAL recorded.</p>}
        <ol className="signal-shared-pairs__grid">{visible.map((group, index) => <li key={`${group.text}|${group.anchor}`} id={`${listId}-objective-${index}`} tabIndex={-1} className="signal-pair-card">
          <p className="signal-pair-card__tag">Objective {index + 1}</p>
          <blockquote className="signal-pair-card__quote">“{group.text}”</blockquote>
          <PairSource evidence={evidence} side="objective" text={group.text} anchor={group.anchor}
            accessibleName={`Open ${instructorAnchor(group.anchor)}, objective ${index + 1}`}
            title={`Objective ${index + 1}${group.anchor.trim() ? `, ${instructorAnchor(group.anchor)}` : ""}`}
            counterpart={group.results} />
          {group.results.length > 0
            ? <div className="signal-pair-card__results">
              <p className="signal-pair-card__tag">Compared with {group.results.length} {group.results.length === 1 ? "result" : "results"}</p>
              <ul>{group.results.map((result, place) => <li key={`${result.text}|${result.anchor}`}><span>“{result.text}”</span>
                <PairSource evidence={evidence} side="result" text={result.text} anchor={result.anchor}
                  accessibleName={`Open ${instructorAnchor(result.anchor)}, result ${place + 1} of ${group.results.length} for objective ${index + 1}`}
                  title={`Result compared with objective ${index + 1}${result.anchor.trim() ? `, ${instructorAnchor(result.anchor)}` : ""}`}
                  counterpart={[{ text: group.text, anchor: group.anchor }]} /></li>)}</ul>
            </div>
            : <p className="signal-field-hint">The result it was compared with was not saved with this link.</p>}
        </li>)}</ol>
        {visible.length < all.length && <Button variant="secondary" onClick={onMore}>Show {Math.min(SHARED_GROUPS_SHOWN, all.length - visible.length)} more</Button>}
      </>}
    </div>
  </section>;
}

export interface SharedPairsEvidence {
  token: string;
  manifest?: EvidenceManifest;
  failed: boolean;
}

const SIDE_ROLES = {
  objective: ["undecided_intent", "agreement_intent"],
  result: ["undecided_outcome", "agreement_outcome"],
} as const;

/** A statement's page and the button that opens it. The reference is found by
 * its own quote and page, so a statement shown once stands for every pair it
 * is in. */
function PairSource({ evidence, side, text, anchor, accessibleName, title, counterpart }: {
  evidence?: SharedPairsEvidence; side: "objective" | "result"; text: string; anchor: string;
  accessibleName: string; title: string; counterpart: { text: string; anchor: string }[];
}) {
  const roles: readonly string[] = SIDE_ROLES[side];
  const reference = evidence?.manifest?.references.find(ref => roles.includes(ref.role) && ref.quote === text && (ref.anchor ?? "") === anchor);
  if (!anchor.trim()) return <span className="signal-pair-card__source signal-field-hint">No page was saved for this statement.</span>;
  const page = instructorAnchor(anchor);
  return <span className="signal-pair-card__source">
    {!(evidence?.manifest && reference) && <cite>{page}</cite>}
    {evidence?.manifest && reference && <SharedPageButton token={evidence.token} snapshot={evidence.manifest.snapshot_id} reference={reference}
      label={`Open ${page}`} className="signal-pair-card__open" accessibleName={accessibleName} title={title}
      context={<section aria-label="Statement and what it was compared with" className="signal-pair-dialog">
        <h3>{side === "objective" ? "Statement selected as an objective" : "Statement selected as a result"}</h3>
        <blockquote>“{text}”</blockquote>
        <h4>Compared with</h4>
        <ul>{counterpart.map(item => <li key={`${item.text}|${item.anchor}`}>“{item.text}”{item.anchor.trim() && <> <cite>{instructorAnchor(item.anchor)}</cite></>}</li>)}</ul>
        <p>VERIDICAL could not decide whether this result answers this objective. This is not a finding and does not change readiness. The statement roles have not been verified as this study's objective and result.</p>
      </section>} />}
  </span>;
}
