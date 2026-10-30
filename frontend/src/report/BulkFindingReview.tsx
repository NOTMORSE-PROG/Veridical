// V-089: many findings can be confirmed as real issues, or dismissed, in one
// action. The dialog names each finding, whether it was opened and whether it
// counts for readiness, so neither action is taken blind.
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ApiError } from "../api/client";
import { BULK_BAR_INLINE_MEDIA_QUERY, BULK_RESOLVE_MAX_LOCATIONS, BULK_RESOLVE_MAX_TEXT_CHARS, RESOLUTION_REASON_MIN_LENGTH } from "../config/ui";
import { BULK_REVIEW_COPY, plural } from "../domain/bulkReviewCopy";
import { findingSubject, findingTitle, severityLabel } from "../domain/findingSummary";
import { countsForReadiness } from "../domain/readinessExclusion";
import { instructorAnchor } from "../domain/sourceAnchor";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { useMediaQuery } from "../ui/useMediaQuery";
import type { FlagFindingCluster } from "./flagClusters";
import { useBulkResolveFlags, type BulkAction, type BulkResolveOut } from "./useBulkResolve";
import { fieldRefusal, formRefusal, isServiceBusy, isValidationRefusal, REFUSAL_NOTHING_CHANGED, SERVICE_BUSY_COPY, useRefusalFocus } from "../api/fieldRefusal";
import { RefusalAlert } from "../ui/RefusalAlert";

// While the bar is fixed to the screen, its measured height keeps focused
// controls clear of it (scroll padding reads this variable).
function useBarHeight(node: HTMLElement | null) {
  useEffect(() => {
    if (!node || typeof ResizeObserver === "undefined") return;
    const root = document.documentElement;
    const observer = new ResizeObserver(() => root.style.setProperty("--signal-bulk-bar-height", `${Math.ceil(node.getBoundingClientRect().height)}px`));
    observer.observe(node);
    return () => { observer.disconnect(); root.style.removeProperty("--signal-bulk-bar-height"); };
  }, [node]);
}

// Rendered twice by the findings panel: "before" the list draws the bar when
// it sits in the page, "after" draws it when it is fixed to the screen.
export function BulkSelectionBar({ slot, findings, locations, elsewhere, decided, onAction, onClear }: {
  slot: "before" | "after";
  findings: number; locations: number; elsewhere: number; decided: boolean; onAction: (action: BulkAction) => void; onClear: () => void;
}) {
  // On a narrow window (or a zoomed one) a fixed bar would cover the page, so
  // it sits in the flow above the list instead.
  const inline = useMediaQuery(BULK_BAR_INLINE_MEDIA_QUERY, false);
  const [node, setNode] = useState<HTMLDivElement | null>(null);
  useBarHeight(inline || slot !== "after" ? null : node);
  if (findings === 0 || (inline ? slot !== "before" : slot !== "after")) return null;
  const tooMany = locations > BULK_RESOLVE_MAX_LOCATIONS;
  const bar = <div ref={setNode} className="signal-bulk-bar" data-placement={inline ? "inline" : "fixed"} role="region" aria-label="Selected findings">
    <p role="status" aria-live="polite"><strong>{BULK_REVIEW_COPY.selected(findings, locations)}</strong>
      {elsewhere > 0 && <> {elsewhere === findings ? BULK_REVIEW_COPY.noneShown : BULK_REVIEW_COPY.elsewhere(elsewhere)}</>}</p>
    {decided ? <p className="signal-field-hint">{BULK_REVIEW_COPY.decided}</p>
      : tooMany ? <p className="signal-field-hint" role="alert">{BULK_REVIEW_COPY.tooMany(locations)}</p> : null}
    <div className="signal-control-cluster">
      <Button variant="secondary" disabled={decided || tooMany} onClick={() => onAction("confirm")}>{BULK_REVIEW_COPY.confirm}</Button>
      <Button variant="secondary" disabled={decided || tooMany} onClick={() => onAction("dismiss")}>{BULK_REVIEW_COPY.dismiss}</Button>
      <Button variant="quiet" onClick={onClear}>{BULK_REVIEW_COPY.clear}</Button>
    </div>
  </div>;
  // Fixed bars are rendered on the page body; the page's own layout would
  // otherwise carry a fixed element along with the content.
  return inline ? bar : createPortal(bar, document.body);
}

function openedState(cluster: FlagFindingCluster, viewedIds: ReadonlySet<number>) {
  const opened = cluster.flags.filter((flag) => viewedIds.has(flag.id)).length;
  return { opened, complete: opened === cluster.flags.length };
}

