import { reviewDeskDestination } from "../routing/reviewDeskReturn";
import { WorkflowLink as Link } from "../routing/WorkflowLink";
import { ReviewDeskReturnLink } from "../routing/ReviewDeskReturnLink";
import { useEffect, useId, useRef, useState } from "react";
import { useLocation, useParams } from "react-router";
import { ApiError } from "../api/client";
import { TEST_AI_DISCLOSURE } from "../domain/aiModeDisclosure";
import type { FlagOut } from "../api/types";
import { useMe } from "../auth/useAuth";
import { PassagePairPanel } from "../document/PassagePairPanel";
import { checkKindMeta, findingVerificationPrompt, humanize, isPValueFinding } from "../domain/checkKind";
import { evidenceDisplayState } from "../domain/evidenceDisplay";
import { findingMethodLabel, isCitationMetadataFinding } from "../domain/findingMethod";
import { markFlagViewed } from "../domain/flagViewed";
import { problemLabel } from "../domain/problemLabel";
import { instructorAnchor } from "../domain/sourceAnchor";
import { systemFindingCopy } from "../domain/systemFindingCopy";
import { COMPARISON_LEAD_COPY, comparisonLeadIdentity, isOptionalComparisonLead } from "../domain/comparisonLeadIdentity";
import { comparisonLeadCollection } from "../report/comparisonLeadCollection";
import { useCachedReportFlags } from "../report/useReport";
import { rememberRouteReturnFocus, useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { ConfirmCitationSourceModal } from "./ConfirmCitationSourceModal";
import { PValueComparison } from "./PValueComparison";
import { AgreementComparison } from "./AgreementComparison";
import { CitationComparison } from "./CitationComparison";
import { CitationHistoryWarning } from "./CitationHistoryWarning";
import { useAnnotateFlag, useFlag, useOverrideFlag } from "./useFlag";
import { useFlagActionReview } from "./useFlagActionReview";
import { FlagActionCurrentReview } from "./FlagActionCurrentReview";
import { recoverableReadError } from "../report/reportReadState";
import { useExplicitActionFocus } from "../routing/useExplicitActionFocus";
import { isAssessmentRevision } from "../report/assessmentRevision";
import { keepNewerAssessment } from "../report/assessmentRevision";
import { useQueryClient } from "@tanstack/react-query";
import { useFindingCompletion, useFindingCompletionOwner } from "./useFindingCompletion";

const SEVERITY_LABEL: Record<FlagOut["severity"], string> = {
  high: "High severity",
  med: "Medium severity",
  low: "Low severity",
};

function humanizedVerdict(flag: FlagOut): string {
  if (!flag.ai_verdict_summary) return "No determination recorded";
  return problemLabel(flag.ai_verdict_summary) ?? humanize(flag.ai_verdict_summary);
}

// BUG-170: a whole-document/chapter/resubmission originality-reuse flag's
// `evidence_excerpt` is always a system-authored template sentence, never
// a quote from the manuscript (`systemFindingCopy.ts`'s own docstring
// already draws this line) -- rendering it inside a <blockquote>/<cite>
// pair styled identically to a real excerpt claimed it was checkable
// manuscript text when it wasn't, directly under a heading promising
// "Evidence from the manuscript". Only a passage-level reuse flag (or any
// non-reuse check kind, which never has this system-authored-summary
// shape at all) gets the quote treatment; every other reuse verdict gets
// the same sentence rendered as reasoning instead, honestly labeled, and
// the section's own heading changes when nothing manuscript-sourced is
// left to show at all (design review spec). The condition logic itself
// lives in `domain/evidenceDisplay.ts`, shared with
// `SignalDocumentViewer.tsx`'s own evidence panel -- UX review found
// that screen had drifted (still rendering the fake quote unconditionally)
// specifically because this logic was first written inline, here only.
function EvidenceSection({
  flag,
  sourcePath,
  sourceLinkId,
}: {
  flag: FlagOut;
  sourcePath: string;
  sourceLinkId: string;
}) {
  const sourceLocation = useLocation();
  const { showsManuscriptQuote, reasoningText, showsManuscriptEvidence, isResubmission } = evidenceDisplayState(flag);
  const headingId = showsManuscriptEvidence ? "evidence-heading" : "system-record-heading";

  return (
    <section className="signal-evidence-section" aria-labelledby={headingId}>
      <div className="signal-section-heading">
        <div>
          <p className="signal-section-kicker">{showsManuscriptEvidence ? "Checkable record" : "System record"}</p>
          <h2 id={headingId}>{showsManuscriptEvidence ? "Evidence from the manuscript" : "No manuscript excerpt is available"}</h2>
        </div>
      </div>
      {showsManuscriptQuote && flag.check_kind !== "internal_agreement" && (
        <blockquote className="signal-evidence-quote"><span>“{systemFindingCopy(flag.evidence_excerpt, flag.ai_verdict_summary, "evidence")}”</span><cite>{instructorAnchor(flag.page_anchor)}</cite></blockquote>
      )}
      <PValueComparison flag={flag} />
      <AgreementComparison flag={flag} />
      <CitationComparison flag={flag} />
      {reasoningText && (!isPValueFinding(flag.ai_verdict_summary) || flag.p_value_comparison) && <div className="signal-reasoning"><h3>{flag.citation_metadata_review_warning ? "Original recorded explanation" : "Recorded reasoning"}</h3><p>{systemFindingCopy(reasoningText, flag.ai_verdict_summary, "reasoning")}</p></div>}
      <div className="signal-reasoning"><h3>What the instructor should verify</h3><p>{findingVerificationPrompt(flag.check_kind, flag.ai_verdict_summary, !!flag.passage_pair, !!flag.citation_metadata_comparison)}</p></div>
      {/* BUG-154: the API has always returned a complete
          passage_pair for a matched flag (own/matched excerpt +
          context both sides, similarity, level) -- this page fetched
          it and rendered none of it, so an instructor asking "where
          did this come from" had no answer except a link into the
          document viewer, which was itself broken until BUG-138.
          Same component, same props, same "flag" variant already
          shipped on SignalDocumentViewer.tsx -- no new design here. */}
      {flag.passage_pair && (
        <PassagePairPanel pair={flag.passage_pair} ownAnchor={instructorAnchor(flag.page_anchor)} wholeManuscript={!flag.is_passage_level} variant="flag" />
      )}
      {/* BUG-170: a reuse flag with no manuscript-sourced content at
          all (no quote, no passage panel) still needs an honest reason
          why -- "resubmission" never had a passage to compare (nothing
          was searched for); anything else genuinely searched and found
          nothing close enough, and only THAT case's own severity was
          ever lowered because of it (BUG-153's own downgrade). */}
      {!showsManuscriptEvidence && (
        <Alert
          title={isResubmission ? "No passage-level comparison applies" : "No supporting passage could be found"}
          tone={isResubmission ? "info" : "warning"}
        >
          {isResubmission
            ? "This finding compares the manuscript as a whole against your own Library record named above, so there is no specific passage to compare side by side. Open that record and the manuscript location below to check it yourself."
            : flag.evidence_unavailable
              ? "VERIDICAL looked for a specific passage that matches the Library record named above and did not find one close enough to show side by side. This finding's severity was lowered because no matching passage could be shown. Open the manuscript location below to check it yourself."
              : "VERIDICAL looked for a specific passage that matches the Library record named above and did not find one close enough to show side by side. Open the manuscript location below to check it yourself."}
        </Alert>
      )}
      {flag.matched_ref != null && (
        <ActionLink to={`/library/${flag.matched_ref}`} variant="secondary">
          Open Library record #{flag.matched_ref}
        </ActionLink>
      )}
      <ActionLink
        id={sourceLinkId}
        to={sourcePath}
        variant="brand"
        onClick={() => rememberRouteReturnFocus(sourceLocation.pathname + sourceLocation.search, sourcePath, sourceLinkId)}
      >
        View this location in the manuscript
      </ActionLink>
    </section>
  );
}

function ModeDisclosure({ mode, registry = false }: { mode: FlagOut["llm_mode"]; registry?: boolean }) {
  if (mode === "real") return null;
  if (registry && mode === "fake") return <Alert title="Check recorded in a test-mode run" tone="warning">This record belongs to a test-mode run. Do not treat its saved provider values as evidence of a fresh external lookup.</Alert>;
  return mode === "fake" ? (
    <Alert title={TEST_AI_DISCLOSURE.title} tone="warning">
      {TEST_AI_DISCLOSURE.body}
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

function OverrideDialog({ flag, onClose, onAccepted, continuationIsCurrent, fallbackFocusRef }: { flag: FlagOut; onClose: () => void; onAccepted: (flag: FlagOut) => boolean; continuationIsCurrent: () => boolean; fallbackFocusRef: React.RefObject<HTMLHeadingElement | null> }) {
  const [reason, setReason] = useState("");
  const [attempted, setAttempted] = useState(false);
  const override = useOverrideFlag(flag.id);
  const review = useFlagActionReview(flag, "override");
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const followAction = useExplicitActionFocus(() => reasonRef.current);
  const errorId = useId();
  const invalid = attempted && reason.trim().length === 0;
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
      onError: review.freeze,
    });
  }

  return (
    <Dialog
      title="Override this finding?"
      fallbackFocusRef={fallbackFocusRef}
      returnFocusTarget={() => completion.returnTarget(fallbackFocusRef.current)}
      shouldRestoreFocus={() => completion.shouldRestore(continuationIsCurrent)}
      initialFocusTitle
      onClose={override.isPending || accepted ? undefined : onClose}
      actions={<><Button variant="secondary" disabled={override.isPending || accepted} onClick={onClose}>Cancel</Button><Button variant="brand" disabled={!review.canSubmit || accepted} busy={override.isPending} onClick={confirm}>Confirm override</Button></>}
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
            aria-invalid={invalid || undefined}
            aria-describedby={invalid ? errorId : undefined}
            onChange={(event) => {
              setReason(event.target.value);
              review.reasonChanged();
              if (attempted) setAttempted(false);
            }}
          />
        </label>
        {invalid && <p id={errorId} role="alert" className="signal-field-error">Enter a reason before confirming.</p>}
        {override.isError && <Alert title="Saving could not be confirmed" tone="error" role="alert">Your entered reason is kept. Review the current assessment before trying again.</Alert>}
      </div>
    </Dialog>
  );
}

