import { reviewDeskDestination } from "../routing/reviewDeskReturn";
import { WorkflowLink as Link } from "../routing/WorkflowLink";
import { ReviewDeskReturnLink } from "../routing/ReviewDeskReturnLink";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import { ApiError } from "../api/client";
import { FindingModeDisclosure, FlagReviewActions } from "./FlagReviewActions";
import { OwnedManuscriptSourcePane } from "../document/ManuscriptSourcePane";
import { useReviewWorkspaceCapacity } from "../document/useReviewWorkspaceCapacity";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
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
import { PValueComparison } from "./PValueComparison";
import { AgreementComparison, type AgreementStatementRole } from "./AgreementComparison";
import { CitationComparison } from "./CitationComparison";
import { CitationHistoryWarning } from "./CitationHistoryWarning";
import { useFlag } from "./useFlag";
import { recoverableReadError } from "../report/reportReadState";

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
  expanded,
  onExpand,
  onSource,
  activeRole,
}: {
  flag: FlagOut;
  sourcePath: string;
  sourceLinkId: string;
  expanded: boolean;
  onExpand: (next: boolean) => void;
  onSource: (anchor?: string | null, role?: AgreementStatementRole) => void;
  activeRole?: AgreementStatementRole;
}) {
  const sourceLocation = useLocation();
  const { showsManuscriptQuote, showsManuscriptEvidence, isResubmission } = evidenceDisplayState(flag);
  const headingId = showsManuscriptEvidence ? "evidence-heading" : "system-record-heading";

  return (
    <section className="signal-evidence-section signal-flag-evidence" aria-labelledby={headingId}>
      <div className="signal-section-heading">
        <div>
          <h2 id={headingId}>{showsManuscriptEvidence ? "Evidence from the manuscript" : "No manuscript excerpt is available"}</h2>
        </div>
        {expanded ? <Button variant="secondary" onClick={() => onExpand(false)}>Return to manuscript</Button>
          : (flag.agreement_pair || flag.passage_pair) && <Button variant="secondary" onClick={() => onExpand(true)}>{flag.agreement_pair ? "Compare statements" : "Compare passages"}</Button>}
      </div>
      {showsManuscriptQuote && flag.check_kind !== "internal_agreement" && (
        <blockquote className="signal-evidence-quote"><span>“{systemFindingCopy(flag.evidence_excerpt, flag.ai_verdict_summary, "evidence")}”</span><cite>{instructorAnchor(flag.page_anchor)}</cite></blockquote>
      )}
      <PValueComparison flag={flag} />
      <AgreementComparison flag={flag} onSource={onSource} activeRole={activeRole} />
      <CitationComparison flag={flag} />
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
      <Button id={sourceLinkId} variant="quiet" onClick={() => onSource()}>Show recorded location</Button>
      <ActionLink id={`flag-review-queue-${flag.id}`} to={sourcePath} variant="quiet" onClick={() => rememberRouteReturnFocus(sourceLocation.pathname + sourceLocation.search, sourcePath, `flag-review-queue-${flag.id}`)}>Open review queue</ActionLink>
    </section>
  );
}

function FindingReasoning({ flag }: { flag: FlagOut }) {
  const { reasoningText } = evidenceDisplayState(flag);
  return <>
    {reasoningText && (!isPValueFinding(flag.ai_verdict_summary) || flag.p_value_comparison) && <div className="signal-reasoning"><h3>{flag.citation_metadata_review_warning ? "Original recorded explanation" : "Recorded reasoning"}</h3><p>{systemFindingCopy(reasoningText, flag.ai_verdict_summary, "reasoning")}</p></div>}
    <div className="signal-reasoning"><h3>What the instructor should verify</h3><p>{findingVerificationPrompt(flag.check_kind, flag.ai_verdict_summary, !!flag.passage_pair, !!flag.citation_metadata_comparison)}</p></div>
  </>;
}

