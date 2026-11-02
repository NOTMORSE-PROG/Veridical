// F8/4l: token-authorized public source associations, never private source URLs.
import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { useLocation } from "react-router";
import { ApiError, api } from "../api/client";
import { instructorAnchor } from "../domain/sourceAnchor";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import type { EvidenceManifest, EvidencePage, EvidenceReference } from "./evidenceTypes";
import { isPermanentSourceError, verifyEvidencePage } from "./RecordedEvidencePage";
import { EvidenceSourceLayout } from "./EvidenceSourceLayout";
import { EvidenceItem } from "./EvidenceItem";

// An adviser cannot upload anything, so the shared view says what remains.
const SHARED_PAGE_GONE = "The original page is not available in this shared report. The saved quotation above is what VERIDICAL recorded.";

const ROLE_LABEL: Record<EvidenceReference["role"], string> = {
  criterion_evidence: "Recorded criterion passage", criterion_scope: "Recorded assessment scope",
  finding_excerpt: "Recorded finding passage", finding_scope: "Recorded finding location",
  agreement_intent: "Statement A, selected as an objective", agreement_outcome: "Statement B, selected as a result",
  undecided_intent: "Possible objective", undecided_outcome: "Possible result it was compared with",
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

type SharedPageQuery = ReturnType<typeof useSharedSourcePage>;

function useSharedSourcePage(token: string, snapshot: string, referenceId: string, enabled: boolean) {
  return useQuery({ queryKey: ["shared-source-page", token, snapshot, referenceId],
    enabled, gcTime: 0, retry: false,
    queryFn: async ({ signal }) => {
      await Promise.resolve(); signal.throwIfAborted();
      const page = await api.getPublic<EvidencePage>(`/shared/${encodeURIComponent(token)}/evidence/${referenceId}/page?snapshot_id=${snapshot}`, { signal });
      return verifyEvidencePage(page, snapshot, referenceId);
    },
  });
}

function SharedPageImage({ query, reference, large }: { query: SharedPageQuery; reference: EvidenceReference; large: boolean }) {
  const page = !query.isError && query.fetchStatus === "idle" ? query.data : undefined;
  return <>
    {query.isFetching && <p role="status">Loading original page.</p>}
    {query.isError && (isPermanentSourceError(query.error)
      ? <p role="status">{SHARED_PAGE_GONE}</p>
      : <div role="alert"><p>{query.error instanceof ApiError ? query.error.message : "The original page could not be loaded."}</p><Button disabled={query.isFetching} variant="secondary" onClick={() => { void query.refetch(); }}>Retry original page</Button></div>)}
    {page && <figure className={large ? "signal-evidence-dialog__page" : "signal-criterion-packet__preview"}>
      {page.image_data_url ? <img src={page.image_data_url} alt={`Original source, ${instructorAnchor(reference.anchor ?? "")}`} /> : <p>No original-page image is available for this evidence.</p>}
      <figcaption>{page.caption}</figcaption>
    </figure>}
  </>;
}

/** One statement's original page in a dialog, with what it was compared with
 * beside it. Nothing loads until the button is pressed, and the button stays
 * when the page turns out to be gone so focus has somewhere to return. */
export function SharedPageButton({ token, snapshot, reference, accessibleName, title, context, label = "Open page", className }: {
  token: string; snapshot: string; reference: EvidenceReference;
  accessibleName: string; title: string; context: ReactNode;
  label?: string; className?: string;
}) {
  const [dialog, setDialog] = useState(false);
  const query = useSharedSourcePage(token, snapshot, reference.reference_id, dialog);
  return <>
    <Button variant="quiet" className={className} aria-label={accessibleName} aria-haspopup="dialog" onClick={() => setDialog(true)}>{label}</Button>
    {dialog && <Dialog title={title} wide className="signal-dialog--source-context signal-dialog--statement-page" onClose={() => setDialog(false)}>
      {/* Focusable so the page can be scrolled from the keyboard. */}
      <EvidenceSourceLayout source={<div className="signal-statement-page" tabIndex={0} role="region" aria-label={`Original page, ${instructorAnchor(reference.anchor ?? "")}`}><SharedPageImage query={query} reference={reference} large /></div>} context={context} />
    </Dialog>}
  </>;
}

function SharedSourcePage({ token, snapshot, reference, context, references, initialPreview = false }: {
  token: string; snapshot: string; reference: EvidenceReference;
  context?: SharedEvidenceContext; references: readonly EvidenceReference[];
  initialPreview?: boolean;
}) {
  const location = useLocation();
  return <SharedPageSession key={`${location.key}:${token}:${snapshot}:${reference.reference_id}`} token={token} snapshot={snapshot} reference={reference} context={context} references={references} initialPreview={initialPreview} />;
}

function SharedPageSession({ token, snapshot, reference, context, references, initialPreview }: {
  token: string; snapshot: string; reference: EvidenceReference;
  context?: SharedEvidenceContext; references: readonly EvidenceReference[];
  initialPreview: boolean;
}) {
  // Only the first item's preview opens by itself: stacked items would
  // otherwise each request a page at once.
  const [preview, setPreview] = useState(initialPreview);
  const [dialog, setDialog] = useState(false);
  const query = useSharedSourcePage(token, snapshot, reference.reference_id, preview || dialog);
  const image = (large: boolean) => <SharedPageImage query={query} reference={reference} large={large} />;
  // Once the page is known to be gone, its buttons go too: only the message stays.
  if (isPermanentSourceError(query.error)) return <div className="signal-shared-packet__source"><p role="status">{SHARED_PAGE_GONE}</p></div>;
  return <div className="signal-shared-packet__source">
    <Button variant="secondary" aria-label={reference.anchor?.trim() ? `Open original page: ${instructorAnchor(reference.anchor)}` : undefined} onClick={() => setDialog(true)}>{reference.anchor?.trim() ? "Open original page" : "Open saved source"}</Button>
    <Button variant="secondary" aria-expanded={preview} onClick={() => setPreview(value => !value)}>{preview ? "Hide" : "Show"} page preview</Button>
    {preview && image(false)}
    {dialog && <Dialog title={context ? `Evidence and explanation: ${context.title}` : `Original source: ${instructorAnchor(reference.anchor ?? "") || "Saved source"}`} wide className={context ? "signal-dialog--source-context" : undefined} onClose={() => setDialog(false)}>
      {context ? <EvidenceSourceLayout source={image(true)} context={<section aria-label="Shared assessment"><h3>{context.title}</h3><h4>Assessment and explanation</h4><p>{context.explanation || "No assessment explanation was shared for this item."}</p>{context.recordedNote && <p><strong>Recorded system note:</strong> {context.recordedNote}</p>}
        <div className="signal-criterion-packet__quotations">{references.map(entry => <figure key={entry.reference_id}><figcaption><strong>{ROLE_LABEL[entry.role]}</strong><span>{instructorAnchor(entry.anchor ?? "")}</span></figcaption><blockquote>{entry.quote}</blockquote></figure>)}</div></section>} />
        : <><blockquote>{reference.quote}</blockquote>{image(true)}</>}
    </Dialog>}
  </div>;
}

// The default role of a section needs no label; others say what the passage is.
const DEFAULT_ROLES = new Set<EvidenceReference["role"]>(["criterion_evidence", "finding_excerpt"]);

export function SharedEvidencePacket({ token, manifest, references, fallback = [], context, embedded = false, previewClosed = false }: {
  /** Start with every page preview closed (the page is one click away). */
  previewClosed?: boolean;
  token: string; manifest?: EvidenceManifest; references: EvidenceReference[];
  fallback?: { quote: string; anchor: string }[];
  context?: SharedEvidenceContext;
  comparison?: boolean;
  /** Inside a section that already has its own heading. */
  embedded?: boolean;
}) {
  const list = <div className="signal-evidence-list">
    {!manifest && <p>Original-page associations could not be loaded. Saved quotations remain available.</p>}
    {references.length ? references.map((reference, index) => <EvidenceItem key={reference.reference_id}
      label={DEFAULT_ROLES.has(reference.role) ? undefined : ROLE_LABEL[reference.role]}
      quote={reference.quote} missing={reference.anchor?.trim() ? "No quotation was saved for this location." : "No quotation or location was saved for this item."}
      anchor={instructorAnchor(reference.anchor ?? "") || undefined}
      notes={<>
        {reference.role.startsWith("agreement_") && <p>The selected statement roles have not been verified as this study's objective and result.</p>}
        {reference.source_scope === "bounded_comparison_excerpt" && <p>Only this saved comparison excerpt is shared. Its original source page is unavailable in this report.</p>}
      </>}
      actions={manifest && reference.source_scope === "own_manuscript" && (reference.anchor?.trim() || reference.quote.trim())
        ? <SharedSourcePage key={`${token}:${manifest.snapshot_id}:${reference.reference_id}`} token={token} snapshot={manifest.snapshot_id} reference={reference} context={context} references={references}
          initialPreview={!previewClosed && index === references.findIndex(item => item.source_scope === "own_manuscript" && Boolean(item.anchor?.trim() || item.quote.trim()))} />
        : undefined}
      result={manifest && reference.source_scope === "own_manuscript" && reference.quote.trim() && !reference.anchor?.trim()
        ? <p>No source location was saved for this item.</p> : undefined} />)
      : fallback.map((entry, index) => <EvidenceItem key={index} quote={entry.quote} anchor={instructorAnchor(entry.anchor) || undefined} />)}
    {!references.length && !fallback.length && <p>No source passages were saved for this item.</p>}
  </div>;
  if (embedded) return list;
  return <section className="signal-shared-packet signal-item-section" aria-labelledby={undefined}>
    <h4 className="signal-item-section__title">Quoted evidence</h4>
    {list}
  </section>;
}
