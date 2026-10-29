// F8/4l: token-authorized public source associations, never private source URLs.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useLocation } from "react-router";
import { ApiError, api } from "../api/client";
import { instructorAnchor } from "../domain/sourceAnchor";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import type { EvidenceManifest, EvidencePage, EvidenceReference } from "./evidenceTypes";
import { verifyEvidencePage } from "./RecordedEvidencePage";
import { EvidenceSourceLayout } from "./EvidenceSourceLayout";

const ROLE_LABEL: Record<EvidenceReference["role"], string> = {
  criterion_evidence: "Recorded criterion passage", criterion_scope: "Recorded assessment scope",
  finding_excerpt: "Recorded finding passage", finding_scope: "Recorded finding location",
  agreement_intent: "Statement A, selected as an objective", agreement_outcome: "Statement B, selected as a result",
  reuse_own: "Passage in the checked manuscript", reuse_match: "Saved comparison excerpt",
};
interface SharedEvidenceContext { title: string; explanation: string | null; recordedNote: string | null }

export function useSharedEvidenceManifest(token: string, enabled: boolean) {
  return useQuery({ queryKey: ["shared-evidence", token], enabled: Boolean(token) && enabled,
    retry: false, gcTime: 0,
    queryFn: async ({ signal }) => {
      await Promise.resolve(); signal.throwIfAborted();
      const out = await api.getPublic<EvidenceManifest>(`/shared/${encodeURIComponent(token)}/evidence`, { signal });
      if (!/^[a-f0-9]{64}$/.test(out.snapshot_id) || !Array.isArray(out.references)
        || out.references.some(ref => !/^[a-f0-9]{64}$/.test(ref.reference_id))) {
        throw new ApiError(409, "conflict", "This shared evidence could not be verified.");
      }
      return out;
    },
  });
}

function SharedSourcePage({ token, snapshot, reference, context, references }: {
  token: string; snapshot: string; reference: EvidenceReference;
  context?: SharedEvidenceContext; references: readonly EvidenceReference[];
}) {
  const location = useLocation();
  return <SharedPageSession key={`${location.key}:${token}:${snapshot}:${reference.reference_id}`} token={token} snapshot={snapshot} reference={reference} context={context} references={references} />;
}

function SharedPageSession({ token, snapshot, reference, context, references }: {
  token: string; snapshot: string; reference: EvidenceReference;
  context?: SharedEvidenceContext; references: readonly EvidenceReference[];
}) {
  const [preview, setPreview] = useState(true);
  const [dialog, setDialog] = useState(false);
  const query = useQuery({ queryKey: ["shared-source-page", token, snapshot, reference.reference_id],
    enabled: preview || dialog, gcTime: 0, retry: false,
    queryFn: async ({ signal }) => {
      await Promise.resolve(); signal.throwIfAborted();
      const page = await api.getPublic<EvidencePage>(`/shared/${encodeURIComponent(token)}/evidence/${reference.reference_id}/page?snapshot_id=${snapshot}`, { signal });
      return verifyEvidencePage(page, snapshot, reference.reference_id);
    },
  });
  const page = !query.isError && query.fetchStatus === "idle" ? query.data : undefined;
  const image = (large: boolean) => <>
    {query.isFetching && <p role="status">Loading original page.</p>}
    {query.isError && <div role="alert"><p>{query.error instanceof ApiError ? query.error.message : "The original page could not be loaded."}</p><Button disabled={query.isFetching} variant="secondary" onClick={() => { void query.refetch(); }}>Retry original page</Button></div>}
    {page && <figure className={large ? "signal-evidence-dialog__page" : "signal-criterion-packet__preview"}>
      {page.image_data_url ? <img src={page.image_data_url} alt={`Original source, ${instructorAnchor(reference.anchor ?? "")}`} /> : <p>No original-page image is available for this evidence.</p>}
      <figcaption>{page.caption}</figcaption>
    </figure>}
  </>;
  return <div className="signal-shared-packet__source">
    <Button variant="secondary" aria-label={reference.anchor?.trim() ? `Open original page: ${instructorAnchor(reference.anchor)}` : undefined} onClick={() => setDialog(true)}>{reference.anchor?.trim() ? "Open original page" : "Open saved source"}</Button>
    <Button variant="secondary" aria-expanded={preview} onClick={() => setPreview(value => !value)}>{preview ? "Hide" : "Show"} original-page preview</Button>
    {preview && image(false)}
    {dialog && <Dialog title={context ? `Evidence and explanation: ${context.title}` : `Original source: ${instructorAnchor(reference.anchor ?? "") || "Saved source"}`} wide className={context ? "signal-dialog--source-context" : undefined} onClose={() => setDialog(false)}>
      {context ? <EvidenceSourceLayout source={image(true)} context={<section aria-label="Shared assessment"><h3>{context.title}</h3><h4>Assessment and explanation</h4><p>{context.explanation || "No assessment explanation was shared for this item."}</p>{context.recordedNote && <p><strong>Recorded system note:</strong> {context.recordedNote}</p>}
        <div className="signal-criterion-packet__quotations">{references.map(entry => <figure key={entry.reference_id}><figcaption><strong>{ROLE_LABEL[entry.role]}</strong><span>{instructorAnchor(entry.anchor ?? "")}</span></figcaption><blockquote>{entry.quote}</blockquote></figure>)}</div></section>} />
        : <><blockquote>{reference.quote}</blockquote>{image(true)}</>}
    </Dialog>}
  </div>;
}