function Row({ cluster, viewedIds }: { cluster: FlagFindingCluster; viewedIds: ReadonlySet<number> }): ReactNode {
  const first = cluster.flags[0];
  const { opened, complete } = openedState(cluster, viewedIds);
  const counted = cluster.flags.some(countsForReadiness);
  const subject = findingSubject(first);
  return <li>
    <span className="signal-bulk-dialog__title"><strong>{findingTitle(first)}</strong> <span className="signal-bulk-dialog__page">· {instructorAnchor(first.page_anchor)}</span>
      {cluster.flags.length > 1 ? ` (${plural(cluster.flags.length, "location", "locations")})` : ""}</span>
    <span className="signal-bulk-dialog__subject">{subject}</span>
    <span className="signal-bulk-dialog__meta">
      <span>{severityLabel(first)} · {counted ? BULK_REVIEW_COPY.counted : BULK_REVIEW_COPY.notCounted}</span>
      <span className={complete ? "signal-viewed-state" : "signal-bulk-dialog__unopened"}>
        {complete ? BULK_REVIEW_COPY.opened : opened > 0 ? BULK_REVIEW_COPY.partlyOpened(opened, cluster.flags.length) : BULK_REVIEW_COPY.notOpened}</span>
      <a className="signal-text-link" href={`/flags/${first.id}`} target="_blank" rel="noopener"
        aria-label={`${BULK_REVIEW_COPY.reviewEvidence}: ${findingTitle(first)}, ${instructorAnchor(first.page_anchor)}, ${subject} (${BULK_REVIEW_COPY.opensNewTab})`}>{BULK_REVIEW_COPY.reviewEvidence}</a>
    </span>
  </li>;
}

