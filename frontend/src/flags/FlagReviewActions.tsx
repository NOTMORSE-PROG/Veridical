// F8, screens 4i/4w: the same instructor actions beside either source view.
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { useLocation } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../api/client";
import type { FlagOut } from "../api/types";
import { TEST_AI_DISCLOSURE, UNKNOWN_AI_DISCLOSURE } from "../domain/aiModeDisclosure";
import { isOptionalComparisonLead } from "../domain/comparisonLeadIdentity";
import { isAssessmentRevision, keepNewerAssessment } from "../report/assessmentRevision";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { useExplicitActionFocus } from "../routing/useExplicitActionFocus";
import { ConfirmCitationSourceModal } from "./ConfirmCitationSourceModal";
import { useAnnotateFlag, useConfirmFinding, useOverrideFlag } from "./useFlag";
import { useFlagActionReview } from "./useFlagActionReview";
import { FlagActionCurrentReview } from "./FlagActionCurrentReview";
import { useFindingCompletion, useFindingCompletionOwner } from "./useFindingCompletion";
import { useAuthOwner } from "../auth/queryOwner";
import { fieldRefusal, formRefusal, isValidationRefusal, REFUSAL_NOTHING_CHANGED, useRefusalFocus } from "../api/fieldRefusal";

const authenticatedLifetimes = new WeakMap<object, number>();
let nextAuthenticatedLifetime = 0;
type AnnotationScope = { actor: number | undefined; values: Map<string, string>; stop?: () => void };
const annotationDrafts = new WeakMap<object, AnnotationScope>();

export function FindingModeDisclosure({ mode, registry = false, id }: { mode: FlagOut["llm_mode"]; registry?: boolean; id?: string }) {
  if (mode === "real") return null;
  return <div id={id} className="signal-flag-mode">
    <strong>{registry && mode === "fake" ? "Check recorded in a test-mode run"
      : mode === "fake" ? TEST_AI_DISCLOSURE.title : UNKNOWN_AI_DISCLOSURE.title}</strong>
    <p>{registry && mode === "fake"
      ? "This record belongs to a test-mode run. Do not treat its saved provider values as evidence of a fresh external lookup."
      : mode === "fake" ? TEST_AI_DISCLOSURE.body
        : registry ? UNKNOWN_AI_DISCLOSURE.registry : UNKNOWN_AI_DISCLOSURE.finding}</p>
  </div>;
}

