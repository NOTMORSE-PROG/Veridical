import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { type AuthOwner, useAuthOwner } from "../auth/queryOwner";
import { SHARE_EXPIRY_OPTIONS } from "../config/ui";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { useCreateShareLink, useRevokeShareLink, useShareLink } from "./useShare";

type ExpirySelection = "current" | (typeof SHARE_EXPIRY_OPTIONS)[number]["value"];
type Props = { checkRunId: number; manuscriptLabel: string; onClose: () => void };
const dateFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

function expiresAt(selection: ExpirySelection, current: string | null): string | null {
  if (selection === "current") return current;
  const option = SHARE_EXPIRY_OPTIONS.find(entry => entry.value === selection);
  if (!option?.days) return null;
  const expiry = new Date(); expiry.setDate(expiry.getDate() + option.days);
  return expiry.toISOString();
}

// Screen4k, F8.7: retire the entire interaction session before replacement
// report/auth children commit, including confirmation and clipboard feedback.
export function SignalShareDialog(props: Props) {
  const owner = useAuthOwner();
  const currentRun = useRef(props.checkRunId); currentRun.current = props.checkRunId;
  const [session, setSession] = useState(() => ({ owner, runId: props.checkRunId, epoch: 0 }));
  if (session.owner !== owner || session.runId !== props.checkRunId) {
    setSession({ owner, runId: props.checkRunId, epoch: session.epoch + 1 });
    return null;
  }
  if (!owner.isCurrent()) return null;
  return <ShareDialogSession key={session.epoch} {...props} owner={owner}
    shouldRestoreFocus={() => owner.isCurrent() && currentRun.current === session.runId} />;
}

