import { useEffect, useState } from "react";
import { BASE_URL } from "../api/client";
import type { PublicEvidenceImageOut } from "../api/types";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";

export function SharedEvidenceImage({ token, evidence }: { token: string; evidence: PublicEvidenceImageOut }) {
  const [image, setImage] = useState<{ key: string; url: string }>();
  const [error, setError] = useState(false);
  const [enlarged, setEnlarged] = useState(false);
  const identity = `${token}:${evidence.image_id}:${evidence.image_sha256}`;
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    setError(false);
    setEnlarged(false);
    if (!evidence.image_id || !/^e-[a-f0-9]{24}$/.test(evidence.image_id) || !evidence.image_sha256 || !/^[a-f0-9]{64}$/.test(evidence.image_sha256)) { setError(true); return; }
    void fetch(`${BASE_URL}/shared/${encodeURIComponent(token)}/evidence-images/${evidence.image_id}`, { credentials: "omit", signal: controller.signal })
      .then(async response => {
        if (!response.ok || !response.headers.get("Content-Type")?.startsWith("image/png")) throw new Error("Image unavailable");
        const bytes = await response.arrayBuffer();
        const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), value => value.toString(16).padStart(2, "0")).join("");
        if (digest !== evidence.image_sha256) throw new Error("Image identity mismatch");
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(new Blob([bytes], { type: "image/png" }));
        setImage({ key: identity, url: objectUrl });
      }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [identity, token, evidence.image_id, evidence.image_sha256]);
  return <figure className="signal-shared-evidence-image">
    <figcaption><strong>{evidence.label}</strong><span>Original manuscript · PDF page {evidence.page} · {evidence.precision === "page_bbox" ? "Complete quotation boxed" : "Page only; no exact mark"}</span></figcaption>
    <blockquote><span>“{evidence.quote}”</span><cite>{evidence.anchor}</cite></blockquote>
    {error ? <p role="status">This source image is unavailable. The saved quotation and location remain above.</p> : image?.key === identity ? <><a className="signal-evidence-source-link" href={image.url} onClick={event => { event.preventDefault(); setEnlarged(true); }}>View larger source image</a><img loading="lazy" width={evidence.image_width} height={evidence.image_height} src={image.url} onError={() => setError(true)} alt={`Original manuscript PDF page ${evidence.page}. ${evidence.label}. ${evidence.precision_note}`} />{enlarged && <Dialog wide title={`Original manuscript: ${evidence.anchor}`} onClose={() => setEnlarged(false)}><p>{evidence.label}</p><p>“{evidence.quote}”</p><img className="signal-shared-enlarged-image" src={image.url} alt={`Original manuscript: ${evidence.anchor}. ${evidence.precision_note}`} /></Dialog>}</> : <p role="status">Loading the original source image…</p>}
  </figure>;
}

export function SharedEvidencePager({ token, images }: { token: string; images: PublicEvidenceImageOut[] }) {
  const [selected, setSelected] = useState(0);
  const [both, setBoth] = useState(false);
  const index = Math.min(selected, Math.max(0, images.length - 1));
  if (!images.length) return <p>No original page image was shared for this item. Read the saved quotation and location.</p>;
  return <section className="signal-shared-source" aria-label="Original source evidence">{images.length === 2 && <Button variant="quiet" aria-pressed={both} onClick={() => setBoth(!both)}>{both ? "Show one page at a time" : "Show both source pages"}</Button>}{both && images.length === 2 ? <div className="signal-shared-source-pair">{images.map((entry, position) => <SharedEvidenceImage key={`${token}:${entry.evidence_id}:${position}`} token={token} evidence={entry} />)}</div> : <><nav className="signal-evidence-navigation" aria-label="Shared evidence pages"><Button variant="quiet" disabled={index === 0} onClick={() => setSelected(index - 1)}>Previous evidence</Button><span aria-live="polite">Evidence {index + 1} of {images.length}</span><Button variant="quiet" disabled={index === images.length - 1} onClick={() => setSelected(index + 1)}>Next evidence</Button></nav><SharedEvidenceImage key={`${token}:${images[index].evidence_id}:${index}`} token={token} evidence={images[index]} /></>}</section>;
}

export function SharedEvidenceImages({ token, images }: { token: string; images: PublicEvidenceImageOut[] }) {
  return <section className="signal-shared-evidence" aria-labelledby="shared-evidence-heading"><h2 id="shared-evidence-heading">Original manuscript evidence</h2><p>These images retain their original PDF page numbers and source qualifications. The report identifies how its review and grades were prepared.</p><div>{images.map(entry => <SharedEvidenceImage key={entry.evidence_id} token={token} evidence={entry} />)}</div></section>;
}
