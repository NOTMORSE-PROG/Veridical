// Screen 4u — Settings (V-042): instructor profile, password change, the
// quota meter's full detail, and a plain-facts transparency block (scoring
// thresholds, escalation threshold, which prompt/model versions actually
// ran). The transparency section deliberately reads as raw, independently
// checkable facts, never a trust-building narrative -- same overreliance
// lens DESIGN.md §1.7 already applies to withholding polish from any
// unresolved-AI-judgment surface, extended here since this section's whole
// purpose is "check this yourself."
import {
  type FormEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";
import { useQuota } from "../api/useQuota";
import { ApiError } from "../api/client";
import type { ModelQuotaStatus } from "../api/types";
import { StatusPill } from "../components/StatusPill";
import { cx } from "../components/cx";
import { useChangePassword, useMe } from "../auth/useAuth";
import { quotaRemaining } from "../domain/quotaTone";
import { useRouteFocus } from "../routing/useRouteFocus";
import { PASSWORD_MIN_LENGTH } from "../config/ui";
import { useDeleteApiKey, useSetApiKey, useSettings } from "./useSettings";

function SectionCard({
  headingId,
  title,
  children,
}: {
  headingId: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-labelledby={headingId}
      className="signal-settings-section rounded-lg border border-border bg-panel p-4 shadow-sm sm:p-5"
    >
      <h2 id={headingId} tabIndex={-1} className="text-md mb-3 font-bold text-ink">
        {title}
      </h2>
      {children}
    </section>
  );
}

function focusSettingsSection(event: ReactMouseEvent<HTMLAnchorElement>) {
  event.preventDefault();
  const targetId = event.currentTarget.hash.slice(1);
  const target = document.getElementById(targetId);
  if (!target) return;

  window.history.pushState(null, "", `#${targetId}`);
  target.scrollIntoView?.({ block: "start" });
  target.focus({ preventScroll: true });
}

function ProfileSection() {
  const { data: me } = useMe();
  if (!me) return null;
  return (
    <SectionCard headingId="profile-heading" title="Profile">
      <dl className="flex flex-col gap-2 text-sm">
        <div>
          <dt className="text-ink-tertiary">Display name</dt>
          <dd className="text-ink">{me.display_name}</dd>
        </div>
        <div>
          <dt className="text-ink-tertiary">Email</dt>
          <dd className="text-ink">{me.email}</dd>
        </div>
      </dl>
      <p className="mt-3 text-xs text-ink-tertiary">
        Neither is editable yet. Contact your administrator if either needs to change.
      </p>
    </SectionCard>
  );
}

function PasswordSection() {
  const change = useChangePassword();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [attempted, setAttempted] = useState(false);
  const [success, setSuccess] = useState(false);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const currentRef = useRef<HTMLInputElement>(null);
  const nextRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (change.isError) errorRef.current?.focus();
  }, [change.isError]);

  const currentMissing = attempted && !current;
  const nextTooShort = attempted && next.length < PASSWORD_MIN_LENGTH;
  const confirmMismatch = attempted && !nextTooShort && confirm !== next;

  function resetOnEdit() {
    if (attempted) setAttempted(false);
    if (success) setSuccess(false);
    if (change.isError) change.reset();
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setAttempted(true);
    setSuccess(false);
    // ux-critic finding (P1): only the current-password branch used to
    // move focus on an invalid submit -- the other two set `aria-invalid`/
    // `aria-describedby` via re-render but never moved focus there, so a
    // screen-reader user whose focus stayed on the Submit button never
    // heard the failure at all (the description is only announced once
    // focus actually lands on the field it describes). All three branches
    // now match `ReopenModal`'s own established focus-the-invalid-field
    // convention.
    if (!current) {
      currentRef.current?.focus();
      return;
    }
    if (next.length < PASSWORD_MIN_LENGTH) {
      nextRef.current?.focus();
      return;
    }
    if (confirm !== next) {
      confirmRef.current?.focus();
      return;
    }
    change.mutate(
      { current_password: current, new_password: next },
      {
        onSuccess: () => {
          setCurrent("");
          setNext("");
          setConfirm("");
          setAttempted(false);
          setSuccess(true);
        },
      },
    );
  }

  const serverError =
    change.error instanceof ApiError
      ? change.error.message
      : change.error
        ? "Could not change your password. Try again."
        : null;

  return (
    <SectionCard headingId="password-heading" title="Change password">
      <p role="status" aria-live="polite" className="sr-only">
        {success ? "Password changed." : ""}
      </p>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-sm font-medium text-ink">Current password</span>
          <input
            ref={currentRef}
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => {
              setCurrent(e.target.value);
              resetOnEdit();
            }}
            aria-invalid={currentMissing ? "true" : undefined}
            aria-describedby={currentMissing ? "current-password-err" : undefined}
            className={cx(
              "h-11 rounded-md border px-3 text-base text-ink",
              currentMissing ? "border-2 border-status-attention-text" : "border-border-input",
            )}
          />
        </label>
        {currentMissing && (
          <p id="current-password-err" className="text-sm text-status-attention-text">
            Enter your current password.
          </p>
        )}

        <label className="flex flex-col gap-1">
          <span className="text-sm font-medium text-ink">New password</span>
          <input
            ref={nextRef}
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => {
              setNext(e.target.value);
              resetOnEdit();
            }}
            aria-invalid={nextTooShort ? "true" : undefined}
            aria-describedby={nextTooShort ? "new-password-err" : undefined}
            className={cx(
              "h-11 rounded-md border px-3 text-base text-ink",
              nextTooShort ? "border-2 border-status-attention-text" : "border-border-input",
            )}
          />
        </label>
        {nextTooShort && (
          <p id="new-password-err" className="text-sm text-status-attention-text">
            Use at least {PASSWORD_MIN_LENGTH} characters.
          </p>
        )}

        <label className="flex flex-col gap-1">
          <span className="text-sm font-medium text-ink">Confirm new password</span>
          <input
            ref={confirmRef}
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => {
              setConfirm(e.target.value);
              resetOnEdit();
            }}
            aria-invalid={confirmMismatch ? "true" : undefined}
            aria-describedby={confirmMismatch ? "confirm-password-err" : undefined}
            className={cx(
              "h-11 rounded-md border px-3 text-base text-ink",
              confirmMismatch ? "border-2 border-status-attention-text" : "border-border-input",
            )}
          />
        </label>
        {confirmMismatch && (
          <p id="confirm-password-err" className="text-sm text-status-attention-text">
            This doesn't match the new password above.
          </p>
        )}

        {serverError && (
          <p ref={errorRef} role="alert" tabIndex={-1} className="text-sm font-medium text-danger">
            <span className="sr-only">Error: </span>
            {serverError}
          </p>
        )}
        {success && (
          <p className="text-sm font-medium text-status-success-text">Password changed.</p>
        )}

        <button
          type="submit"
          disabled={change.isPending}
          aria-busy={change.isPending ? "true" : undefined}
          className="flex h-11 items-center justify-center self-start rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-60"
        >
          {change.isPending ? "Changing password." : "Change password"}
        </button>
      </form>
    </SectionCard>
  );
}