export function SharedEvidencePacket({ token, manifest, references, fallback = [], context, comparison = false }: {
  token: string; manifest?: EvidenceManifest; references: EvidenceReference[];
  fallback?: { quote: string; anchor: string }[];
  context?: SharedEvidenceContext;
  comparison?: boolean;
}) {
  const [selected, setSelected] = useState(0);
  const entries = references.length ? references : fallback;
  const paired = comparison || references.length > 1 && references.some(reference => reference.role.startsWith("agreement_") || reference.role.startsWith("reuse_"));
  const currentIndex = Math.min(selected, Math.max(0, entries.length - 1));
  return <section className="signal-shared-packet" aria-label="Saved evidence">
    <div className="signal-shared-packet__heading"><h4>Evidence from the manuscript</h4>{entries.length > 0 && <span role="status">{paired ? `${entries.length} related passages` : `Evidence ${currentIndex + 1} of ${entries.length}`}</span>}</div>
    {!paired && entries.length > 1 && <nav className="signal-shared-evidence-navigation" aria-label="Saved evidence navigation"><Button variant="secondary" disabled={currentIndex === 0} onClick={() => setSelected(currentIndex - 1)}>Previous evidence</Button><Button variant="quiet" disabled={currentIndex === entries.length - 1} onClick={() => setSelected(currentIndex + 1)}>Next evidence</Button></nav>}
    {!manifest && <p>Original-page associations could not be loaded. Saved quotations remain available.</p>}
    {references.length ? (paired ? references : references.slice(currentIndex, currentIndex + 1)).map(reference => <div className="signal-shared-packet__evidence" key={reference.reference_id}>
      <div><strong>{ROLE_LABEL[reference.role]}</strong><blockquote>{reference.quote || "No quotation was saved for this location."}</blockquote><p>{instructorAnchor(reference.anchor ?? "")}</p>
        {reference.role.startsWith("agreement_") && <p>The selected statement roles have not been verified as this study's objective and result.</p>}
        {reference.source_scope === "bounded_comparison_excerpt" && <p>Only this saved comparison excerpt is shared. Its original source page is unavailable in this report.</p>}
      </div>
      {manifest && reference.source_scope === "own_manuscript" && (reference.anchor?.trim() || reference.quote.trim()
        ? <SharedSourcePage key={`${token}:${manifest.snapshot_id}:${reference.reference_id}`} token={token} snapshot={manifest.snapshot_id} reference={reference} context={context} references={references} />
        : <p>No source location was saved for this item.</p>)}
    </div>) : (paired ? fallback : fallback.slice(currentIndex, currentIndex + 1)).map((entry, index) => <div key={index}><blockquote>{entry.quote}</blockquote><p>{instructorAnchor(entry.anchor)}</p></div>)}
    {!references.length && !fallback.length && <p>No source passages were saved for this item.</p>}
  </section>;
}