export function BulkResolveDialog({ action, clusters, checkRunId, expectedRevision, viewedIds, text, onText, onDone, onClose }: {
  action: BulkAction; clusters: FlagFindingCluster[]; checkRunId: number; expectedRevision: number | null | undefined;
  viewedIds: ReadonlySet<number>; text: string; onText: (text: string) => void; onDone: (message: string) => void; onClose: () => void;
}) {
  const resolve = useBulkResolveFlags(checkRunId);
  const [attempted, setAttempted] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const acknowledgeRef = useRef<HTMLInputElement>(null);
  const fieldId = useId();
  // A confirmed citation source is already resolved and cannot be confirmed
  // as a real issue, so it is left out of a confirmation.
  const sourceConfirmed = action === "confirm" ? clusters.filter((cluster) => cluster.flags.some((flag) => flag.confirmed_citation_source)) : [];
  const included = clusters.filter((cluster) => !sourceConfirmed.includes(cluster));
  const flagIds = included.flatMap((cluster) => cluster.flags.map((flag) => flag.id));
  // A finding counts as opened only when every location was opened, as on the report.
  const unopened = included.filter((cluster) => !openedState(cluster, viewedIds).complete).length;
  const counted = included.filter((cluster) => cluster.flags.some(countsForReadiness)).length;
  const trimmed = text.trim();
  const missing = action === "dismiss" && trimmed.length < RESOLUTION_REASON_MIN_LENGTH;
  const tooLong = trimmed.length > BULK_RESOLVE_MAX_TEXT_CHARS;
  // Too long is said as it happens; a missing reason only after trying.
  const invalid = tooLong || (attempted && missing);
  // Either action on findings that were not opened here needs an explicit
  // word that they were checked some other way. Confirming counts against
  // the student, so it is guarded as firmly as dismissing.
  const needsAcknowledgement = unopened > 0;
  const unacknowledged = attempted && needsAcknowledgement && !acknowledged;
  // A refusal of this dialog's own text shows under it; anything else the
  // server refused (the selection, the request as a whole) is said once below.
  const textField = action === "confirm" ? "note" : "reason";
  const refusal = fieldRefusal(resolve.error, textField);
  useRefusalFocus(refusal, textRef);
  // Only a refusal (4xx) is known to have changed nothing; a failure or an
  // unreadable answer may have been applied, so it is not said to have not.
  const refused = isServiceBusy(resolve.error) || (resolve.error instanceof ApiError && resolve.error.status >= 400 && resolve.error.status < 500);
  const error = isValidationRefusal(resolve.error) ? formRefusal(resolve.error, [textField], BULK_REVIEW_COPY.requestRefused)
    : isServiceBusy(resolve.error) ? SERVICE_BUSY_COPY.body
    : refused && resolve.error instanceof ApiError ? resolve.error.message
      : resolve.error ? BULK_REVIEW_COPY.unconfirmedBody : null;

  function submit() {
    setAttempted(true);
    if (missing || tooLong || flagIds.length === 0 || expectedRevision == null) { textRef.current?.focus(); return; }
    if (needsAcknowledgement && !acknowledged) { acknowledgeRef.current?.focus(); return; }
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

  return <Dialog title={action === "confirm" ? BULK_REVIEW_COPY.confirmTitle(included.length) : BULK_REVIEW_COPY.dismissTitle(included.length)}
    initialFocusTitle onClose={resolve.isPending ? undefined : onClose}
    actions={<><Button variant="secondary" disabled={resolve.isPending} onClick={onClose}>{BULK_REVIEW_COPY.cancel}</Button>
      {/* After an outcome nobody can know, review the findings first rather
          than sending the same write again. */}
      <Button variant="brand" busy={resolve.isPending} disabled={included.length === 0 || (Boolean(resolve.error) && !refused && !isValidationRefusal(resolve.error))} onClick={submit}>
        {action === "confirm" ? BULK_REVIEW_COPY.confirm : BULK_REVIEW_COPY.dismiss}</Button></>}>
    <div className="signal-group-flow signal-bulk-dialog" aria-busy={resolve.isPending}>
      <p><strong>{BULK_REVIEW_COPY.unopened(unopened)}</strong></p>
      <p>{action === "confirm" ? BULK_REVIEW_COPY.confirmCounts(counted, included.length) : BULK_REVIEW_COPY.dismissCounts(counted, included.length)}</p>
      {sourceConfirmed.length > 0 && <p className="signal-field-hint">{BULK_REVIEW_COPY.sourceConfirmed(sourceConfirmed.length)}</p>}
      <ul className="signal-bulk-dialog__list" aria-label="Selected findings">
        {included.map((cluster) => <Row key={cluster.key} cluster={cluster} viewedIds={viewedIds} />)}
      </ul>
      <div className="signal-field signal-evidence-field">
        <label htmlFor={fieldId}>{action === "confirm" ? BULK_REVIEW_COPY.note : BULK_REVIEW_COPY.reason}</label>
        <p id={`${fieldId}-hint`} className="signal-field-hint">{action === "confirm" ? BULK_REVIEW_COPY.noteHint : BULK_REVIEW_COPY.reasonHint}</p>
        <textarea id={fieldId} ref={textRef} rows={4} value={text} required={action === "dismiss"} disabled={resolve.isPending}
          aria-invalid={invalid || Boolean(refusal) || undefined}
          aria-describedby={`${fieldId}-hint ${fieldId}-count${invalid ? ` ${fieldId}-error` : refusal ? ` ${fieldId}-refusal` : ""}`}
          onChange={(event) => { if (refusal) resolve.reset(); onText(event.target.value); setAttempted(false); }} />
        <p id={`${fieldId}-count`} className={tooLong ? "signal-field-error" : "signal-field-hint"}>{BULK_REVIEW_COPY.counter(trimmed.length)}</p>
        {invalid && <p id={`${fieldId}-error`} role="alert" className="signal-field-error">{tooLong ? BULK_REVIEW_COPY.tooLong : BULK_REVIEW_COPY.reasonMissing}</p>}
        {!invalid && refusal && <p id={`${fieldId}-refusal`} className="signal-field-error">{refusal} {REFUSAL_NOTHING_CHANGED}</p>}
      </div>
      {needsAcknowledgement && <div className="signal-field">
        <label className="signal-bulk-dialog__acknowledge">
          <input ref={acknowledgeRef} type="checkbox" checked={acknowledged} disabled={resolve.isPending}
            aria-invalid={unacknowledged || undefined} aria-describedby={unacknowledged ? `${fieldId}-ack-error` : undefined}
            onChange={(event) => { setAcknowledged(event.target.checked); setAttempted(false); }} />
          <span>{BULK_REVIEW_COPY.acknowledge(unopened)}</span>
        </label>
        {unacknowledged && <p id={`${fieldId}-ack-error`} role="alert" className="signal-field-error">{BULK_REVIEW_COPY.acknowledgeMissing}</p>}
      </div>}
      {error && <RefusalAlert title={refused ? BULK_REVIEW_COPY.refusedTitle : BULK_REVIEW_COPY.unconfirmedTitle} message={error} takeFocus={!refusal} />}
    </div>
  </Dialog>;
}