// V-052 (BYOK): a free-standing section, not folded into PasswordSection --
// this is optional/reversible AI-capacity configuration, not a credential
// gate on the account itself. Four states in priority order: loading,
// load-error, unavailable-on-this-deployment (disabled, never hidden --
// charter rule 9), and configured-vs-not (form vs. status+remove).
function ApiKeySection() {
  const { data, isPending, isError, refetch } = useSettings();
  const setKey = useSetApiKey();
  const deleteKey = useDeleteApiKey();
  const [value, setValue] = useState("");
  const [attempted, setAttempted] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const errorRef = useRef<HTMLParagraphElement>(null);
  const keyInputRef = useRef<HTMLInputElement>(null);
  const configuredRef = useRef<HTMLParagraphElement>(null);
  const prevHasOwnKey = useRef<boolean | null>(null);

  const activeError = setKey.error ?? deleteKey.error;
  useEffect(() => {
    if (activeError) errorRef.current?.focus();
  }, [activeError]);

  // Same in-place-content-swap focus-move convention as ShareModal.tsx's
  // `prevLinkToken` -- Modal.tsx's own focus-trap only runs on mount, and
  // this section isn't a modal at all, but the underlying bug class
  // (content swaps in place, nothing claims focus) applies identically.
  useEffect(() => {
    const has = data?.api_key.has_own_api_key ?? null;
    if (has !== null && prevHasOwnKey.current !== null && has !== prevHasOwnKey.current) {
      if (has) configuredRef.current?.focus();
      else keyInputRef.current?.focus();
    }
    prevHasOwnKey.current = has;
  }, [data?.api_key.has_own_api_key]);

  if (isPending) {
    return (
      <SectionCard headingId="api-key-heading" title="Personal Gemini key">
        <div role="status" aria-live="polite" aria-busy="true" className="text-sm text-ink-tertiary">
          Loading your API key settings…
        </div>
      </SectionCard>
    );
  }
  if (isError || !data) {
    return (
      <SectionCard headingId="api-key-heading" title="Personal Gemini key">
        <div
          role="alert"
          className="rounded-md border border-status-attention-text/25 bg-status-attention-bg p-3 text-sm text-status-attention-text"
        >
          Could not load your API key settings.{" "}
          <button type="button" onClick={() => refetch()} className="underline">
            Try again
          </button>
          .
        </div>
      </SectionCard>
    );
  }

  const { byok_available, has_own_api_key } = data.api_key;

  if (!byok_available) {
    return (
      <SectionCard headingId="api-key-heading" title="Personal Gemini key">
        <p className="text-sm text-ink-secondary">
          VERIDICAL currently shares one free Gemini key across every instructor, so the AI
          capacity everyone sees is split between all of you. Bringing your own key lets your AI
          grading draw from your own quota instead.
        </p>
        <p className="mt-3 rounded-md border border-status-attention-text/25 bg-status-attention-bg p-3 text-sm text-status-attention-text">
          Bringing your own API key isn't available on this deployment yet. Ask your
          administrator to enable it. Until then, VERIDICAL grades using the shared key.
        </p>
      </SectionCard>
    );
  }

  const serverError =
    activeError instanceof ApiError
      ? activeError.message
      : activeError
        ? has_own_api_key
          ? "Could not remove your API key. Try again."
          : "Could not save your API key. Try again."
        : null;

  function resetOnEdit() {
    if (attempted) setAttempted(false);
    if (setKey.isError) setKey.reset();
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setAttempted(true);
    if (!value.trim()) {
      keyInputRef.current?.focus();
      return;
    }
    setKey.mutate(
      { api_key: value },
      {
        onSuccess: () => {
          setValue("");
          setAnnouncement("Your own API key is saved and active.");
        },
      },
    );
  }

  function handleDelete() {
    deleteKey.mutate(undefined, {
      onSuccess: () => setAnnouncement("Removed. AI grading now uses the shared key."),
    });
  }

  const emptyOnSubmit = attempted && !value.trim();

  if (has_own_api_key) {
    return (
      <SectionCard headingId="api-key-heading" title="Personal Gemini key">
        <p ref={configuredRef} tabIndex={-1} className="text-sm text-ink">
          Your own Gemini API key is active. AI grading now uses your quota, not the shared pool.
        </p>
        <p role="status" aria-live="polite" className="sr-only">
          {announcement}
        </p>

        {serverError && (
          <p
            ref={errorRef}
            role="alert"
            tabIndex={-1}
            className="mt-2 text-sm font-medium text-danger"
          >
            <span className="sr-only">Error: </span>
            {serverError}
          </p>
        )}

        <button
          type="button"
          onClick={handleDelete}
          disabled={deleteKey.isPending}
          aria-busy={deleteKey.isPending ? "true" : undefined}
          className="mt-3 flex h-11 items-center justify-center self-start rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-60"
        >
          {deleteKey.isPending ? "Removing." : "Remove key"}
        </button>
        <p className="mt-1.5 text-xs text-ink-tertiary">
          Removing switches AI grading back to the shared key immediately. You can add your key
          again any time.
        </p>
      </SectionCard>
    );
  }

  return (
    <SectionCard headingId="api-key-heading" title="Personal Gemini key">
      <p className="text-sm text-ink-secondary">
        VERIDICAL currently shares one free Gemini key across every instructor, so the AI
        capacity everyone sees is split between all of you. Add your own free Gemini API key and
        your AI grading draws from your own quota instead, so your runs never compete with
        anyone else's for capacity.
      </p>
      <p className="mt-1 text-sm text-ink-secondary">
        This is optional. VERIDICAL grades manuscripts using the shared key whether or not you
        add your own, and you can remove your key at any time.
      </p>
      <a
        href="https://aistudio.google.com/app/apikey"
        target="_blank"
        rel="noopener noreferrer"
        className="mt-2 inline-flex min-h-11 items-center gap-1 text-sm font-medium text-link underline hover:text-link-hover"
      >
        Get a free key from Google AI Studio
        <span className="sr-only"> (opens in a new tab)</span>
      </a>

      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>

      <form onSubmit={handleSubmit} className="mt-3 flex flex-col gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-sm font-medium text-ink">Gemini API key</span>
          <input
            ref={keyInputRef}
            type="password"
            autoComplete="off"
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              resetOnEdit();
            }}
            aria-invalid={emptyOnSubmit ? "true" : undefined}
            aria-describedby={emptyOnSubmit ? "api-key-err" : undefined}
            className={cx(
              "h-11 rounded-md border px-3 text-base text-ink",
              emptyOnSubmit ? "border-2 border-status-attention-text" : "border-border-input",
            )}
          />
        </label>
        {emptyOnSubmit && (
          <p id="api-key-err" className="text-sm text-status-attention-text">
            Paste your Gemini API key.
          </p>
        )}

        {serverError && (
          <p ref={errorRef} role="alert" tabIndex={-1} className="text-sm font-medium text-danger">
            <span className="sr-only">Error: </span>
            {serverError}
          </p>
        )}

        <button
          type="submit"
          disabled={setKey.isPending}
          aria-busy={setKey.isPending ? "true" : undefined}
          className="flex h-11 items-center justify-center self-start rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-60"
        >
          {setKey.isPending ? "Validating key." : "Save key"}
        </button>
      </form>
    </SectionCard>
  );
}