function AnnotationPanel({ flag, drafts }: { flag: FlagOut; drafts: Map<string, string> }) {
  const isCurrent = useFindingCompletionOwner(`${flag.check_run_id}:${flag.id}:annotation`);
  const noteHelpId = useId();
  const draftKey = `${flag.check_run_id}:${flag.id}`;
  const [value, setValue] = useState(() => drafts.get(draftKey) ?? flag.annotation ?? "");
  const [dirty, setDirty] = useState(() => drafts.has(draftKey));
  const [attempted, setAttempted] = useState(false);
  const [saved, setSaved] = useState(false);
  const annotate = useAnnotateFlag(flag.id);
  const errorId = useId();
  const refusalId = useId();
  const invalid = attempted && value.trim().length === 0;
  const refusal = fieldRefusal(annotate.error, "annotation") ?? formRefusal(annotate.error, ["annotation"]);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  useRefusalFocus(refusal, noteRef);

  useEffect(() => {
    const draft = drafts.get(draftKey);
    if (draft !== undefined && draft.trim() === (flag.annotation ?? "")) { drafts.delete(draftKey); setDirty(false); }
    if (!drafts.has(draftKey)) setValue(flag.annotation ?? "");
  }, [flag.annotation, drafts, draftKey]);

  function save() {
    setAttempted(true);
    setSaved(false);
    if (!isCurrent() || !value.trim()) return;
    annotate.mutate(value.trim(), { onSuccess: () => {
      if (isCurrent()) { drafts.delete(draftKey); setDirty(false); setSaved(true); }
    } });
  }

  return (
    <section className="signal-evidence-section signal-annotation-panel" aria-labelledby="annotation-heading">
      <div className="signal-section-heading">
        <div><p className="signal-section-kicker">Private workspace</p><h2 id="annotation-heading">Private note</h2></div>
      </div>
      <p>Your note stays private to this instructor account and is not included in a shared report.</p>
      <label className="signal-evidence-field">
        <span>Note</span>
        <textarea
          ref={noteRef}
          rows={4}
          value={value}
          aria-invalid={invalid || Boolean(refusal) || undefined}
          aria-describedby={invalid ? errorId : refusal ? refusalId : undefined}
          disabled={annotate.isPending}
          onChange={(event) => {
            if (refusal) annotate.reset();
            setValue(event.target.value);
            if (event.target.value === (flag.annotation ?? "")) drafts.delete(draftKey);
            else drafts.set(draftKey, event.target.value);
            setDirty(drafts.has(draftKey));
            setSaved(false);
            if (attempted) setAttempted(false);
          }}
        />
      </label>
      {dirty && <p className="signal-field-hint">Unsaved note kept for this finding while you stay signed in.</p>}
      {invalid && <p id={errorId} role="alert" className="signal-field-error">Enter a note before saving.</p>}
      {!invalid && refusal && <p id={refusalId} role="alert" className="signal-field-error">{refusal} {REFUSAL_NOTHING_CHANGED}</p>}
      {annotate.isError && !isValidationRefusal(annotate.error) && <Alert title="Could not save this note" tone="error" role="alert">{annotate.error instanceof ApiError ? annotate.error.message : "Try again."}</Alert>}
      <div className="signal-evidence-actions">
        <Button variant="secondary" aria-describedby={noteHelpId} busy={annotate.isPending} onClick={save}>{annotate.isPending ? "Saving note" : "Save note"}</Button>
        {saved && !annotate.isPending && <span role="status" aria-live="polite" className="signal-save-state">Note saved.</span>}
        {dirty && <Button variant="quiet" disabled={annotate.isPending} onClick={() => {
          drafts.delete(draftKey); setDirty(false); setValue(flag.annotation ?? ""); setSaved(false);
        }}>Discard unsaved note</Button>}
      </div>
      <p id={noteHelpId} className="signal-field-hint">Saves your note without changing the result. It is not included in shared reports.</p>
    </section>
  );
}

const confirmedAtFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

// Agreeing with a finding needs no justification, so the note is optional.
function ConfirmFinding({ flag }: { flag: FlagOut }) {
  const [note, setNote] = useState("");
  const confirm = useConfirmFinding(flag.id);
  const review = useFlagActionReview(flag, "confirm-finding");
  const noteId = useId();
  const refusalId = useId();
  const refusal = fieldRefusal(confirm.error, "note") ?? formRefusal(confirm.error, ["note"]);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  useRefusalFocus(refusal, noteRef);
  if (flag.finding_confirmed) return <div className="signal-finding-confirmed" role="status">
    <strong>Confirmed by you{flag.finding_confirmed_at ? ` on ${confirmedAtFormatter.format(new Date(flag.finding_confirmed_at))}` : ""}.</strong>
    <p>{flag.finding_confirm_note ? `Your note: ${flag.finding_confirm_note}` : "No note was added."} It still counts for readiness. You can dismiss it instead if you change your mind.</p>
  </div>;
  return <div className="signal-finding-confirm">
    <h3>Is this a real issue?</h3>
    <p>Confirm it to mark it as checked. It keeps counting for readiness.</p>
    <label htmlFor={noteId}>Comment (optional)</label>
    <textarea ref={noteRef} id={noteId} rows={2} value={note} disabled={confirm.isPending}
      aria-invalid={Boolean(refusal) || undefined} aria-describedby={refusal ? refusalId : undefined}
      onChange={event => { if (refusal) confirm.reset(); setNote(event.target.value); }} />
    {refusal && <p id={refusalId} role="alert" className="signal-field-error">{refusal} {REFUSAL_NOTHING_CHANGED}</p>}
    {confirm.isError && !isValidationRefusal(confirm.error) && <p role="alert" className="signal-field-error">{confirm.error instanceof Error ? confirm.error.message : "The finding could not be confirmed. Try again."}</p>}
    <Button variant="primary" disabled={confirm.isPending || !review.canSubmit} onClick={() => {
      if (!review.isCurrent() || !isAssessmentRevision(review.expectedRevision)) return;
      confirm.mutate({ note: note.trim() || null, expected_revision: review.expectedRevision });
    }}>{confirm.isPending ? "Confirming" : "Confirm finding"}</Button>
  </div>;
}

