import { useEffect, useRef, useState } from "react";
import { SHARE_EXPIRY_OPTIONS } from "../config/ui";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { useCreateShareLink, useRevokeShareLink, useShareLink } from "./useShare";

type ExpirySelection = "current" | (typeof SHARE_EXPIRY_OPTIONS)[number]["value"];

const dateFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

function expiresAt(selection: ExpirySelection, current: string | null): string | null {
  if (selection === "current") return current;
  const option = SHARE_EXPIRY_OPTIONS.find((entry) => entry.value === selection);
  if (!option?.days) return null;
  const expiry = new Date();
  expiry.setDate(expiry.getDate() + option.days);
  return expiry.toISOString();
}

export function SignalShareDialog({ checkRunId, manuscriptLabel, onClose }: {
  checkRunId: number;
  manuscriptLabel: string;
  onClose: () => void;
}) {
  const { data: link, isPending } = useShareLink(checkRunId);
  const create = useCreateShareLink(checkRunId);
  const revoke = useRevokeShareLink(checkRunId);
  const [selection, setSelection] = useState<ExpirySelection>("none");
  const [confirming, setConfirming] = useState<"regenerate" | "revoke">();
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const [announcement, setAnnouncement] = useState("");
  const confirmRef = useRef<HTMLDivElement>(null);
  const urlRef = useRef<HTMLInputElement>(null);
  const previousToken = useRef<string | undefined>(undefined);

  useEffect(() => {
    if (link) setSelection("current");
    if (link?.token && link.token !== previousToken.current) {
      setAnnouncement(previousToken.current ? "A replacement link is ready." : "Share link created.");
      queueMicrotask(() => urlRef.current?.focus());
    }
    previousToken.current = link?.token;
  }, [link]);

  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
  }, [confirming]);

  const shareUrl = link ? `${window.location.origin}/shared/${link.token}` : "";
  const error = create.error ?? revoke.error;
  const selectedOption = selection === "current" ? undefined : SHARE_EXPIRY_OPTIONS.find((entry) => entry.value === selection);

  async function copy() {
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopyState("copied");
      setAnnouncement("Link copied to clipboard.");
    } catch {
      setCopyState("failed");
    }
  }

  return (
    <Dialog title="Share this report" onClose={create.isPending || revoke.isPending ? undefined : onClose}>
      <div className="signal-share-dialog">
        <p aria-live="polite" className="signal-live-region">{announcement}</p>
        <p>Anyone with the link can view {manuscriptLabel}'s readiness band, criterion outcomes, integrity signals, evidence, and any decision note as they currently stand.</p>
        <Alert title="Treat this link as semi-confidential" tone="warning">No sign-in is required, and a recipient can forward it. The link is read-only and gives no access to your Review Desk, Audit, settings, or other reports. You can revoke it here.</Alert>

        {isPending ? <div role="status" aria-busy="true" className="signal-desk-loading"><span>Checking for an active link…</span><i /><i /></div> : !link ? (
          <div className="signal-share-dialog__form">
            <label><span>Link expires</span><select value={selection} onChange={(event) => setSelection(event.target.value as ExpirySelection)}>{SHARE_EXPIRY_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
            <Button variant="brand" busy={create.isPending} onClick={() => create.mutate({ expires_at: expiresAt(selection, null) })}>Create read-only link</Button>
          </div>
        ) : (
          <div className="signal-share-dialog__active">
            <div className="signal-share-dialog__url"><input ref={urlRef} readOnly aria-label="Report share link" value={shareUrl} onFocus={(event) => event.currentTarget.select()} /><Button variant="brand" onClick={copy}>{copyState === "copied" ? "Copied" : "Copy link"}</Button></div>
            {copyState === "failed" && <Alert title="Could not copy automatically" tone="error" role="alert">Select the link above and copy it manually.</Alert>}
            <p className="signal-field-hint">Created {dateFormatter.format(new Date(link.created_at))}. {link.expires_at ? `Expires ${dateFormatter.format(new Date(link.expires_at))}.` : "No expiry set."}</p>
            <label><span>Replacement link expiry</span><select value={selection} onChange={(event) => setSelection(event.target.value as ExpirySelection)}><option value="current">Keep current expiry</option>{SHARE_EXPIRY_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
            {selection !== "current" && <Alert title="Not applied yet" tone="info" role="status">Regenerating will issue a new link with {selectedOption?.label.toLowerCase()}. The current link will stop working immediately.</Alert>}
            {!confirming && <div className="signal-share-dialog__actions"><Button variant="secondary" onClick={() => setConfirming("regenerate")}>Regenerate link</Button><Button variant="danger" onClick={() => setConfirming("revoke")}>Revoke link</Button></div>}
            {confirming && <div ref={confirmRef} tabIndex={-1} className="signal-share-dialog__confirm"><p>{confirming === "revoke" ? "Revoke this link now? Anyone using it will lose access immediately." : "Replace the current link now? It will stop working as soon as the new link is issued."}</p><div><Button variant="secondary" disabled={create.isPending || revoke.isPending} onClick={() => setConfirming(undefined)}>Cancel</Button>{confirming === "revoke" ? <Button variant="danger" busy={revoke.isPending} onClick={() => revoke.mutate(undefined, { onSuccess: () => { setConfirming(undefined); setAnnouncement("Share link revoked."); } })}>Revoke link</Button> : <Button variant="brand" busy={create.isPending} onClick={() => create.mutate({ expires_at: expiresAt(selection, link.expires_at) }, { onSuccess: () => setConfirming(undefined) })}>Create replacement link</Button>}</div></div>}
          </div>
        )}
        {error && <Alert title="Could not update this share link" tone="error" role="alert">{error instanceof Error ? error.message : "Try again."}</Alert>}
      </div>
    </Dialog>
  );
}
