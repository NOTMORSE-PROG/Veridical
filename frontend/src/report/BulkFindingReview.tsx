// V-089: many findings can be confirmed as real issues, or dismissed, in one
// action. The dialog names what is selected and how much of it has not been
// opened, so a bulk dismissal is never blind.
import { useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ApiError } from "../api/client";
import type { FlagSummaryOut } from "../api/types";
import { BULK_RESOLVE_LISTED_FINDINGS, BULK_RESOLVE_MAX_LOCATIONS, BULK_RESOLVE_MAX_TEXT_CHARS } from "../config/ui";
import { BULK_REVIEW_COPY, plural } from "../domain/bulkReviewCopy";
import { problemLabel } from "../domain/problemLabel";
import { instructorAnchor } from "../domain/sourceAnchor";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import type { FlagFindingCluster } from "./flagClusters";
import { useBulkResolveFlags, type BulkAction, type BulkResolveOut } from "./useBulkResolve";

// Several findings often share a title, so each line also names what it is
// about (the cited work, when there is one) and where it is.
function clusterTitle(cluster: FlagFindingCluster) {
  const first = cluster.flags[0];
  const title = problemLabel(first.problem_kind) ?? first.criterion_text ?? "Possible inconsistency";
  const subject = first.cited_text?.trim();
  return `${title}${subject ? `: ${subject}` : ""} · ${instructorAnchor(first.page_anchor)}`;
}

export function BulkSelectionBar({ findings, locations, decided, onAction, onClear }: {
  findings: number; locations: number; decided: boolean; onAction: (action: BulkAction) => void; onClear: () => void;
}) {
  if (findings === 0) return null;
  const tooMany = locations > BULK_RESOLVE_MAX_LOCATIONS;
  // Rendered on the page body: the bar is fixed to the screen, and the page's
  // own layout would otherwise carry it along with the content.
  return createPortal(<div className="signal-bulk-bar" role="region" aria-label="Selected findings">
    <p role="status" aria-live="polite"><strong>{BULK_REVIEW_COPY.selected(findings, locations)}</strong></p>
    {decided ? <p className="signal-field-hint">{BULK_REVIEW_COPY.decided}</p>
      : tooMany ? <p className="signal-field-hint" role="alert">{BULK_REVIEW_COPY.tooMany(locations)}</p> : null}
    <div className="signal-control-cluster">
      <Button variant="secondary" disabled={decided || tooMany} onClick={() => onAction("confirm")}>{BULK_REVIEW_COPY.confirm}</Button>
      <Button variant="secondary" disabled={decided || tooMany} onClick={() => onAction("dismiss")}>{BULK_REVIEW_COPY.dismiss}</Button>
      <Button variant="quiet" onClick={onClear}>{BULK_REVIEW_COPY.clear}</Button>
    </div>
  </div>, document.body);
}