function ResolutionControl({ flag: observedFlag }: { flag: FlagOut }) {
  const client = useQueryClient();
  const location = useLocation();
  const isCurrent = useFindingCompletionOwner(`${location.key}:${observedFlag.check_run_id}:${observedFlag.id}:resolution`);
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
      <h2 ref={headingRef} tabIndex={-1} id={headingId}>{terminal ? flag.confirmed_citation_source ? "Source confirmed" : "Finding overridden" : optional ? "Review lead: not scored" : "Resolve only after checking the evidence"}</h2>
    </div></div>
    {terminal ? <>
        <p>{flag.confirmed_citation_source ? `You confirmed the source after checking it: ${flag.override_reason}.` : `You overrode this possible inconsistency: ${flag.override_reason}.`}</p>
        {flag.confirmed_citation_source && <p>Your confirmation applies only to this exact reference in your account, not to another reference or instructor. It does not establish support for a manuscript claim.</p>}
        <p>{isOptionalComparisonLead(flag) ? "The original comparison record remains above. This lead does not affect readiness." : "The original finding remains above, and the readiness band has been recalculated."}</p>
        <ActionLink to={`/report/${flag.check_run_id}`} variant="secondary">View updated report</ActionLink>
      </>
    : optional ? <>
        <p>This comparison does not change readiness. You can inspect the evidence without writing a comment or recording an override.</p>
      </>
    : <>
      <p>{flag.ai_verdict_summary ? "This finding remains open unless you record a reasoned override." : "VERIDICAL did not reach a determination, so this finding does not affect readiness unless you affirm it."}</p>
      {confirmSourceEligible && (flag.citation_source_key ? (
        <div className="signal-confirm-source-option">
          <h3>Verified the source yourself?</h3>
          <p>This resolves this finding. Later checks may reuse your confirmation only for this exact reference and source key in your account.</p>
          {flag.citation_source_key.kind === "title" && <Alert title="Title matches are less precise" tone="warning">Double-check that this is the source cited in the manuscript before confirming it.</Alert>}
          <Button variant="secondary" onClick={() => setConfirmOpen(true)}>Confirm this source</Button>
        </div>
      ) : (
        <Alert title="No source record can be confirmed" tone="info">VERIDICAL could not identify a DOI, ISBN, or title. If you verified the citation independently, override only this finding and explain why.</Alert>
      ))}
      <div className="signal-evidence-actions"><Button ref={overrideTriggerRef} variant="secondary" onClick={() => setOverrideOpen(true)}>Override</Button></div>
    </>}
  </section>;
  return <>{content}
    {overrideOpen && <OverrideDialog key={`${flag.check_run_id}:${flag.id}:override`} flag={flag} fallbackFocusRef={headingRef} continuationIsCurrent={isCurrent} onAccepted={accept} onClose={closeOverride} />}
    {confirmOpen && <ConfirmCitationSourceModal key={`${flag.check_run_id}:${flag.id}:confirm-source`} flag={flag} fallbackFocusRef={headingRef} continuationIsCurrent={isCurrent} onAccepted={accept} onClose={() => setConfirmOpen(false)} />}
  </>;
}

