// Share modal (F8.7, V-040, screen 4k): generate/copy/revoke/regenerate
// a read-only, tokenized link to this report. A genuinely sensitive
// action -- the copy states plainly what the link exposes and that it
// should be treated as semi-confidential, never oversells its safety.
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { Modal, ModalBackdrop } from "../components/Modal";
import { cx } from "../components/cx";
import { useCreateShareLink, useRevokeShareLink, useShareLink } from "./useShare";

const dateFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

type ExpiryPreset = "none" | "7d" | "30d" | "90d";

function expiryFromPreset(preset: ExpiryPreset): string | null {
  if (preset === "none") return null;
  const days = { "7d": 7, "30d": 30, "90d": 90 }[preset];
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString();
}

const buttonBase =
  "flex h-11 items-center justify-center rounded-md px-4 text-sm font-bold disabled:opacity-60 sm:h-9";
const secondaryButtonClass = cx(
  buttonBase,
  "border border-border-input bg-panel text-ink hover:bg-status-neutral-bg",
);
const primaryButtonClass = cx(buttonBase, "bg-action text-on-action hover:bg-action-hover");
const dangerButtonClass = cx(buttonBase, "bg-danger text-on-danger hover:bg-danger-hover");

function ExpirySelect({
  value,
  onChange,
  caption,
}: {
  value: ExpiryPreset;
  onChange: (v: ExpiryPreset) => void;
  caption?: string;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-sm font-medium text-ink">Link expires</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as ExpiryPreset)}
        className="h-11 rounded-md border border-border-input bg-panel px-3 text-base text-ink sm:h-9"
      >
        <option value="none">No expiry</option>
        <option value="7d">7 days</option>
        <option value="30d">30 days</option>
        <option value="90d">90 days</option>
      </select>
      {caption && <span className="text-xs text-ink-tertiary">{caption}</span>}
    </label>
  );
}