function OverrideDialog({ flag, onClose, onAccepted, continuationIsCurrent, fallbackFocusRef }: { flag: FlagOut; onClose: () => void; onAccepted: (flag: FlagOut) => boolean; continuationIsCurrent: () => boolean; fallbackFocusRef: React.RefObject<HTMLHeadingElement | null> }) {
  const [reason, setReason] = useState("");
  const [attempted, setAttempted] = useState(false);
  const override = useOverrideFlag(flag.id);
  const review = useFlagActionReview(flag, "override");
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const followAction = useExplicitActionFocus(() => reasonRef.current);
  const errorId = useId();
  const refusalId = useId();
  const invalid = attempted && reason.trim().length === 0;
  const refusal = fieldRefusal(override.error, "reason") ?? formRefusal(override.error, ["reason"]);
  useRefusalFocus(refusal, reasonRef);
  const completion = useFindingCompletion();
  const [accepted, setAccepted] = useState(false);

  function confirm() {
    setAttempted(true);
    if (accepted || !review.isCurrent() || !reason.trim() || !review.canSubmit || !isAssessmentRevision(review.expectedRevision)) return;
    completion.begin(reasonRef.current);
    override.mutate({ reason: reason.trim(), expected_revision: review.expectedRevision }, {
      onSuccess: out => {
        if (review.isCurrent() && onAccepted(out)) { completion.accept(); setAccepted(true); }
        else review.freeze();
      },
      // A refusal of the reason is definite and changes nothing: keep the
      // review open so the instructor can correct the reason and try again.
      onError: (error) => { if (!isValidationRefusal(error)) review.freeze(); },
    });
  }

  return (
    <Dialog
      title="Dismiss this finding?"
      fallbackFocusRef={fallbackFocusRef}
      returnFocusTarget={() => completion.returnTarget(fallbackFocusRef.current)}
      shouldRestoreFocus={() => completion.shouldRestore(continuationIsCurrent)}
      initialFocusTitle
      onClose={override.isPending || accepted ? undefined : onClose}
      actions={<><Button variant="secondary" disabled={override.isPending || accepted} onClick={onClose}>Cancel</Button><Button variant="brand" disabled={!review.canSubmit || accepted} busy={override.isPending} onClick={confirm}>Confirm dismissal</Button></>}
    >
      <div className="signal-resolution-dialog" aria-busy={override.isPending}>
        <p>The original possible inconsistency remains in the evidence record. Your reason is recorded in Audit and the readiness band is recalculated.</p>
        <FlagActionCurrentReview review={review} busy={override.isPending} onRebind={trigger => followAction(trigger, review.rebind)} />
        <label className="signal-evidence-field">
          <span>Reason (required)</span>
          <textarea
            ref={reasonRef}
            rows={4}
            value={reason}
            disabled={override.isPending}
            aria-invalid={invalid || Boolean(refusal) || undefined}
            aria-describedby={invalid ? errorId : refusal ? refusalId : undefined}
            onChange={(event) => {
              if (refusal) override.reset();
              setReason(event.target.value);
              review.reasonChanged();
              if (attempted) setAttempted(false);
            }}
          />
        </label>
        {invalid && <p id={errorId} role="alert" className="signal-field-error">Enter a reason before confirming.</p>}
        {!invalid && refusal && <p id={refusalId} role="alert" className="signal-field-error">{refusal} {REFUSAL_NOTHING_CHANGED}</p>}
        {override.isError && !isValidationRefusal(override.error) && <Alert title="Saving could not be confirmed" tone="error" role="alert">Your entered reason is kept. Review the current assessment before trying again.</Alert>}
      </div>
    </Dialog>
  );
}