function ShareDialogSession({ checkRunId, manuscriptLabel, onClose, owner, shouldRestoreFocus }: Props & {
  owner: AuthOwner; shouldRestoreFocus: () => boolean;
}) {
  const client = useQueryClient();
  const query = useShareLink(checkRunId);
  const link = query.data;
  const create = useCreateShareLink(checkRunId);
  const revoke = useRevokeShareLink(checkRunId);
  const [newSelection, setNewSelection] = useState<ExpirySelection>("none");
  const [replacementSelection, setReplacementSelection] = useState<ExpirySelection>("current");
  const [confirming, setConfirming] = useState<{ action: "regenerate" | "revoke"; token: string }>();
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const [announcement, setAnnouncement] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const needsRead = useRef(false);
  const live = useRef(true);
  const inFlight = useRef<{ claim: FocusContinuation | null } | null>(null);
  const copyAttempt = useRef<object | null>(null);
  const focusIntent = useRef<{ claim: FocusContinuation; target: "url" | "create" | "status" } | null>(null);
  const retryAttempt = useRef<{ claim: FocusContinuation | null } | null>(null);
  const statusRef = useRef<HTMLParagraphElement>(null);
  const [focusEpoch, setFocusEpoch] = useState(0);
  const confirmRef = useRef<HTMLDivElement>(null);
  const urlRef = useRef<HTMLInputElement>(null);
  const createRef = useRef<HTMLButtonElement>(null);
  const isCurrent = useCallback(() => live.current && owner.isCurrent(), [owner]);
  const confirmed = query.isSuccess && !query.isError && query.fetchStatus === "idle" && !uncertain;
  const busy = create.isPending || revoke.isPending;
  const refreshing = query.fetchStatus !== "idle" || query.isPending;
  const shareUrl = link ? `${window.location.origin}/shared/${encodeURIComponent(link.token)}` : "";
  const selectedOption = SHARE_EXPIRY_OPTIONS.find(entry => entry.value === replacementSelection);

  useLayoutEffect(() => {
    live.current = true;
    return () => { live.current = false; focusIntent.current?.claim.dispose(); inFlight.current?.claim?.dispose(); retryAttempt.current?.claim?.dispose(); copyAttempt.current = null; };
  }, []);
  useEffect(() => { if (confirming && isCurrent()) confirmRef.current?.focus(); }, [confirming, isCurrent]);
  useEffect(() => { setCopyState("idle"); copyAttempt.current = null; setConfirming(undefined); }, [link?.token]);
  useEffect(() => {
    const intent = focusIntent.current;
    if (!intent || (intent.target === "status" ? refreshing || busy : !confirmed)) return;
    if (isCurrent()) intent.claim.focus(intent.target === "status" ? statusRef.current : intent.target === "url" ? urlRef.current : createRef.current);
    else intent.claim.dispose();
    focusIntent.current = null;
  }, [focusEpoch, confirmed, refreshing, busy, isCurrent]);

  function canAct() {
    const state = client.getQueryState(["share", checkRunId]);
    return isCurrent() && !needsRead.current && !inFlight.current && state?.status === "success"
      && state.fetchStatus === "idle" && state.data === link;
  }
  async function retry(trigger: HTMLButtonElement) {
    if (!isCurrent() || inFlight.current || retryAttempt.current) return;
    const claim = document.activeElement === trigger && document.hasFocus() ? claimFocusContinuation(trigger) : null;
    const attempt = { claim }; retryAttempt.current = attempt;
    const result = await query.refetch();
    if (!isCurrent()) { claim?.dispose(); return; }
    if (result.isSuccess) {
      needsRead.current = false; setUncertain(false); create.reset(); revoke.reset();
      setAnnouncement("Link status refreshed.");
    }
    if (claim) {
      focusIntent.current?.claim.dispose(); focusIntent.current = { claim, target: "status" };
      setFocusEpoch(epoch => epoch + 1);
    }
    if (retryAttempt.current === attempt) retryAttempt.current = null;
  }
  async function copy() {
    if (!canAct() || !link) return;
    const token = link.token;
    const attempt = {}; copyAttempt.current = attempt;
    try {
      await navigator.clipboard.writeText(shareUrl);
      if (!isCurrent() || copyAttempt.current !== attempt
        || client.getQueryData<{ token: string } | null>(["share", checkRunId])?.token !== token) return;
      setCopyState("copied"); setAnnouncement("Link copied to clipboard.");
    } catch {
      if (isCurrent() && copyAttempt.current === attempt
        && client.getQueryData<{ token: string } | null>(["share", checkRunId])?.token === token) setCopyState("failed");
    }
  }
  function change(action: "create" | "regenerate" | "revoke", trigger: HTMLButtonElement) {
    if (!canAct() || (action !== "create" && (!link || confirming?.token !== link.token || confirming.action !== action))) return;
    focusIntent.current?.claim.dispose(); focusIntent.current = null;
    const claim = document.activeElement === trigger && document.hasFocus() ? claimFocusContinuation(trigger) : null;
    const attempt = { claim }; inFlight.current = attempt;
    setAnnouncement("");
    const success = (ack: unknown) => {
      if (!isCurrent()) { claim?.dispose(); return; }
      setConfirming(undefined); setCopyState("idle");
      const currentLink = client.getQueryData<{ token: string } | null>(["share", checkRunId]);
      const matches = action === "revoke" ? currentLink === null : currentLink?.token === (ack as { token?: string })?.token;
      setAnnouncement(matches ? action === "revoke" ? "Share link revoked." : action === "regenerate" ? "A replacement link is ready." : "Share link created."
        : "Sharing change recorded. Current link status is shown below.");
      if (action !== "revoke") setReplacementSelection("current");
      if (claim) { focusIntent.current = { claim, target: currentLink ? "url" : "create" }; setFocusEpoch(epoch => epoch + 1); }
    };
    const failure = () => {
      claim?.dispose();
      if (isCurrent()) { needsRead.current = true; setUncertain(true); setConfirming(undefined); }
    };
    const settled = () => { if (inFlight.current === attempt) inFlight.current = null; };
    if (action === "revoke") revoke.mutate(undefined, { onSuccess: success, onError: failure, onSettled: settled });
    else create.mutate({ expires_at: expiresAt(action === "create" ? newSelection : replacementSelection, link?.expires_at ?? null) },
      { onSuccess: success, onError: failure, onSettled: settled });
  }
  const retryButton = <Button variant="secondary" onClick={event => { void retry(event.currentTarget); }}>Retry link status</Button>;
  const activeConfirmation = confirming?.token === link?.token ? confirming : undefined;
  const statusText = busy ? "Changing link\u2026" : refreshing
    ? query.fetchStatus === "paused" ? "Waiting for a connection to check link status." : link !== undefined ? "Updating link status\u2026" : "Checking for an active link\u2026"
    : uncertain ? "Could not confirm the change."
    : query.isError ? link === undefined ? "Could not check link status." : "Current link status could not be confirmed." : link ? "Active read-only link." : "No active link.";
  return <Dialog title="Share this report" shouldRestoreFocus={shouldRestoreFocus}
    onClose={busy ? undefined : () => { if (!inFlight.current && isCurrent()) onClose(); }}>
    <div className="signal-share-dialog">
      <p aria-live="polite" className="signal-live-region">{announcement}</p>
      <p ref={statusRef} tabIndex={-1} role="status" aria-live="polite" aria-busy={busy || query.fetchStatus === "fetching" || undefined}><strong>{statusText}</strong></p>
      {!busy && !refreshing && (uncertain ? <Alert title="Check link status before continuing" tone="error" role="alert"><p>Check link status before trying again.</p>{retryButton}</Alert>
        : query.isError ? <Alert title="Check link status before continuing" tone="error" role="alert"><p>{link === undefined ? "An active link may still exist. Try again before creating or changing a link." : link ? "A link was present when last checked. It may still work, but its current status is unknown." : "No active link was recorded at the last check. Try again before creating a link."}</p>{retryButton}</Alert>
        : confirmed ? <p>{link ? "This link opens" : "Creating a link will share"} {manuscriptLabel}'s current report, including its readiness band, criterion results, integrity findings, evidence and any decision note.</p> : null)}
      <p className="signal-field-hint">Shared links require no sign-in and can be forwarded. They are read-only and do not give access to your Review Desk, Audit, settings or other reports.</p>
      {confirmed && !busy && (!link ? <div className="signal-share-dialog__form">
        <label><span>Link expires</span><select disabled={busy} value={newSelection} onChange={event => setNewSelection(event.target.value as ExpirySelection)}>{SHARE_EXPIRY_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
        <Button ref={createRef} variant="brand" busy={create.isPending} onClick={event => change("create", event.currentTarget)}>Create read-only link</Button>
      </div> : <div className="signal-share-dialog__active">
        <div className="signal-share-dialog__url"><input ref={urlRef} readOnly aria-label="Report share link" value={shareUrl} onFocus={event => event.currentTarget.select()} /><Button variant="brand" disabled={busy} onClick={() => { void copy(); }}>{copyState === "copied" ? "Copied" : "Copy link"}</Button></div>
        {copyState === "failed" && <Alert title="Could not copy automatically" tone="error" role="alert">Select the link above and copy it manually.</Alert>}
        <p className="signal-field-hint">Created {dateFormatter.format(new Date(link.created_at))}. {link.expires_at ? `Expires ${dateFormatter.format(new Date(link.expires_at))}.` : "No expiry set."}</p>
        <label><span>Replacement link expiry</span><select disabled={busy} value={replacementSelection} onChange={event => setReplacementSelection(event.target.value as ExpirySelection)}><option value="current">Keep current expiry</option>{SHARE_EXPIRY_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
        {replacementSelection !== "current" && <Alert title="Replacement options" tone="info" role="status">Regenerating will issue a new link with {selectedOption?.label.toLowerCase()}. The current link will stop working immediately.</Alert>}
        {!activeConfirmation && <div className="signal-share-dialog__actions"><Button variant="secondary" onClick={() => { if (canAct()) setConfirming({ action: "regenerate", token: link.token }); }}>Regenerate link</Button><Button variant="danger" onClick={() => { if (canAct()) setConfirming({ action: "revoke", token: link.token }); }}>Revoke link</Button></div>}
        {activeConfirmation && <div ref={confirmRef} tabIndex={-1} className="signal-share-dialog__confirm"><p>{activeConfirmation.action === "revoke" ? "Revoke this link now? Anyone using it will lose access immediately." : "Replace the current link now? It will stop working as soon as the new link is issued."}</p><div><Button variant="secondary" disabled={busy} onClick={() => { if (!inFlight.current && isCurrent()) setConfirming(undefined); }}>Cancel</Button>{activeConfirmation.action === "revoke" ? <Button variant="danger" busy={revoke.isPending} onClick={event => change("revoke", event.currentTarget)}>Revoke link</Button> : <Button variant="brand" busy={create.isPending} onClick={event => change("regenerate", event.currentTarget)}>Create replacement link</Button>}</div></div>}
      </div>)}
    </div>
  </Dialog>;
}
