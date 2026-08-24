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

// BUG-090: "current" is the sentinel that makes the live link and the
// select's rest state provably agree by construction (see the invariant
// note on ExpirySelect below) — never derived from a guessed preset
// against the link's real `expires_at`, which would be wrong for a link
// created days ago (a "7 days" preset from 6 days back now has ~1 day
// left, not "7 days"). Only the regenerate-form (an existing link) ever
// uses "current"; the create-form has no live link yet, so it's never a
// valid option there.
type ExpirySelection = "current" | "none" | "7d" | "30d" | "90d";

function expiryFromSelection(
  selection: ExpirySelection,
  currentExpiresAt: string | null,
): string | null {
  if (selection === "current") return currentExpiresAt;
  if (selection === "none") return null;
  const days = { "7d": 7, "30d": 30, "90d": 90 }[selection];
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString();
}

// Vocabulary reused verbatim across the option labels, the pending
// notice, and the Regenerate button's own label — Nielsen consistency,
// never a second phrasing for the same fact.
const OPTION_LABEL: Record<Exclude<ExpirySelection, "current">, string> = {
  none: "no expiry",
  "7d": "7 days",
  "30d": "30 days",
  "90d": "90 days",
};
const PENDING_DESCRIPTION: Record<Exclude<ExpirySelection, "current">, string> = {
  none: "has no expiry",
  "7d": "expires in 7 days",
  "30d": "expires in 30 days",
  "90d": "expires in 90 days",
};

const buttonBase =
  "flex h-11 items-center justify-center rounded-md px-4 text-sm font-bold disabled:opacity-60 sm:h-9";
const secondaryButtonClass = cx(
  buttonBase,
  "border border-border-input bg-panel text-ink hover:bg-status-neutral-bg",
);
const primaryButtonClass = cx(buttonBase, "bg-action text-on-action hover:bg-action-hover");
const dangerButtonClass = cx(buttonBase, "bg-danger text-on-danger hover:bg-danger-hover");