function ResolutionControl({ flag: observedFlag }: { flag: FlagOut }) {
  const client = useQueryClient();
  const location = useLocation();
  const isCurrent = useFindingCompletionOwner(`${location.pathname}:${observedFlag.check_run_id}:${observedFlag.id}:resolution`);
  const [receipt, setReceipt] = useState<FlagOut>();
  const flag = receipt && isCurrent() ? keepNewerAssessment(observedFlag, receipt) : observedFlag;
  useEffect(() => {
    if (receipt && isAssessmentRevision(observedFlag.assessment_revision)
      && isAssessmentRevision(receipt.assessment_revision)
      && observedFlag.assessment_revision >= receipt.assessment_revision) setReceipt(undefined);
  }, [observedFlag.assessment_revision, receipt]);
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const overrideTriggerRef = useRef<HTMLButtonElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const confirmSourceEligible = flag.check_kind === "citation_integrity" && flag.ai_verdict_summary === "unverifiable_not_found";

  function closeOverride() {
    setOverrideOpen(false);
  }

  function accept(out: FlagOut) {
    const current = client.getQueryData<FlagOut>(["flag", observedFlag.id]);
    const report = client.getQueryData<{ assessment_revision: number | null }>(["report", observedFlag.check_run_id]);
    if (!isCurrent() || out.id !== observedFlag.id || out.check_run_id !== observedFlag.check_run_id
      || !out.overridden || !isAssessmentRevision(out.assessment_revision)
      || (current && keepNewerAssessment(current, out) !== out)
      || (report && isAssessmentRevision(report.assessment_revision) && report.assessment_revision > out.assessment_revision)) return false;
    setReceipt(out);
    setOverrideOpen(false);
    setConfirmOpen(false);
    return true;
  }

  const terminal = flag.overridden;
  const optional = flag.affects_readiness === false;
  const headingId = terminal ? "resolution-heading" : "resolution-options-heading";
  const content = <section className={terminal ? "signal-resolution-record" : optional ? "signal-evidence-section" : "signal-evidence-section signal-resolution-options"} aria-labelledby={headingId}>
    <div className={terminal ? undefined : "signal-section-heading"}><div>
      {!optional || terminal ? <p className="signal-section-kicker">{terminal ? "Instructor resolution" : "Instructor action"}</p> : null}
      <h2 ref={headingRef} tabIndex={-1} id={headingId}>{terminal ? flag.confirmed_citation_source ? "Source confirmed" : "Finding dismissed" : optional ? "Review lead: not scored" : "Resolve only after checking the evidence"}</h2>
    </div></div>
    {terminal ? <>
        <p>{flag.confirmed_citation_source ? `You confirmed the source after checking it: ${flag.override_reason}.` : `You dismissed this possible inconsistency: ${flag.override_reason}.`}</p>
        {flag.confirmed_citation_source && <p>Your confirmation applies only to this exact reference in your account, not to another reference or instructor. It does not establish support for a manuscript claim.</p>}
        <p>{isOptionalComparisonLead(flag) ? "The original comparison record remains above. This lead does not affect readiness." : "The original finding remains above, and the readiness band has been recalculated."}</p>
        <ActionLink to={`/report/${flag.check_run_id}`} variant="secondary">View updated report</ActionLink>
      </>
    : optional ? <>
        <p>This comparison does not change readiness. You can inspect the evidence without writing a comment or dismissing it.</p>
      </>
    : <>
      <p>{flag.ai_verdict_summary ? "If you think this finding is wrong, dismiss it and explain why. The original evidence stays in the review record." : "VERIDICAL did not reach a conclusion. Review the evidence before deciding whether to dismiss this finding."}</p>
      {confirmSourceEligible && (flag.citation_source_key ? (
        <div className="signal-confirm-source-option">
          <h3>Verified the source yourself?</h3>
          <p>Opens a form to record where you checked this source. Confirming closes this finding. Later checks may reuse your confirmation only for this exact reference and source key in your account.</p>
          {flag.citation_source_key.kind === "title" && <Alert title="Title matches are less precise" tone="warning">Double-check that this is the source cited in the manuscript before confirming it.</Alert>}
          <Button variant="secondary" onClick={() => setConfirmOpen(true)}>Confirm this source</Button>
        </div>
      ) : (
        <Alert title="No source record can be confirmed" tone="info">VERIDICAL could not identify a DOI, ISBN, or title. If you verified the citation independently, dismiss only this finding and explain why.</Alert>
      ))}
      <ConfirmFinding flag={flag} />
      <div className="signal-evidence-actions"><Button ref={overrideTriggerRef} variant="secondary" onClick={() => setOverrideOpen(true)}>Dismiss finding</Button></div><p className="signal-field-hint">Opens a form for your reason. Nothing is dismissed until you confirm.</p>
    </>}
  </section>;
  return <>{content}
    {overrideOpen && <OverrideDialog key={`${flag.check_run_id}:${flag.id}:override`} flag={flag} fallbackFocusRef={headingRef} continuationIsCurrent={isCurrent} onAccepted={accept} onClose={closeOverride} />}
    {confirmOpen && <ConfirmCitationSourceModal key={`${flag.check_run_id}:${flag.id}:confirm-source`} flag={flag} fallbackFocusRef={headingRef} continuationIsCurrent={isCurrent} onAccepted={accept} onClose={() => setConfirmOpen(false)} />}
  </>;
}