export function FlagDetailPage() {
  const { flagId } = useParams<{ flagId: string }>();
  const routeLocation = useLocation();
  const id = Number(flagId);
  const validId = Number.isInteger(id) && id > 0;
  const { data, isPending, isError, error, refetch } = useFlag(id);
  const flag = data?.id === id && recoverableReadError(error) ? data : undefined;
  const cachedFlags = useCachedReportFlags(flag?.check_run_id);
  const leadContext = flag ? comparisonLeadCollection(flag.check_run_id, cachedFlags) : undefined;
  const optionalComparison = Boolean(flag && isOptionalComparisonLead(flag));
  const leadIdentity = flag && optionalComparison ? comparisonLeadIdentity(flag, leadContext?.snapshot) : undefined;
  const headingRef = useRef<HTMLHeadingElement>(null);
  const resolutionRef = useRef<HTMLDivElement>(null);
  const previousResolved = useRef<boolean | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const returnFocus = (routeLocation.state as { routeReturnFocus?: { returnPath: string; elementId: string } } | null)?.routeReturnFocus;
  useRouteFocus("Evidence detail - VERIDICAL", headingRef, returnFocus);
  // BUG-167: also drives the breadcrumb's own destination below --
  // `returnPath` is the report's exact prior URL (filter/pagination
  // included, not just its bare path), so a breadcrumb click restores the
  // same view an instructor left, the same round trip `useRouteFocus`
  // itself now also handles for a PUSH-style return. Falls back to the
  // bare report path when this page was reached directly (no recorded
  // origin to return to).
  const { data: me } = useMe();

  // BUG-167: "opened" means the evidence actually rendered, not merely
  // that a link was clicked -- gated on `flag` being loaded, same
  // condition that already excludes the isPending/isError branches below.
  // `markFlagViewed` is idempotent (a no-op once already recorded), so
  // depending on the whole `flag` object -- rather than just `flag.id` --
  // costs nothing and matches this file's own sibling effect below.
  useEffect(() => {
    if (!flag) return;
    markFlagViewed(me?.id, flag.id);
  }, [flag, me?.id]);

  useEffect(() => {
    if (!flag) return;
    if (previousResolved.current === null) {
      previousResolved.current = flag.overridden;
      return;
    }
    if (flag.overridden && previousResolved.current === false) {
      setAnnouncement(isOptionalComparisonLead(flag) ? "Comparison lead resolved. This does not affect readiness." : flag.confirmed_citation_source ? "Source confirmed. The readiness band was recalculated." : "Finding overridden. The readiness band was recalculated.");
    }
    previousResolved.current = flag.overridden;
  }, [flag]);

  const meta = flag ? checkKindMeta(flag.check_kind) : null;
  const sourcePath = flag ? reviewDeskDestination(`/report/${flag.check_run_id}/document?flag=${flag.id}`, routeLocation) : "";
  const sourceLinkId = flag ? `flag-source-link-${flag.id}` : "";

  return (
    <div className="signal-route signal-page-flow signal-flag-detail">
      <header className="signal-route-header signal-flag-header">
        <div>
          <nav aria-label="Breadcrumb"><ReviewDeskReturnLink>Review Desk</ReviewDeskReturnLink><span aria-hidden="true">/</span>{flag && <><Link to={returnFocus?.returnPath ?? `/report/${flag.check_run_id}`}>{flag.manuscript_group_label}</Link><span aria-hidden="true">/</span></>}<span>Evidence</span></nav>
          <p className="signal-eyebrow">{optionalComparison ? "Review · Optional comparison lead" : "Review · Possible inconsistency"}</p>
          <h1 ref={headingRef} tabIndex={-1}>{meta?.title ?? "Evidence detail"}</h1>
          {flag?.criterion_text && <p className="signal-route-header__intro">Related criterion: {flag.criterion_text}</p>}
        </div>
        {flag && <div className="signal-flag-status"><span className={`signal-severity signal-severity--${optionalComparison ? "low" : flag.severity}`}>{optionalComparison ? COMPARISON_LEAD_COPY.badge : SEVERITY_LABEL[flag.severity]}</span>{flag.overridden && <span className="signal-resolution-state">{flag.confirmed_citation_source ? "Source confirmed" : "Overridden"}</span>}</div>}
      </header>

      {!validId ? (
        <Alert title="This evidence address is invalid" tone="error" role="alert"><ReviewDeskReturnLink variant="secondary">Return to Review Desk</ReviewDeskReturnLink></Alert>
      ) : isPending ? (
        <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading evidence…</span><i /><i /></div>
      ) : !flag ? (
        <Alert title="Could not load this evidence" tone="error" role="alert"><p>{error instanceof ApiError ? error.message : "Try again."}</p><Button variant="secondary" onClick={() => refetch()}>Try again</Button></Alert>
      ) : (
        <>
          {isError && <Alert title="Could not update this evidence" tone="warning"><p>Showing the last loaded finding. Your entered reason is kept. Review the current assessment before confirming an action.</p><Button variant="secondary" onClick={() => { void refetch(); }}>Retry evidence</Button></Alert>}
          <p aria-live="polite" className="signal-live-region">{announcement}</p>
          {(!isCitationMetadataFinding(flag) || flag.llm_mode === "fake") && <ModeDisclosure mode={flag.llm_mode} registry={isCitationMetadataFinding(flag)} />}
          {flag.first_upload_context && <Alert title="Limited first-upload comparison context" tone="info">This signal was created while there were few manuscripts to compare against. The severity was not changed by that limitation.</Alert>}

          <section className="signal-evidence-hero" aria-labelledby="finding-heading">
            <div className="signal-evidence-hero__meta"><span>{meta?.eyebrow ?? humanize(flag.check_kind)}</span><span>{leadIdentity ? `${leadContext?.lastLoaded ? "Last loaded: " : ""}${leadIdentity.label}` : `Signal #${flag.id}`}</span></div>
            <h2 id="finding-heading">{optionalComparison ? "Recorded comparison lead" : "Recorded system finding"}</h2>
            {optionalComparison && <p>{COMPARISON_LEAD_COPY.intro}</p>}
            <p className="signal-ai-suggestion"><strong>{findingMethodLabel(flag)}</strong><span>{humanizedVerdict(flag)}</span></p>
            <p>{findingMethodLabel(flag) === "AI suggestion"
              ? "Repeated AI consistency is intentionally not shown as a percentage. The excerpt and source location below are the evidence to check."
              : isCitationMetadataFinding(flag) && !flag.citation_metadata_comparison
                ? "Review the original reference and recorded explanation. Verify the source independently or run a new check before recording an instructor action."
                : "Review the recorded evidence and source before recording an instructor action."}</p>
          </section>

          <CitationHistoryWarning flag={flag} />
          <EvidenceSection flag={flag} sourcePath={sourcePath} sourceLinkId={sourceLinkId} />

          <div ref={resolutionRef} tabIndex={-1}><ResolutionControl key={`${routeLocation.key}:${flag.check_run_id}:${flag.id}`} flag={flag} /></div>
          <AnnotationPanel flag={flag} />
        </>
      )}
    </div>
  );
}