function ExpirySelect({
  id,
  label,
  value,
  onChange,
  options,
  describedById,
}: {
  id: string;
  label: string;
  value: ExpirySelection;
  onChange: (v: ExpirySelection) => void;
  options: { value: ExpirySelection; label: string }[];
  describedById?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium text-ink">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value as ExpirySelection)}
        aria-describedby={describedById}
        // BUG-090 (ux-critic finding): measured 223px against ~464px of
        // available modal width with no `w-full` — a pre-existing gap
        // that became an active clipping risk once a longer "Keep
        // current (expires …)" option landed.
        className="h-11 w-full rounded-md border border-border-input bg-panel px-3 text-base text-ink sm:h-9"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
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
  const [expirySelection, setExpirySelection] = useState<ExpirySelection>("none");
  const [confirming, setConfirming] = useState<"revoke" | "regenerate" | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const [announcement, setAnnouncement] = useState("");
  const errorRef = useRef<HTMLParagraphElement>(null);
  const confirmPanelRef = useRef<HTMLParagraphElement>(null);
  const urlInputRef = useRef<HTMLInputElement>(null);
  const createButtonRef = useRef<HTMLButtonElement>(null);
  const regenerateButtonRef = useRef<HTMLButtonElement>(null);
  const revokeButtonRef = useRef<HTMLButtonElement>(null);
  const prevLinkToken = useRef<string | null>(link?.token ?? null);
  const prevConfirming = useRef<"revoke" | "regenerate" | null>(null);
  const cancelledRef = useRef(false);

  const activeError = create.error ?? revoke.error;
  useEffect(() => {
    if (activeError) errorRef.current?.focus();
  }, [activeError]);

  // BUG-090: re-anchors to the live truth whenever it changes -- initial
  // load of an existing link, and again after a successful regenerate
  // swaps in a new token. Discards any leftover pending selection from
  // before a click so a stale, un-applied choice can never resurface and
  // be mistaken for something that took effect.
  useEffect(() => {
    if (link) setExpirySelection("current");
  }, [link?.token]);

  // ux-critic finding (P1, live-reproduced): opening a confirm sub-panel
  // -- or the URL input replacing the "Create link" form after a
  // successful create -- swaps DOM content in place without moving
  // focus, the same "component named Modal that wasn't actually one"
  // bug class Modal.tsx's own header comment says this app already
  // fixed once (BUG-020, V-055). Modal.tsx's own focus-trap effect only
  // runs on initial mount; a content swap INSIDE an already-open modal
  // needs its own explicit focus move, same as DecisionPanel.tsx's
  // `prevDecision` ref pattern for an in-page content swap.
  //
  // `ux-critic` follow-up finding (P1, live-reproduced, this session):
  // that first pass only handled the OPENING of a confirm panel and the
  // very FIRST link ever created -- every "returning" transition (Cancel
  // on either panel, a successful regenerate of an EXISTING link, a
  // successful revoke) had no focus-management code path at all and fell
  // through to the browser default, `<body>` -- exactly the same bug
  // class, just the other three-quarters of it. Fixed below by tracking
  // the PREVIOUS `confirming` value (same shape as `prevLinkToken`) and
  // covering every direction: Cancel returns focus to whichever trigger
  // button opened the panel (Nielsen: return focus to its origin);
  // a token change while a link ALREADY existed (rotation, not first
  // creation) still moves focus to the URL input; a link disappearing
  // (revoke success) moves focus to the "Create link" button that
  // replaces the whole form. A `role="status"` announcement (mirroring
  // the existing copy-success one) names what happened for a
  // screen-reader user who can't see the URL/summary line change.
  useEffect(() => {
    if (confirming !== null) {
      confirmPanelRef.current?.focus();
    } else if (cancelledRef.current) {
      // Cancel only -- a SUCCESSFUL regenerate/revoke also sets
      // `confirming` back to null, but that path's own focus move (URL
      // input or "Create link") is owned by the effect below, which
      // reacts to the actual data change rather than to this transient
      // UI state -- without this guard the two would race on every
      // success, and whichever effect happened to run second would win
      // arbitrarily.
      if (prevConfirming.current === "regenerate") regenerateButtonRef.current?.focus();
      else if (prevConfirming.current === "revoke") revokeButtonRef.current?.focus();
      cancelledRef.current = false;
    }
    prevConfirming.current = confirming;
  }, [confirming]);
  useEffect(() => {
    const token = link?.token ?? null;
    const hadLinkBefore = prevLinkToken.current !== null;
    if (token !== null && token !== prevLinkToken.current) {
      urlInputRef.current?.focus();
      setAnnouncement(hadLinkBefore ? "New link created." : "Link created.");
    } else if (token === null && hadLinkBefore) {
      createButtonRef.current?.focus();
      setAnnouncement("Link revoked.");
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
    create.mutate({ expires_at: expiryFromSelection(expirySelection, null) });
  }

  function handleCancelConfirm() {
    cancelledRef.current = true;
    setConfirming(null);
  }

  function handleConfirmRevoke() {
    revoke.mutate(undefined, { onSuccess: () => setConfirming(null) });
  }

  function handleConfirmRegenerate() {
    create.mutate(
      { expires_at: expiryFromSelection(expirySelection, link?.expires_at ?? null) },
      { onSuccess: () => setConfirming(null) },
    );
  }

  // Narrowed once, here, instead of re-deriving/re-casting at each of
  // the three render sites that need "the pending value, not 'current'."
  const pendingExpiry: Exclude<ExpirySelection, "current"> | null =
    link && expirySelection !== "current" ? expirySelection : null;

  const serverError =
    activeError instanceof ApiError ? activeError.message : activeError ? "Could not update this link. Try again." : null;

  return (
    <ModalBackdrop>
      <Modal title="Share this report" onClose={onClose} size="md">
        <div className="flex flex-col gap-3 text-sm">
          {/* ux-critic finding (P1, this session): a screen-reader
              instructor who revokes or regenerates got total silence --
              no focus move (fixed above) and no announcement. Kept
              OUTSIDE the conditionally-swapped branches below (a stable
              node across the create/exists DOM swap) so the live region
              doesn't itself get unmounted the moment the thing it's
              announcing happens. */}
          <p aria-live="polite" className="sr-only">
            {announcement}
          </p>
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
                  <ExpirySelect
                    id="share-expiry-create"
                    label="Link expires"
                    value={expirySelection}
                    onChange={setExpirySelection}
                    options={[
                      { value: "none", label: "No expiry" },
                      { value: "7d", label: "7 days" },
                      { value: "30d", label: "30 days" },
                      { value: "90d", label: "90 days" },
                    ]}
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
                  <button
                    ref={createButtonRef}
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
                    id="share-expiry-regenerate"
                    label="New link's expiry"
                    value={expirySelection}
                    onChange={setExpirySelection}
                    describedById={pendingExpiry ? "share-expiry-pending" : undefined}
                    options={[
                      {
                        value: "current",
                        label: link.expires_at
                          ? `Keep current (expires ${dateFormatter.format(new Date(link.expires_at))})`
                          : "Keep current (no expiry)",
                      },
                      { value: "none", label: "No expiry" },
                      { value: "7d", label: "7 days" },
                      { value: "30d", label: "30 days" },
                      { value: "90d", label: "90 days" },
                    ]}
                  />
                  {/* BUG-090: the summary line above (`Created … Expires
                      …`/`No expiry set.`) is derived ONLY from `link.
                      expires_at`, never from `expirySelection` -- it
                      cannot say something the server hasn't done. This
                      notice is the only place a pending, un-applied
                      choice is ever shown, and it appears the instant
                      the selection diverges from "current" (never a
                      silent no-op the way the old caption was). */}
                  {pendingExpiry && (
                    <p
                      id="share-expiry-pending"
                      role="status"
                      className="rounded-md bg-status-info-bg px-3 py-2.5 text-sm text-status-info-text"
                    >
                      Not yet applied. Click Regenerate link below to issue a new link that{" "}
                      {pendingExpiry && PENDING_DESCRIPTION[pendingExpiry]}. The
                      link above will stop working as soon as you do.
                    </p>
                  )}

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
                        ref={regenerateButtonRef}
                        type="button"
                        onClick={() => setConfirming("regenerate")}
                        // BUG-090: three redundant signals for one fact
                        // when a change is pending (WCAG 1.4.1, color is
                        // never the only carrier) -- this label, this
                        // color, and the notice paragraph above all say
                        // the same thing.
                        className={pendingExpiry ? primaryButtonClass : secondaryButtonClass}
                      >
                        {pendingExpiry
                          ? `Regenerate link (${OPTION_LABEL[pendingExpiry]})`
                          : "Regenerate link"}
                      </button>
                      <button
                        ref={revokeButtonRef}
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
                          onClick={handleCancelConfirm}
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
                        {pendingExpiry
                          ? `Replace this link with a new one that ${PENDING_DESCRIPTION[pendingExpiry]}? The current link stops working immediately once you do.`
                          : "Replace this link with a new one? The current link stops working immediately once you do."}
                      </p>
                      <div className="flex justify-end gap-2">
                        <button
                          type="button"
                          onClick={handleCancelConfirm}
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