export function ShareModal({
  checkRunId,
  manuscriptLabel,
  onClose,
}: {
  checkRunId: number;
  manuscriptLabel: string;
  onClose: () => void;
}) {
  const { data: link, isPending: linkPending } = useShareLink(checkRunId);
  const create = useCreateShareLink(checkRunId);
  const revoke = useRevokeShareLink(checkRunId);
  const [expiryPreset, setExpiryPreset] = useState<ExpiryPreset>("none");
  const [confirming, setConfirming] = useState<"revoke" | "regenerate" | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const errorRef = useRef<HTMLParagraphElement>(null);
  const confirmPanelRef = useRef<HTMLParagraphElement>(null);
  const urlInputRef = useRef<HTMLInputElement>(null);
  const prevLinkToken = useRef<string | null>(link?.token ?? null);

  const activeError = create.error ?? revoke.error;
  useEffect(() => {
    if (activeError) errorRef.current?.focus();
  }, [activeError]);

  // ux-critic finding (P1, live-reproduced): opening a confirm sub-panel
  // -- or the URL input replacing the "Create link" form after a
  // successful create -- swaps DOM content in place without moving
  // focus, the same "component named Modal that wasn't actually one"
  // bug class Modal.tsx's own header comment says this app already
  // fixed once (BUG-020, V-055). Modal.tsx's own focus-trap effect only
  // runs on initial mount; a content swap INSIDE an already-open modal
  // needs its own explicit focus move, same as DecisionPanel.tsx's
  // `prevDecision` ref pattern for an in-page content swap.
  useEffect(() => {
    if (confirming !== null) confirmPanelRef.current?.focus();
  }, [confirming]);
  useEffect(() => {
    const token = link?.token ?? null;
    if (token !== null && prevLinkToken.current === null) {
      urlInputRef.current?.focus();
    }
    prevLinkToken.current = token;
  }, [link]);

  const shareUrl = link ? `${window.location.origin}/shared/${link.token}` : "";

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopyState("copied");
      setTimeout(() => setCopyState("idle"), 2000);
    } catch {
      setCopyState("failed");
    }
  }

  function handleCreate() {
    create.mutate({ expires_at: expiryFromPreset(expiryPreset) });
  }

  function handleConfirmRevoke() {
    revoke.mutate(undefined, { onSuccess: () => setConfirming(null) });
  }

  function handleConfirmRegenerate() {
    create.mutate(
      { expires_at: expiryFromPreset(expiryPreset) },
      { onSuccess: () => setConfirming(null) },
    );
  }

  const serverError =
    activeError instanceof ApiError ? activeError.message : activeError ? "Could not update this link. Try again." : null;

  return (
    <ModalBackdrop>
      <Modal title="Share this report" onClose={onClose} size="md">
        <div className="flex flex-col gap-3 text-sm">
          {linkPending ? (
            <p role="status" aria-live="polite" aria-busy="true" className="text-ink-secondary">
              Checking for an existing link.
            </p>
          ) : (
            <>
              <p className="text-ink-secondary">
                Anyone with this link can view {manuscriptLabel}'s status, score, flags,
                evidence, and any decision note you've added, exactly as they currently stand
                on this screen, even if you haven't made a final decision yet. It's read only:
                no sign-in, no editing, and no access to your dashboard, audit trail, or any
                other report.
              </p>
              <p className="rounded-md bg-status-attention-bg px-3 py-2.5 text-sm text-status-attention-text">
                Treat this link as semi-confidential. Anyone who has it can view the report,
                including if it's forwarded to the group being checked. You can revoke it any
                time from this screen.
              </p>

              {!link ? (
                <>
                  <ExpirySelect value={expiryPreset} onChange={setExpiryPreset} />
                  {serverError && (
                    <p
                      ref={errorRef}
                      role="alert"
                      tabIndex={-1}
                      className="text-sm font-medium text-danger"
                    >
                      <span className="sr-only">Error: </span>
                      {serverError}
                    </p>
                  )}
                  <button
                    type="button"
                    onClick={handleCreate}
                    disabled={create.isPending}
                    className={cx(primaryButtonClass, "w-fit")}
                  >
                    {create.isPending ? "Creating link." : "Create link"}
                  </button>
                </>
              ) : (
                <>
                  <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center">
                    <input
                      ref={urlInputRef}
                      readOnly
                      aria-label="Report share link"
                      value={shareUrl}
                      onFocus={(e) => e.currentTarget.select()}
                      className="h-11 min-w-0 flex-1 rounded-md border border-border-input bg-page px-3 text-base text-ink sm:h-9"
                    />
                    <button type="button" onClick={handleCopy} className={cx(primaryButtonClass, "flex-none")}>
                      {copyState === "copied" ? "Copied!" : "Copy link"}
                    </button>
                  </div>
                  <p aria-live="polite" className="sr-only">
                    {copyState === "copied" ? "Link copied to clipboard." : ""}
                  </p>
                  {copyState === "failed" && (
                    <p role="alert" className="text-sm font-medium text-danger">
                      Couldn't copy automatically. Select the link text above and copy it manually.
                    </p>
                  )}
                  <p className="text-xs text-ink-tertiary">
                    Created {dateFormatter.format(new Date(link.created_at))}.{" "}
                    {link.expires_at
                      ? `Expires ${dateFormatter.format(new Date(link.expires_at))}.`
                      : "No expiry set."}
                  </p>

                  <ExpirySelect
                    value={expiryPreset}
                    onChange={setExpiryPreset}
                    caption="Applies the next time you regenerate this link."
                  />

                  {serverError && (
                    <p
                      ref={errorRef}
                      role="alert"
                      tabIndex={-1}
                      className="text-sm font-medium text-danger"
                    >
                      <span className="sr-only">Error: </span>
                      {serverError}
                    </p>
                  )}

                  {confirming === null && (
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => setConfirming("regenerate")}
                        className={secondaryButtonClass}
                      >
                        Regenerate link
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirming("revoke")}
                        className={dangerButtonClass}
                      >
                        Revoke link
                      </button>
                    </div>
                  )}

                  {confirming === "revoke" && (
                    <div className="flex flex-col gap-2 rounded-md border border-border bg-page p-3">
                      <p ref={confirmPanelRef} tabIndex={-1} className="text-ink">
                        Revoke this link? Anyone using it loses access immediately. This can't be
                        undone, but you can create a new one anytime.
                      </p>
                      <div className="flex justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => setConfirming(null)}
                          disabled={revoke.isPending}
                          className={secondaryButtonClass}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          onClick={handleConfirmRevoke}
                          disabled={revoke.isPending}
                          className={dangerButtonClass}
                        >
                          {revoke.isPending ? "Revoking." : "Revoke link"}
                        </button>
                      </div>
                    </div>
                  )}

                  {confirming === "regenerate" && (
                    <div className="flex flex-col gap-2 rounded-md border border-border bg-page p-3">
                      <p ref={confirmPanelRef} tabIndex={-1} className="text-ink">
                        Replace this link with a new one? The current link stops working
                        immediately once you do.
                      </p>
                      <div className="flex justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => setConfirming(null)}
                          disabled={create.isPending}
                          className={secondaryButtonClass}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          onClick={handleConfirmRegenerate}
                          disabled={create.isPending}
                          className={primaryButtonClass}
                        >
                          {create.isPending ? "Creating." : "Create new link"}
                        </button>
                      </div>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </Modal>
    </ModalBackdrop>
  );
}
