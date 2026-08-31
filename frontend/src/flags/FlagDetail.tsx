import { useEffect, useId, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router";
import { ApiError } from "../api/client";
import type { FlagOut } from "../api/types";
import { checkKindMeta, humanize } from "../domain/checkKind";
import { problemLabel } from "../domain/problemLabel";
import { systemFindingCopy } from "../domain/systemFindingCopy";
import { rememberRouteReturnFocus, useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { ConfirmCitationSourceModal } from "./ConfirmCitationSourceModal";
import { useAnnotateFlag, useFlag, useOverrideFlag } from "./useFlag";

const SEVERITY_LABEL: Record<FlagOut["severity"], string> = {
  high: "High severity",
  med: "Medium severity",
  low: "Low severity",
};

function humanizedVerdict(flag: FlagOut): string {
  if (!flag.ai_verdict_summary) return "No determination recorded";
  return problemLabel(flag.ai_verdict_summary) ?? humanize(flag.ai_verdict_summary);
}

function ModeDisclosure({ mode }: { mode: FlagOut["llm_mode"] }) {
  if (mode === "real") return null;
  return mode === "fake" ? (
    <Alert title="Test-mode AI result" tone="warning">
      This finding came from fixture responses, not real AI grading. Do not treat it as a finding about the manuscript.
    </Alert>
  ) : (
    <Alert title="AI mode could not be verified" tone="warning">
      This finding predates reliable AI-mode tracking. Check the evidence directly before acting on it.
    </Alert>
  );
}

function AnnotationPanel({ flag }: { flag: FlagOut }) {
  const [value, setValue] = useState(flag.annotation ?? "");
  const [attempted, setAttempted] = useState(false);
  const [saved, setSaved] = useState(false);
  const annotate = useAnnotateFlag(flag.id);
  const errorId = useId();
  const invalid = attempted && value.trim().length === 0;

  useEffect(() => setValue(flag.annotation ?? ""), [flag.annotation]);

  function save() {
    setAttempted(true);
    setSaved(false);
    if (!value.trim()) return;
    annotate.mutate(value.trim(), { onSuccess: () => setSaved(true) });
  }

  return (
    <section className="signal-evidence-section signal-annotation-panel" aria-labelledby="annotation-heading">
      <div className="signal-section-heading">
        <div><p className="signal-section-kicker">Private workspace</p><h2 id="annotation-heading">Instructor annotation</h2></div>
      </div>
      <p>Your note stays private to this instructor account and is not included in a shared report.</p>
      <label className="signal-evidence-field">
        <span>Annotation</span>
        <textarea
          rows={4}
          value={value}
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? errorId : undefined}
          disabled={annotate.isPending}
          onChange={(event) => {
            setValue(event.target.value);
            setSaved(false);
            if (attempted) setAttempted(false);
          }}
        />
      </label>
      {invalid && <p id={errorId} role="alert" className="signal-field-error">Enter a note before saving.</p>}
      {annotate.isError && <Alert title="Could not save this annotation" tone="error" role="alert">{annotate.error instanceof ApiError ? annotate.error.message : "Try again."}</Alert>}
      <div className="signal-evidence-actions">
        <Button variant="secondary" busy={annotate.isPending} onClick={save}>{annotate.isPending ? "Saving annotation" : "Save annotation"}</Button>
        {saved && !annotate.isPending && <span role="status" aria-live="polite" className="signal-save-state">Annotation saved.</span>}
      </div>
    </section>
  );
}

function OverrideDialog({ flag, onClose }: { flag: FlagOut; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const [attempted, setAttempted] = useState(false);
  const override = useOverrideFlag(flag.id);
  const errorId = useId();
  const invalid = attempted && reason.trim().length === 0;

  function confirm() {
    setAttempted(true);
    if (!reason.trim()) return;
    override.mutate(reason.trim(), { onSuccess: onClose });
  }

  return (
    <Dialog
      title="Override this finding?"
      onClose={override.isPending ? undefined : onClose}
      actions={<><Button variant="secondary" disabled={override.isPending} onClick={onClose}>Cancel</Button><Button variant="brand" busy={override.isPending} onClick={confirm}>Confirm override</Button></>}
    >
      <div className="signal-resolution-dialog" aria-busy={override.isPending}>
        <p>The original possible inconsistency remains in the evidence record. Your reason is recorded in Audit and the readiness band is recalculated.</p>
        <label className="signal-evidence-field">
          <span>Reason (required)</span>
          <textarea
            autoFocus
            rows={4}
            value={reason}
            disabled={override.isPending}
            aria-invalid={invalid || undefined}
            aria-describedby={invalid ? errorId : undefined}
            onChange={(event) => {
              setReason(event.target.value);
              if (attempted) setAttempted(false);
            }}
          />
        </label>
        {invalid && <p id={errorId} role="alert" className="signal-field-error">Enter a reason before confirming.</p>}
        {override.isError && <Alert title="Could not override this finding" tone="error" role="alert">{override.error instanceof ApiError ? override.error.message : "Try again."}</Alert>}
      </div>
    </Dialog>
  );
}

function ResolutionControl({ flag }: { flag: FlagOut }) {
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const overrideTriggerRef = useRef<HTMLButtonElement>(null);
  const confirmSourceEligible = flag.check_kind === "citation_integrity" && flag.ai_verdict_summary === "unverifiable_not_found";

  function closeOverride() {
    setOverrideOpen(false);
    queueMicrotask(() => overrideTriggerRef.current?.focus());
  }

  if (flag.overridden) {
    return (
      <section className="signal-resolution-record" aria-labelledby="resolution-heading">
        <div><p className="signal-section-kicker">Instructor resolution</p><h2 id="resolution-heading">{flag.confirmed_citation_source ? "Source confirmed" : "Finding overridden"}</h2></div>
        <p>{flag.confirmed_citation_source ? `You confirmed the source after checking it: ${flag.override_reason}.` : `You overrode this possible inconsistency: ${flag.override_reason}.`}</p>
        {flag.confirmed_citation_source && <p>The verified-source record also applies when the same DOI, ISBN, or title appears in another manuscript. This action currently has no in-product undo.</p>}
        <p>The original finding remains above, and the readiness band has been recalculated.</p>
        <ActionLink to={`/report/${flag.check_run_id}`} variant="secondary">View updated report</ActionLink>
      </section>
    );
  }

  return (
    <section className="signal-evidence-section signal-resolution-options" aria-labelledby="resolution-options-heading">
      <div className="signal-section-heading"><div><p className="signal-section-kicker">Instructor action</p><h2 id="resolution-options-heading">Resolve only after checking the evidence</h2></div></div>
      <p>{flag.ai_verdict_summary ? "This finding remains open unless you record a reasoned override." : "VERIDICAL did not reach a determination, so this finding does not affect readiness unless you affirm it."}</p>
      {confirmSourceEligible && (flag.citation_source_key ? (
        <div className="signal-confirm-source-option">
          <h3>Verified the source yourself?</h3>
          <p>Confirming the source resolves this flag and updates the shared source record for any manuscript citing the same DOI, ISBN, or title.</p>
          {flag.citation_source_key.kind === "title" && <Alert title="Title matches are less precise" tone="warning">Double-check that it is the same source before confirming it across VERIDICAL.</Alert>}
          <Button variant="secondary" onClick={() => setConfirmOpen(true)}>Confirm this source</Button>
        </div>
      ) : (
        <Alert title="No source record can be confirmed" tone="info">VERIDICAL could not identify a DOI, ISBN, or title. If you verified the citation independently, override only this finding and explain why.</Alert>
      ))}
      <div className="signal-evidence-actions"><Button ref={overrideTriggerRef} variant="secondary" onClick={() => setOverrideOpen(true)}>Override</Button></div>
      {overrideOpen && <OverrideDialog flag={flag} onClose={closeOverride} />}
      {confirmOpen && <ConfirmCitationSourceModal flag={flag} onClose={() => setConfirmOpen(false)} />}
    </section>
  );
}

export function FlagDetailPage() {
  const { flagId } = useParams<{ flagId: string }>();
  const routeLocation = useLocation();
  const id = Number(flagId);
  const validId = Number.isInteger(id) && id > 0;
  const { data: flag, isPending, isError, error, refetch } = useFlag(id);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const resolutionRef = useRef<HTMLDivElement>(null);
  const previousResolved = useRef<boolean | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const returnFocus = (routeLocation.state as { routeReturnFocus?: { returnPath: string; elementId: string } } | null)?.routeReturnFocus;
  useRouteFocus("Evidence detail - VERIDICAL", headingRef, returnFocus);

  useEffect(() => {
    if (!flag) return;
    if (previousResolved.current === null) {
      previousResolved.current = flag.overridden;
      return;
    }
    if (flag.overridden && previousResolved.current === false) {
      setAnnouncement(flag.confirmed_citation_source ? "Source confirmed. The readiness band was recalculated." : "Finding overridden. The readiness band was recalculated.");
      resolutionRef.current?.focus();
    }
    previousResolved.current = flag.overridden;
  }, [flag]);

  const meta = flag ? checkKindMeta(flag.check_kind) : null;
  const sourcePath = flag ? `/report/${flag.check_run_id}/document?flag=${flag.id}` : "";
  const sourceLinkId = flag ? `flag-source-link-${flag.id}` : "";

  return (
    <div className="signal-route signal-flag-detail">
      <header className="signal-route-header signal-flag-header">
        <div>
          <nav aria-label="Breadcrumb"><Link to="/dashboard?queue=needs_review">Review Desk</Link><span aria-hidden="true">/</span>{flag && <><Link to={`/report/${flag.check_run_id}`}>{flag.manuscript_group_label}</Link><span aria-hidden="true">/</span></>}<span>Evidence</span></nav>
          <p className="signal-eyebrow">Review · Possible inconsistency</p>
          <h1 ref={headingRef} tabIndex={-1}>{meta?.title ?? "Evidence detail"}</h1>
          {flag?.criterion_text && <p className="signal-route-header__intro">Related criterion: {flag.criterion_text}</p>}
        </div>
        {flag && <div className="signal-flag-status"><span className={`signal-severity signal-severity--${flag.severity}`}>{SEVERITY_LABEL[flag.severity]}</span>{flag.overridden && <span className="signal-resolution-state">{flag.confirmed_citation_source ? "Source confirmed" : "Overridden"}</span>}</div>}
      </header>

      {!validId ? (
        <Alert title="This evidence address is invalid" tone="error" role="alert"><ActionLink to="/dashboard?queue=needs_review" variant="secondary">Return to Review Desk</ActionLink></Alert>
      ) : isPending ? (
        <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading evidence…</span><i /><i /></div>
      ) : isError || !flag ? (
        <Alert title="Could not load this evidence" tone="error" role="alert"><p>{error instanceof ApiError ? error.message : "Try again."}</p><Button variant="secondary" onClick={() => refetch()}>Try again</Button></Alert>
      ) : (
        <>
          <p aria-live="polite" className="signal-live-region">{announcement}</p>
          <ModeDisclosure mode={flag.llm_mode} />
          {flag.first_upload_context && <Alert title="Limited first-upload comparison context" tone="info">This signal was created before this account had a deeper comparison archive. The severity was not changed by that limitation.</Alert>}

          <section className="signal-evidence-hero" aria-labelledby="finding-heading">
            <div className="signal-evidence-hero__meta"><span>{meta?.eyebrow ?? humanize(flag.check_kind)}</span><span>Signal #{flag.id}</span></div>
            <h2 id="finding-heading">Recorded system finding</h2>
            <p className="signal-ai-suggestion"><strong>AI suggestion</strong><span>{humanizedVerdict(flag)}</span></p>
            <p>Repeated AI consistency is intentionally not shown as a percentage. The excerpt and source location below are the evidence to check.</p>
          </section>

          <section className="signal-evidence-section" aria-labelledby="evidence-heading">
            <div className="signal-section-heading"><div><p className="signal-section-kicker">Checkable record</p><h2 id="evidence-heading">Evidence from the manuscript</h2></div></div>
            <blockquote className="signal-evidence-quote"><span>“{systemFindingCopy(flag.evidence_excerpt, flag.ai_verdict_summary, "evidence")}”</span><cite>{flag.page_anchor}</cite></blockquote>
            {flag.ai_reasoning && <div className="signal-reasoning"><h3>Recorded reasoning</h3><p>{systemFindingCopy(flag.ai_reasoning, flag.ai_verdict_summary, "reasoning")}</p></div>}
            <ActionLink
              id={sourceLinkId}
              to={sourcePath}
              variant="brand"
              onClick={() => rememberRouteReturnFocus(`/flags/${flag.id}`, sourcePath.split("?")[0], sourceLinkId)}
            >
              View this location in the manuscript
            </ActionLink>
          </section>

          <div ref={resolutionRef} tabIndex={-1}><ResolutionControl flag={flag} /></div>
          <AnnotationPanel flag={flag} />
        </>
      )}
    </div>
  );
}