export function BulkResolveDialog({ action, clusters, checkRunId, expectedRevision, viewedIds, onDone, onClose }: {
  action: BulkAction; clusters: FlagFindingCluster[]; checkRunId: number; expectedRevision: number | null | undefined;
  viewedIds: ReadonlySet<number>; onDone: (message: string) => void; onClose: () => void;
}) {
  const resolve = useBulkResolveFlags(checkRunId);
  const [text, setText] = useState("");
  const [attempted, setAttempted] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const fieldId = useId();
  // A confirmed citation source is already resolved and cannot be confirmed
  // as a real issue, so it is left out of a confirmation.
  const sourceConfirmed = action === "confirm" ? clusters.filter((cluster) => cluster.flags.some((flag) => flag.confirmed_citation_source)) : [];
  const included = clusters.filter((cluster) => !sourceConfirmed.includes(cluster));
  const flagIds = included.flatMap((cluster) => cluster.flags.map((flag) => flag.id));
  const unopened = included.filter((cluster) => !cluster.flags.some((flag) => viewedIds.has(flag.id))).length;
  const reopens = action === "confirm" ? included.filter((cluster) => cluster.flags.some((flag: FlagSummaryOut) => flag.overridden)).length : 0;
  const trimmed = text.trim();
  const missing = action === "dismiss" && trimmed.length === 0;
  const tooLong = trimmed.length > BULK_RESOLVE_MAX_TEXT_CHARS;
  const invalid = attempted && (missing || tooLong);
  const error = resolve.error instanceof ApiError ? resolve.error.message : resolve.error ? "The change could not be confirmed. Review the current findings before trying again." : null;

  function submit() {
    setAttempted(true);
    if (missing || tooLong || flagIds.length === 0 || expectedRevision == null) { textRef.current?.focus(); return; }
    resolve.mutate({
      action, flag_ids: flagIds, expected_revision: expectedRevision, unopened_count: unopened,
      ...(action === "confirm" ? { note: trimmed || null } : { reason: trimmed }),
    }, {
      onSuccess: (out: BulkResolveOut) => {
        const changed = new Set(out.flags.map((flag) => flag.id));
        const unchanged = new Set(out.unchanged_ids);
        const changedFindings = included.filter((cluster) => cluster.flags.some((flag) => changed.has(flag.id))).length;
        const unchangedFindings = included.filter((cluster) => cluster.flags.every((flag) => unchanged.has(flag.id))).length;
        onDone(BULK_REVIEW_COPY.done(action, changedFindings, unchangedFindings));
      },
    });
  }

  const listed = included.slice(0, BULK_RESOLVE_LISTED_FINDINGS);
  return <Dialog title={action === "confirm" ? BULK_REVIEW_COPY.confirmTitle(included.length) : BULK_REVIEW_COPY.dismissTitle(included.length)}
    initialFocusTitle onClose={resolve.isPending ? undefined : onClose}
    actions={<><Button variant="secondary" disabled={resolve.isPending} onClick={onClose}>{BULK_REVIEW_COPY.cancel}</Button>
      <Button variant="brand" busy={resolve.isPending} disabled={included.length === 0} onClick={submit}>
        {action === "confirm" ? BULK_REVIEW_COPY.confirm : BULK_REVIEW_COPY.dismiss}</Button></>}>
    <div className="signal-group-flow signal-bulk-dialog" aria-busy={resolve.isPending}>
      <ul className="signal-bulk-dialog__list">
        {listed.map((cluster) => <li key={cluster.key}>{clusterTitle(cluster)}{cluster.flags.length > 1 ? ` (${plural(cluster.flags.length, "location", "locations")})` : ""}</li>)}
        {included.length > listed.length && <li>{BULK_REVIEW_COPY.more(included.length - listed.length)}</li>}
      </ul>
      <p><strong>{BULK_REVIEW_COPY.unopened(unopened)}</strong></p>
      <p>{action === "confirm" ? BULK_REVIEW_COPY.confirmCounts : BULK_REVIEW_COPY.dismissCounts}</p>
      {reopens > 0 && <p>{BULK_REVIEW_COPY.reopens(reopens)}</p>}
      {sourceConfirmed.length > 0 && <p className="signal-field-hint">{BULK_REVIEW_COPY.sourceConfirmed(sourceConfirmed.length)}</p>}
      <div className="signal-field signal-evidence-field">
        <label htmlFor={fieldId}>{action === "confirm" ? BULK_REVIEW_COPY.note : BULK_REVIEW_COPY.reason}</label>
        <p id={`${fieldId}-hint`} className="signal-field-hint">{action === "confirm" ? BULK_REVIEW_COPY.noteHint : BULK_REVIEW_COPY.reasonHint}</p>
        <textarea id={fieldId} ref={textRef} rows={4} value={text} required={action === "dismiss"} disabled={resolve.isPending}
          aria-invalid={invalid || undefined} aria-describedby={`${fieldId}-hint ${fieldId}-count${invalid ? ` ${fieldId}-error` : ""}`}
          onChange={(event) => { setText(event.target.value); setAttempted(false); }} />
        <p id={`${fieldId}-count`} className="signal-field-hint" aria-live="polite">{BULK_REVIEW_COPY.counter(trimmed.length)}</p>
        {invalid && <p id={`${fieldId}-error`} role="alert" className="signal-field-error">{tooLong ? BULK_REVIEW_COPY.tooLong : BULK_REVIEW_COPY.reasonMissing}</p>}
      </div>
      {error && <Alert title="Nothing was changed" tone="error" role="alert">{error}</Alert>}
    </div>
  </Dialog>;
}