const quotaGridCols = "grid-cols-[minmax(0,1fr)_110px_110px_110px_80px_120px]";

function ModelRow({ model }: { model: ModelQuotaStatus }) {
  return (
    <>
      <span className="truncate text-ink" title={model.model}>
        {model.model}
      </span>
      <span className="text-ink-tertiary">
        {model.calls_used} / {model.daily_limit}
      </span>
      <span className="text-ink-tertiary">{model.calls_remaining}</span>
      <span className="text-ink-tertiary">{model.cache_hits_today}</span>
      <span className="text-ink-tertiary">{model.vision ? "Yes" : "No"}</span>
      {model.exhausted && <StatusPill tone="attention">Exhausted</StatusPill>}
    </>
  );
}

function QuotaSection() {
  const { data: quota, isPending, isError, refetch } = useQuota();

  if (isPending) {
    return (
      <SectionCard headingId="quota-heading" title="AI capacity">
        <div role="status" aria-live="polite" aria-busy="true" className="text-sm text-ink-tertiary">
          Loading AI capacity…
        </div>
      </SectionCard>
    );
  }
  if (isError || !quota) {
    return (
      <SectionCard headingId="quota-heading" title="AI capacity">
        <div
          role="alert"
          className="rounded-md border border-status-attention-text/25 bg-status-attention-bg p-3 text-sm text-status-attention-text"
        >
          Could not load AI capacity right now.{" "}
          <button type="button" onClick={() => refetch()} className="underline">
            Try again
          </button>
          .
        </div>
      </SectionCard>
    );
  }

  const { remainingPct, tone } = quotaRemaining(quota.calls_used, quota.daily_limit);
  const fillColor = `var(--color-status-${tone}-text)`;

  return (
    <SectionCard headingId="quota-heading" title="AI capacity">
      <div className="flex flex-col gap-2">
        {quota.mode === "live" && (
          <StatusPill tone={quota.key_source === "own" ? "success" : "neutral"}>
            {quota.key_source === "own" ? "Using your own key" : "Using the shared key"}
          </StatusPill>
        )}
        {quota.mode === "fake" && (
          <p className="text-xs font-medium text-status-caution-text">
            Running in test mode. These numbers are simulated, not real API usage.
          </p>
        )}
        <div
          aria-hidden="true"
          className="h-2 w-full max-w-xs overflow-hidden rounded-full"
          style={{ backgroundColor: "var(--color-neutral-150)" }}
        >
          <span
            className="block h-full rounded-full"
            style={{ width: `${remainingPct}%`, backgroundColor: fillColor }}
          />
        </div>
        <p className="text-sm text-ink">
          {quota.calls_used} of {quota.daily_limit} AI calls used today ({remainingPct}% left)
        </p>
        <p className="text-sm text-ink-tertiary">
          Resets {new Date(quota.reset_at).toLocaleString()}
        </p>
        <p className="text-sm text-ink-tertiary">
          {Math.round(quota.cache_hit_rate * 100)}% of today's calls were served from cache
        </p>
      </div>

      {quota.models && quota.models.length > 0 && (
        <div className="mt-4">
          <ul className="flex flex-col gap-2 lg:hidden">
            {quota.models.map((model) => (
              <li key={model.model} className="rounded-lg border border-border p-3 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="min-w-0 truncate font-medium text-ink" title={model.model}>
                    {model.model}
                  </span>
                  {model.exhausted && <StatusPill tone="attention">Exhausted</StatusPill>}
                </div>
                <p className="mt-1 text-xs text-ink-tertiary">
                  {model.calls_used} / {model.daily_limit} used · {model.calls_remaining} left ·{" "}
                  {model.cache_hits_today} cache hits · {model.vision ? "vision" : "no vision"}
                </p>
              </li>
            ))}
          </ul>
          <div className="hidden overflow-hidden rounded-lg border border-border lg:block">
            <div
              role="row"
              className={`grid ${quotaGridCols} gap-3 border-b border-border bg-status-neutral-bg px-3 py-2 text-xs font-semibold tracking-header text-ink-tertiary uppercase`}
            >
              <span role="columnheader">Model</span>
              <span role="columnheader">Used / Limit</span>
              <span role="columnheader">Remaining</span>
              <span role="columnheader">Cache hits</span>
              <span role="columnheader">Vision</span>
              <span role="columnheader">Status</span>
            </div>
            {quota.models.map((model) => (
              <div
                key={model.model}
                role="row"
                className={`grid ${quotaGridCols} items-center gap-3 border-t border-border px-3 py-2.5 text-sm`}
              >
                <ModelRow model={model} />
              </div>
            ))}
          </div>
        </div>
      )}
    </SectionCard>
  );
}

