import { useEffect, useState } from "react";
import { BASE_URL } from "../api/client";
import type { PublicEvidenceImageOut } from "../api/types";

function SharedEvidenceImage({ token, evidence }: { token: string; evidence: PublicEvidenceImageOut }) {
  const [image, setImage] = useState<{ key: string; url: string }>();
  const [error, setError] = useState(false);
  const identity = `${token}:${evidence.image_id}:${evidence.image_sha256}`;
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    setError(false);
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
    {error ? <p role="status">This source image is unavailable. The saved quotation and location remain above.</p> : image?.key === identity ? <img loading="lazy" width={evidence.image_width} height={evidence.image_height} src={image.url} alt={`Original manuscript PDF page ${evidence.page}. ${evidence.label}. ${evidence.precision_note}`} /> : <p role="status">Loading the original source image…</p>}
  </figure>;
}

export function SharedEvidenceImages({ token, images }: { token: string; images: PublicEvidenceImageOut[] }) {
  return <section className="signal-shared-evidence" aria-labelledby="shared-evidence-heading"><h2 id="shared-evidence-heading">Original manuscript evidence</h2><p>These images retain their original PDF page numbers and source qualifications. Prepared document review; model grading was skipped.</p><div>{images.map(entry => <SharedEvidenceImage key={entry.evidence_id} token={token} evidence={entry} />)}</div></section>;
}