export function FlagDetailPage() {
  const { flagId } = useParams<{ flagId: string }>();
  const routeLocation = useLocation();
  const navigate = useNavigate();
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
  const sourceRef = useRef<HTMLElement>(null);
  const reviewRef = useRef<HTMLDivElement>(null);
  const tabsRef = useRef<HTMLDivElement>(null);
  const sourceFocus = useRef<FocusContinuation | null>(null);
  const [pane, setPane] = useState<"manuscript" | "review">("review");
  const [expandedFor, setExpandedFor] = useState<number | null>(null);
  const expanded = expandedFor === id;
  function setExpanded(next: boolean) { setExpandedFor(next ? id : null); }
  const [sourceCommand, setSourceCommand] = useState<{ flag: number; anchor: string | null | undefined; role?: AgreementStatementRole; attempt: number }>();
  const workspace = useReviewWorkspaceCapacity(active => {
    if (sourceRef.current?.contains(active)) setPane("manuscript");
    else if (reviewRef.current?.contains(active)) setPane("review");
  });
  useLayoutEffect(() => {
    if (workspace.split && tabsRef.current?.contains(document.activeElement)) {
      (pane === "manuscript" ? sourceRef.current : reviewRef.current)?.focus({ preventScroll: true });
    }
  }, [workspace.split, pane]);
  useLayoutEffect(() => {
    if (!sourceFocus.current || expanded || (!workspace.split && pane !== "manuscript")) return;
    sourceFocus.current.focus(sourceRef.current);
    sourceFocus.current.dispose();
    sourceFocus.current = null;
  }, [expanded, workspace.split, pane, sourceCommand]);
  useEffect(() => () => sourceFocus.current?.dispose(), []);
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
  const sourcePath = flag ? reviewDeskDestination(`/report/${flag.check_run_id}/document?view=queue&pane=review`, routeLocation) : "";
  const sourceLinkId = flag ? `flag-source-link-${flag.id}` : "";
  function showSource(anchor?: string | null, role?: AgreementStatementRole) {
    if (!flag) return;
    sourceFocus.current?.dispose();
    sourceFocus.current = claimFocusContinuation();
    setExpanded(false);
    setPane("manuscript");
    setSourceCommand(previous => ({ flag: flag.id, anchor, role, attempt: (previous?.attempt ?? 0) + 1 }));
  }

  return (
    <div className="signal-route signal-page-flow signal-flag-detail">
      <header className="signal-route-header signal-flag-header">
        <div>
          <nav aria-label="Breadcrumb"><ReviewDeskReturnLink>Review Desk</ReviewDeskReturnLink><span aria-hidden="true">/</span>{flag && <><Link to={returnFocus?.returnPath ?? `/report/${flag.check_run_id}`}>{flag.manuscript_group_label}</Link><span aria-hidden="true">/</span></>}<span>Evidence</span></nav>
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
          <section ref={workspace.frame} className="signal-flag-workbench" aria-label="Manuscript and finding review" data-layout={workspace.split ? "split" : "single"} data-reading={expanded ? "full" : undefined}>
            <span ref={workspace.threshold} className="signal-review-workspace-threshold" aria-hidden="true" />
            {(!isCitationMetadataFinding(flag) || flag.llm_mode === "fake") && <FindingModeDisclosure mode={flag.llm_mode} registry={isCitationMetadataFinding(flag)} />}
            <div ref={tabsRef} className="signal-flag-tabs signal-document-tabs" role="tablist" aria-label="Evidence workspace views" hidden={workspace.split || expanded}>
              {(["manuscript", "review"] as const).map(view => <button key={view} id={`flag-${id}-${view}-tab`} type="button" role="tab" aria-selected={pane === view} aria-controls={`flag-${id}-${view}-panel`} tabIndex={pane === view ? 0 : -1} onClick={() => setPane(view)} onKeyDown={event => {
                if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                event.preventDefault();
                const next = event.key === "Home" ? "manuscript" : event.key === "End" ? "review" : view === "manuscript" ? "review" : "manuscript";
                setPane(next);
                document.getElementById(`flag-${id}-${next}-tab`)?.focus();
              }}>{view === "manuscript" ? "Manuscript" : "Review"}</button>)}
            </div>
            <section ref={sourceRef} id={`flag-${id}-manuscript-panel`} tabIndex={-1} role={workspace.split ? "region" : "tabpanel"} aria-label="Source manuscript" className="signal-flag-source" data-active={pane === "manuscript"}>
              <OwnedManuscriptSourcePane key={`${me?.id}:${flag.check_run_id}:${flag.id}`} flag={flag}
                anchor={sourceCommand?.flag === flag.id ? sourceCommand.anchor : undefined} attempt={sourceCommand?.flag === flag.id ? sourceCommand.attempt : undefined}
                selectedStatement={sourceCommand?.flag === flag.id ? sourceCommand.role : undefined}
                visible={!expanded && (workspace.split || pane === "manuscript")}
                onSelectFlag={selected => { if (selected !== flag.id) navigate(reviewDeskDestination(`/flags/${selected}`, routeLocation)); }} />
            </section>
            <div ref={reviewRef} id={`flag-${id}-review-panel`} tabIndex={-1} role={workspace.split ? "region" : "tabpanel"} aria-label="Finding review" className="signal-flag-review" data-active={pane === "review"}>
              <section className="signal-flag-record" aria-label="Recorded finding">
                <p className="signal-flag-record-identity">{leadIdentity ? `${leadContext?.lastLoaded ? "Last loaded: " : ""}${leadIdentity.label}` : `Record #${flag.id}`}</p>
                {optionalComparison && <p>{COMPARISON_LEAD_COPY.intro}</p>}
                <div className="signal-flag-recorded-finding"><h2>{findingMethodLabel(flag)}</h2><p className="signal-flag-verdict">{humanizedVerdict(flag)}</p></div>
              </section>
              <EvidenceSection flag={flag} sourcePath={sourcePath} sourceLinkId={sourceLinkId} expanded={expanded} onExpand={setExpanded} onSource={showSource} activeRole={sourceCommand?.flag === flag.id ? sourceCommand.role : undefined} />
              <section className="signal-flag-summary" aria-label="Reasoning and verification"><FindingReasoning flag={flag} /></section>
              <CitationHistoryWarning flag={flag} />
              {flag.first_upload_context && <Alert title="Limited first-upload comparison context" tone="info">This signal was created while there were few manuscripts to compare against. The severity was not changed by that limitation.</Alert>}
              <div ref={resolutionRef} tabIndex={-1}><FlagReviewActions key={`${flag.check_run_id}:${flag.id}`} flag={flag} /></div>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
