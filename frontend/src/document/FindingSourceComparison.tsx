import { createPortal } from "react-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useLayoutEffect, useRef, useState } from "react";
import type { FlagOut } from "../api/types";
import { useAuthOwner } from "../auth/queryOwner";
import { instructorAnchor } from "../domain/sourceAnchor";
import type { AgreementStatementRole } from "../flags/AgreementComparison";
import { useEvidenceManifest } from "../report/useEvidenceSource";
import { findingSourceReference, useFindingSourcePages, type FindingSourceSide } from "../report/useFindingSourcePages";
import { Button } from "../ui/Button";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { HighlightedText } from "./PassagePairPanel";
import { sharedPassageRanges } from "./sharedPassage";
import { RecordedEvidencePage } from "../report/RecordedEvidencePage";

export function FindingSourceComparison({ flag, current, onSource, pageHost, sourceVisible = false, onReadManuscript }: {
  pageHost?: HTMLElement | null;
  sourceVisible?: boolean;
  onReadManuscript?: () => void;
  flag: FlagOut;
  current: boolean;
  onSource: (anchor: string | null, role: AgreementStatementRole) => void;
}) {
  const client = useQueryClient();
  const owner = useAuthOwner();
  const scope = `${owner.lifetime}:${owner.actorId}:${flag.check_run_id}:${flag.id}`;
  const liveScope = useRef(scope);
  liveScope.current = scope;
  const mounted = useRef(false);
  useLayoutEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const [manualPreview, setPreview] = useState(false);
  const [focusedSource, setFocusedSource] = useState<string | null>(null);
  const preview = Boolean(pageHost) || manualPreview;
  const [reloadState, setReloadState] = useState<{ scope: string; attempt: number; pending: boolean }>();
  const reloading = reloadState?.scope === scope && reloadState.pending;
  const attempt = reloadState?.scope === scope ? reloadState.attempt : 0;
  const agreement = flag.check_kind === "internal_agreement" ? flag.agreement_pair : undefined;
  const reuse = flag.check_kind === "originality_reuse" ? flag.passage_pair : undefined;
  const sides: FindingSourceSide[] = agreement ? [
    { role: "agreement_intent", index: 0, quote: agreement.intent_text, anchor: agreement.intent_anchor },
    { role: "agreement_outcome", index: 1, quote: agreement.outcome_text, anchor: agreement.outcome_anchor },
  ] : reuse ? [{ role: "reuse_own", index: 0, quote: reuse.own_excerpt, anchor: flag.page_anchor }] : [];
  const manifestQuery = useEvidenceManifest(flag.check_run_id, flag.assessment_revision, current && !reloading && sides.length > 0);
  const manifest = current && !reloading && manifestQuery.isSuccess && manifestQuery.fetchStatus === "idle" ? manifestQuery.data : undefined;
  const pages = useFindingSourcePages(flag.check_run_id, flag.id, flag.assessment_revision, manifest, sides, current && !reloading && preview, attempt);
  const shared = reuse ? sharedPassageRanges(reuse.own_excerpt, reuse.matched_excerpt) : undefined;
  const available = preview && current && manifest && pages.isSuccess && pages.fetchStatus === "idle";
  const retired = !current || reloading || !Number.isSafeInteger(flag.assessment_revision) || manifestQuery.isError || pages.isError;
  function reload() {
    if (reloading) return;
    setReloadState({ scope, attempt, pending: true });
    const finish = () => {
      if (mounted.current && owner.isCurrent() && liveScope.current === scope) setReloadState({ scope, attempt: attempt + 1, pending: false });
    };
    void Promise.all([
      client.invalidateQueries({ queryKey: ["flag", flag.id], exact: true }),
      client.invalidateQueries({ queryKey: ["report", flag.check_run_id] }),
    ]).then(finish, finish);
  }
  if (!agreement && !reuse) return null;
  return <section className="signal-finding-source-comparison" aria-label={agreement ? "Statements selected for comparison" : "Stored passages for comparison"}>
    <h3>{agreement ? "Statements selected for comparison" : "Stored passages for comparison"}</h3>
    {agreement ? <p>These statements were selected for comparison. Their roles as this study's objective and result have not been verified.</p>
      : <><p>Similar wording does not establish copying, authorship or which document came first. The comparison excerpt is not a complete source document.</p>
        <p>{shared?.left.length ? "Text shading identifies shared wording between the stored excerpts. Page boxes locate the saved quotation; they do not establish copying." : "No stable exact phrase was found to shade. Compare both passages manually. Page boxes locate the saved quotation."}</p></>}
    {agreement && flag.affects_readiness === false && <p>This review lead does not change readiness.</p>}
    {retired && <Alert title="Recorded source references unavailable" tone="warning" role="status"><p>{reloading ? "Reloading the finding and its recorded source references..." : "This comparison's current source references are unavailable. Reload the finding before comparing source pages. The saved text below is retained for manual review."}</p><Button variant="secondary" busy={reloading} onClick={reload}>Reload recorded evidence</Button></Alert>}
    {!pageHost && <Button variant="secondary" aria-expanded={preview} onClick={() => setPreview(value => !value)}>{preview ? "Hide" : "Show"} original-page previews</Button>}
    {pageHost && createPortal(<section className="signal-source-page-comparison" aria-label="Original manuscript pages">
      <header className="signal-source-page-comparison__toolbar"><strong>Original manuscript pages</strong>{onReadManuscript && <Button variant="secondary" onClick={onReadManuscript}>Read full manuscript</Button>}</header>
      <div className="signal-source-page-comparison__pages">{sides.map((side, index) => {
        const result = available ? pages.data.find(item => item.role === side.role) : undefined;
        const label = agreement ? `Statement ${index === 0 ? "A" : "B"}` : "Your manuscript";
        return <figure key={`${scope}:${side.role}`}><figcaption><strong>{label}</strong><span>{side.anchor?.trim() ? instructorAnchor(side.anchor) : "No location recorded"}</span></figcaption>
          {result?.page?.image_data_url ? <><img src={result.page.image_data_url} alt={`Original manuscript page for ${label}. Placement qualification follows.`} /><p className="signal-field-hint">{result.page.caption}</p></>
            : <p role="status">{retired ? "Source images are cleared until the current finding is reloaded." : manifestQuery.isPending || manifestQuery.isFetching ? "Checking recorded source references." : pages.isFetching ? "Loading the recorded source page." : result?.page?.caption || "The recorded source page is unavailable. Read the saved passage beside this pane."}</p>}
        </figure>;
      })}{reuse && <figure><figcaption><strong>Stored comparison excerpt</strong><span>Library record #{reuse.matched_ref}</span></figcaption><p>Its full source page is unavailable in this report. Read the complete stored excerpt beside this pane or open the permitted Library record.</p></figure>}</div>
    </section>, pageHost)}
    <div className="signal-finding-source-comparison__cards">{sides.map((side, index) => {
      const result = available ? pages.data.find(item => item.role === side.role) : undefined;
      const role: AgreementStatementRole = agreement ? index === 0 ? "objective" : "result" : "statement";
      const label = agreement ? index === 0 ? "Statement A. Selected as an objective" : "Statement B. Selected as a result" : "Your manuscript";
      const ref = findingSourceReference(manifest, flag.id, side);
      const prefix = flag.check_kind === "internal_agreement" ? "agreement" : "reuse";
      return <article key={`${flag.check_run_id}:${flag.id}:${side.role}`} className="signal-finding-source-comparison__card">
        <h4>{label}</h4>
        <p>Recorded location: {side.anchor?.trim() ? instructorAnchor(side.anchor) : "No location recorded"}</p>
        <blockquote className="signal-evidence-quote">{reuse?.own_context_before && <span>{reuse.own_context_before} </span>}<HighlightedText text={side.quote} ranges={shared?.left ?? []} />{reuse?.own_context_after && <span> {reuse.own_context_after}</span>}</blockquote>
        {!pageHost && (result?.page?.image_data_url ? (
          <figure className="signal-criterion-packet__preview">
            <img src={result.page.image_data_url} alt={`Recorded source page for ${agreement ? index === 0 ? "statement A" : "statement B" : "your manuscript excerpt"}. Placement qualification follows.`} />
            <figcaption>{result.page.caption}</figcaption>
          </figure>
        ) : result?.page ? (
          <p className="signal-field-hint">{result.page.caption || "Source page preview is available for PDF manuscripts. Check the text excerpt above."}</p>
        ) : (
          <figure>
            <figcaption>{retired ? "Source images are cleared until the current finding is reloaded."
              : manifestQuery.isPending || manifestQuery.isFetching ? "Checking recorded source references."
                : !ref ? "A current source reference is not available for this saved statement."
                  : pages.isFetching ? "Loading the recorded source page." : "The recorded source page could not be loaded."}</figcaption>
          </figure>
        ))}
        {!retired && ref && manifest && <RecordedEvidencePage checkRunId={flag.check_run_id} snapshot={manifest.snapshot_id} reference={ref} cachedPage={result?.page} />}
        {(result?.failed || pages.isError) && <Button variant="quiet" busy={pages.isFetching} onClick={() => { void pages.refetch(); }}>Retry source page</Button>}
        {side.anchor?.trim() && !(pageHost && sourceVisible && available && result?.page?.image_data_url && focusedSource !== side.role) && <Button id={`${prefix}-${flag.id}-${role}-source`} variant="secondary" onFocus={() => setFocusedSource(side.role)} onBlur={() => setFocusedSource(null)} onClick={() => onSource(side.anchor, role)}>{agreement ? `Show statement ${index === 0 ? "A" : "B"} in manuscript` : "Show excerpt in manuscript"}</Button>}
      </article>;
    })}
      {reuse && <article className="signal-finding-source-comparison__card"><h4>Stored comparison excerpt. Library record #{reuse.matched_ref}</h4>
        <p>Bounded excerpt, not a complete source document. Its original-page image is unavailable in this report.</p>
        <blockquote className="signal-evidence-quote">{reuse.matched_context_before && <span>{reuse.matched_context_before} </span>}<HighlightedText text={reuse.matched_excerpt} ranges={shared?.right ?? []} />{reuse.matched_context_after && <span> {reuse.matched_context_after}</span>}</blockquote>
        <ActionLink to={`/library/${reuse.matched_ref}`} variant="secondary">Open in Library</ActionLink>
      </article>}
    </div>
  </section>;
}