export function FlagReviewActions({ flag }: { flag: FlagOut }) {
  const owner = useAuthOwner();
  const client = useQueryClient();
  useLayoutEffect(() => {
    const query = owner.authQuery;
    if (!query) return;
    let scope = annotationDrafts.get(query);
    if (!scope) {
      scope = { actor: owner.actorId, values: new Map() };
      annotationDrafts.set(query, scope);
    }
    if (scope.stop) return;
    const watched = scope;
    // The draft lifetime follows authentication even while review is closed.
    watched.stop = client.getQueryCache().subscribe(event => {
      if (event.query !== query) return;
      const nextActor = (query.state.data as { id?: number } | null)?.id;
      if (event.type === "removed" || nextActor !== watched.actor) {
        watched.values.clear();
        watched.actor = nextActor;
        authenticatedLifetimes.set(query, ++nextAuthenticatedLifetime);
      }
      if (event.type === "removed") { watched.stop?.(); annotationDrafts.delete(query); }
    });
  }, [client, owner.authQuery, owner.actorId]);
  if (owner.authQuery && annotationDrafts.get(owner.authQuery)?.actor !== owner.actorId) {
    annotationDrafts.set(owner.authQuery, { actor: owner.actorId, values: new Map() });
  }
  if (!owner.authQuery || !owner.isCurrent()) return null;
  if (!annotationDrafts.has(owner.authQuery)) annotationDrafts.set(owner.authQuery, { actor: owner.actorId, values: new Map() });
  if (!authenticatedLifetimes.has(owner.authQuery)) authenticatedLifetimes.set(owner.authQuery, ++nextAuthenticatedLifetime);
  return <div key={`${owner.actorId}:${authenticatedLifetimes.get(owner.authQuery)}:${flag.check_run_id}:${flag.id}`} className="signal-flag-actions">
    <ResolutionControl flag={flag} />
    <details className="signal-flag-annotation"><summary>Private note</summary><AnnotationPanel flag={flag} drafts={annotationDrafts.get(owner.authQuery)!.values} /></details>
  </div>;
}