function TransparencySection() {
  const { data, isLoading, isError, refetch } = useSettings();

  if (isLoading) {
    return (
      <SectionCard headingId="transparency-heading" title="Review rules and technical record">
        <div role="status" aria-live="polite" aria-busy="true" className="text-sm text-ink-tertiary">
          Loading scoring configuration…
        </div>
      </SectionCard>
    );
  }
  if (isError || !data) {
    return (
      <SectionCard headingId="transparency-heading" title="Review rules and technical record">
        <div
          role="alert"
          className="rounded-md border border-status-attention-text/25 bg-status-attention-bg p-3 text-sm text-status-attention-text"
        >
          Could not load scoring configuration.{" "}
          <button type="button" onClick={() => refetch()} className="underline">
            Try again
          </button>
          .
        </div>
      </SectionCard>
    );
  }

  const { thresholds, prompt_versions } = data;
  return (
    <SectionCard headingId="transparency-heading" title="Review rules and technical record">
      <div className="flex flex-col gap-4 text-sm">
        <div>
          <h3 className="font-semibold text-ink">Readiness bands</h3>
          <ul className="mt-1 list-disc pl-5 text-ink-secondary">
            <li>Ready: the recorded evidence supports proceeding to instructor decision.</li>
            <li>Conditionally Ready: material revisions or instructor review remain.</li>
            <li>Not Ready: the recorded criteria show substantial unresolved gaps.</li>
            <li>Needs Review: VERIDICAL could not settle one or more criteria.</li>
          </ul>
          {!thresholds.editable && (
            <p className="mt-2 text-xs text-ink-tertiary">
              These thresholds are fixed for every instructor right now. Instructor-specific
              thresholds are a planned feature, held back until they can be reviewed. Letting
              readiness cutoffs vary silently between sections is exactly the kind of
              inconsistency a defense panel would ask about.
            </p>
          )}
        </div>

        <div>
          <h3 className="font-semibold text-ink">Instructor escalation</h3>
          <p className="mt-1 text-ink-secondary">
            A criterion comes to you when VERIDICAL cannot record a sufficiently reliable outcome.
            Unavailable grading is not treated as passed.
          </p>
        </div>

        <details className="signal-settings-technical">
          <summary>Technical calculation record</summary>
          <p>These values are retained for reproducibility. They are calculation records, not the readiness wording shown as the instructor's judgment.</p>
          <dl>
            <div><dt>Ready composite cutoff</dt><dd>{thresholds.ready_min_score}</dd></div>
            <div><dt>Not Ready composite cutoff</dt><dd>{thresholds.not_ready_max_score}</dd></div>
            <div><dt>Escalation agreement cutoff</dt><dd>{thresholds.escalation_agreement_threshold.toFixed(2)}</dd></div>
          </dl>
        </details>

        <div>
          <h3 className="font-semibold text-ink">AI models and prompts in use</h3>
          <p className="mt-1 text-xs text-ink-tertiary">
            Read from your account's most recent AI calls, not a fixed list. If a model or prompt
            changes, this updates automatically.
          </p>
          {prompt_versions.length === 0 ? (
            <p className="mt-2 text-ink-secondary">
              No AI grading calls have been recorded yet. This fills in automatically once
              VERIDICAL grades a manuscript.
            </p>
          ) : (
            <ul className="mt-2 flex flex-col gap-2">
              {prompt_versions.map((p) => (
                <li key={p.prompt_type} className="rounded-md border border-border p-2.5 text-xs">
                  <span className="font-medium text-ink">{p.prompt_type}</span>
                  <span className="text-ink-tertiary">
                    {" "}
                    · {p.model} · prompt v{p.prompt_version.replace(/^v/, "")} · last used{" "}
                    {new Date(p.observed_at).toLocaleString()}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </SectionCard>
  );
}

export function SettingsPage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Settings - VERIDICAL", headingRef);

  return (
    <div className="signal-route signal-page-flow signal-settings">
      <header className="signal-route-header">
        <div><p className="signal-eyebrow">Workspace utility</p><h1 ref={headingRef} tabIndex={-1}>Workspace settings</h1><p className="signal-route-header__intro">Manage account access, AI capacity, review rules, and technical records.</p></div>
      </header>
      <nav aria-label="Settings sections" className="signal-settings-nav"><a href="#profile-heading" onClick={focusSettingsSection}>Account</a><a href="#api-key-heading" onClick={focusSettingsSection}>Personal key</a><a href="#quota-heading" onClick={focusSettingsSection}>AI capacity</a><a href="#transparency-heading" onClick={focusSettingsSection}>Review rules</a></nav>
      <div className="signal-settings-stack signal-page-flow">
        <ProfileSection />
        <PasswordSection />
        <ApiKeySection />
        <QuotaSection />
        <TransparencySection />
      </div>
    </div>
  );
}
