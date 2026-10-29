// F8/4h/4w: explicit original-page reading through the current bound renderer.
import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useLocation } from "react-router";
import { ApiError, api } from "../api/client";
import { useAuthOwner } from "../auth/queryOwner";
import { instructorAnchor } from "../domain/sourceAnchor";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import type { EvidencePage, EvidenceReference } from "./evidenceTypes";
import { useEvidenceManifest } from "./useEvidenceSource";
import type { FlagOut } from "../api/types";
import { CriterionEvidenceDialog, type CriterionEvidenceContext } from "./CriterionEvidenceDialog";

export async function verifyEvidencePage(page: EvidencePage, snapshot: string, reference: string) {
  if (page.snapshot_id !== snapshot || page.placement.reference_id !== reference) {
    throw new ApiError(409, "conflict", "This evidence changed. Reload its recorded source links.");
  }
  if (page.image_data_url !== null) {
    const match = /^data:image\/png;base64,([A-Za-z0-9+/]+={0,2})$/.exec(page.image_data_url);
    if (!match || !/^[a-f0-9]{64}$/.test(page.image_sha256 ?? "")) {
      throw new ApiError(409, "conflict", "The original-page image could not be verified.");
    }
    const bytes = Uint8Array.from(atob(match[1]), character => character.charCodeAt(0));
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)))
      .map(value => value.toString(16).padStart(2, "0")).join("");
    if (digest !== page.image_sha256) throw new ApiError(409, "conflict", "The original-page image could not be verified.");
  }
  return page;
}

export function RecordedFindingSources({ flag, current }: { flag: FlagOut; current: boolean }) {
  const query = useEvidenceManifest(flag.check_run_id, flag.assessment_revision, current);
  const manifest = current && query.isSuccess && query.fetchStatus === "idle" && !query.isError
    ? query.data : undefined;
  const references = manifest?.references.filter(reference => reference.flag_id === flag.id) ?? [];
  return <section className="signal-criterion-packet" aria-label="Original finding sources">
    <h4 className="signal-section-kicker">Original pages</h4>
    {!manifest && <p>Current original-page links are unavailable. Saved evidence remains readable above.</p>}
    {references.map(reference => <div key={reference.reference_id} className="signal-source-row">
      <p className="signal-source-row__label"><strong>{reference.role === "agreement_intent" ? "Statement A" : reference.role === "agreement_outcome" ? "Statement B" : reference.role === "reuse_own" ? "Checked manuscript" : reference.role === "reuse_match" ? "Saved comparison excerpt" : "Recorded manuscript evidence"}</strong>
        {reference.anchor?.trim() && <span>{instructorAnchor(reference.anchor)}</span>}</p>
      {reference.source_scope === "own_manuscript" && manifest ? <RecordedEvidencePage checkRunId={flag.check_run_id} snapshot={manifest.snapshot_id} reference={reference} />
        : <p className="signal-field-hint">No original-page image is stored for this comparison excerpt.</p>}
    </div>)}
  </section>;
}

export function RecordedEvidencePage({ checkRunId, snapshot, reference, cachedPage, context }: {
  checkRunId: number; snapshot: string; reference: EvidenceReference;
  cachedPage?: EvidencePage;
  context?: CriterionEvidenceContext;
}) {
  const location = useLocation();
  const owner = useAuthOwner();
  if (!reference.anchor?.trim() && !reference.quote.trim()) return <p>No source location was saved for this item.</p>;
  return <RecordedPageSession key={`${location.key}:${owner.lifetime}:${snapshot}:${reference.reference_id}`} checkRunId={checkRunId} snapshot={snapshot} reference={reference} cachedPage={cachedPage} context={context} />;
}

function RecordedPageSession({ checkRunId, snapshot, reference, cachedPage, context }: {
  checkRunId: number; snapshot: string; reference: EvidenceReference;
  cachedPage?: EvidencePage;
  context?: CriterionEvidenceContext;
}) {
  const owner = useAuthOwner();
  const actionId = useId();
  const [preview, setPreview] = useState(false);
  const [open, setOpen] = useState(false);
  const query = useQuery({
    queryKey: ["report", checkRunId, "recorded-page", owner.actorId, owner.lifetime, snapshot, reference.reference_id],
    enabled: owner.isCurrent() && (preview || (open && !context)),
    gcTime: 0, retry: false,
    queryFn: async ({ signal }) => {
      await Promise.resolve();
      signal.throwIfAborted();
      if (!owner.isCurrent()) throw new ApiError(409, "conflict", "This review is no longer active.");
      const page = cachedPage ?? await api.get<EvidencePage>(`/check-runs/${checkRunId}/evidence/${reference.reference_id}/page?snapshot_id=${snapshot}`, { signal });
      const verified = await verifyEvidencePage(page, snapshot, reference.reference_id);
      if (!owner.isCurrent()) throw new ApiError(409, "conflict", "This review is no longer active.");
      return verified;
    },
  });
  const current = owner.isCurrent() && !query.isError && query.fetchStatus === "idle" ? query.data : undefined;
  // A whole-document comparison has no single page; showing page 1 would
  // suggest the evidence is there.
  const wholeDocument = /^whole (?:document|manuscript)$/i.test(instructorAnchor(reference.anchor ?? "").trim());
  const content = (enlarged: boolean) => <>
    {query.isFetching && <p role="status">Loading the recorded original page.</p>}
    {query.isError && <div role="alert"><p>{query.error instanceof ApiError ? query.error.message : "The original page could not be loaded."}</p>
      <Button variant="secondary" disabled={query.isFetching} onClick={() => { void query.refetch(); }}>Retry original page</Button></div>}
    {current && wholeDocument && <p className="signal-field-hint">This comparison covers the whole document, so no single page image applies. Read the passage in the manuscript beside this review.</p>}
    {current && !wholeDocument && <figure className={enlarged ? "signal-evidence-dialog__page" : "signal-criterion-packet__preview"}>
      {current.image_data_url ? <img src={current.image_data_url} alt={`Original source, ${instructorAnchor(reference.anchor ?? "")}`} /> : <p>No original-page image is available for this saved evidence.</p>}
      <figcaption>{current.caption}</figcaption>
    </figure>}
  </>;
  return <div className="signal-recorded-page-actions">
    <Button id={actionId} variant="secondary" aria-label={reference.anchor?.trim() ? `Open original page: ${instructorAnchor(reference.anchor)}` : undefined} onClick={() => setOpen(true)}>{reference.anchor?.trim() ? "Open original page" : "Open saved source"}</Button>
    <Button variant="secondary" aria-expanded={preview} onClick={() => setPreview(value => !value)}>{preview ? "Hide" : "Show"} original-page preview</Button>
    {preview && content(false)}
    {open && owner.isCurrent() && context && <CriterionEvidenceDialog run={checkRunId} request={{ snapshotId: snapshot, reference, actionId }} context={context} onClose={() => setOpen(false)} />}
    {open && owner.isCurrent() && !context && <Dialog title={`Original source: ${instructorAnchor(reference.anchor ?? "") || "Saved source"}`} wide onClose={() => setOpen(false)}>
      <blockquote>{reference.quote}</blockquote><p>{instructorAnchor(reference.anchor ?? "")}</p>{content(true)}
    </Dialog>}
  </div>;
}
