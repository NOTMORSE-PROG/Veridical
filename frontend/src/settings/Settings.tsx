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
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useQuota } from "../api/useQuota";
import { ApiError } from "../api/client";
import type { ModelQuotaStatus } from "../api/types";
import { StatusPill } from "../components/StatusPill";
import { cx } from "../components/cx";
import { useChangePassword, useMe } from "../auth/useAuth";
import { useAuthOwner } from "../auth/queryOwner";
import { quotaRemaining } from "../domain/quotaTone";
import { useRouteFocus } from "../routing/useRouteFocus";
import { PASSWORD_MIN_LENGTH } from "../config/ui";
import { useDeleteApiKey, useSetApiKey, useSettings } from "./useSettings";

function SectionCard({
  headingId,
  title,
  children,
  budget = false,
}: {
  headingId: string;
  title: string;
  children: ReactNode;
  budget?: boolean;
}) {
  return (
    <section
      aria-labelledby={headingId}
      className={cx("signal-settings-section rounded-lg border border-border bg-panel p-4 shadow-sm sm:p-5", budget && "signal-settings-section--budget")}
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
    // UX review finding (P1): only the current-password branch used to
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
  const owner = useAuthOwner();
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

  const { reset: resetSave } = setKey;
  const { reset: resetDelete } = deleteKey;
  // Private drafts and acknowledgments belong to one authentication lifetime.
  useLayoutEffect(() => {
    setValue("");
    setAttempted(false);
    setAnnouncement("");
    prevHasOwnKey.current = null;
    resetSave();
    resetDelete();
  }, [owner.authQuery, owner.actorId, resetSave, resetDelete]);

  const saving = setKey.isPending && setKey.variables?.owner.isCurrent();
  const removing = deleteKey.isPending && deleteKey.variables?.owner.isCurrent();
  const activeError = (setKey.variables?.owner.isCurrent() ? setKey.error : null)
    ?? (deleteKey.variables?.owner.isCurrent() ? deleteKey.error : null);
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
          A personal Gemini key can be used for future AI requests when this deployment supports it. Adding a key does not guarantee additional capacity. Available capacity depends on the provider's project limits.
        </p>
        <p className="mt-3 rounded-md border border-status-attention-text/25 bg-status-attention-bg p-3 text-sm text-status-attention-text">
          Personal API keys are not available on this deployment. You can still review available results and record your own judgments. Ask the deployment administrator if AI configuration is needed.
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
        onSuccess: (_settings, request) => {
          if (!request.owner.isCurrent()) return;
          setValue("");
          setAnnouncement("Your personal key was saved. Existing checks have not been re-run.");
        },
      },
    );
  }

  function handleDelete() {
    deleteKey.mutate(undefined, {
      onSuccess: (_settings, request) => {
        if (!request.owner.isCurrent()) return;
        setAnnouncement("Your personal key was removed. Shared AI configuration can be used if it is available. Existing checks have not been re-run.");
      },
    });
  }

  const emptyOnSubmit = attempted && !value.trim();

  if (has_own_api_key) {
    return (
      <SectionCard headingId="api-key-heading" title="Personal Gemini key">
        <p ref={configuredRef} tabIndex={-1} className="text-sm text-ink">
          A personal Gemini key is saved for this account. Future AI requests can use it when it can be read and is accepted by the service. Available capacity depends on the provider's project limits.
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
          disabled={removing}
          aria-busy={removing ? "true" : undefined}
          className="mt-3 flex h-11 items-center justify-center self-start rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-60"
        >
          {removing ? "Removing." : "Remove key"}
        </button>
        <p className="mt-1.5 text-xs text-ink-tertiary">
          Removing your key removes its saved configuration from your account. It does not cancel checks that are already running or re-run existing checks. Shared AI configuration can be used if it is available. You can add a personal key again later.
        </p>
      </SectionCard>
    );
  }

  return (
    <SectionCard headingId="api-key-heading" title="Personal Gemini key">
      <p className="text-sm text-ink-secondary">
        You can save a personal Gemini key for future AI requests. Adding a key does not guarantee additional capacity. Available capacity depends on the provider's project limits.
      </p>
      <p className="mt-1 text-sm text-ink-secondary">
        Adding a key is optional. Shared AI configuration can be used if it is available. You can review available results and record your own judgments without adding a key. Saving or removing a key does not re-run existing checks.
      </p>
      <a
        href="https://aistudio.google.com/app/apikey"
        target="_blank"
        rel="noopener noreferrer"
        className="mt-2 inline-flex min-h-11 items-center gap-1 text-sm font-medium text-link underline hover:text-link-hover"
      >
        Open Google AI Studio
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
          disabled={saving}
          aria-busy={saving ? "true" : undefined}
          className="flex h-11 items-center justify-center self-start rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-60"
        >
          {saving ? "Validating key." : "Save key"}
        </button>
      </form>
    </SectionCard>
  );
}

type AISetup = "configured" | "unavailable" | "unknown" | "test";

function ModelLocalBudgetPill({ model }: { model: ModelQuotaStatus }) {
  return <span className="signal-model-local-status"><StatusPill tone={model.exhausted ? "attention" : "neutral"}>{model.exhausted ? "Limit reached" : "Below limit"}</StatusPill></span>;
}

function ModelRow({ model }: { model: ModelQuotaStatus }) {
  return (
    <tr>
      <th scope="row">{model.model}</th>
      <td><ModelLocalBudgetPill model={model} /></td>
      <td className="signal-model-capacity-number">{model.recorded_budget_debits ?? model.calls_used} / {model.daily_limit}</td>
      <td className="signal-model-capacity-number">{model.calls_remaining}</td>
      <td className="signal-model-capacity-number">{model.cache_hits_today}</td>
      <td>{model.vision ? "Yes" : "No"}</td>
    </tr>
  );
}

function ModelCapacityTable({ models }: { models: ModelQuotaStatus[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [overflows, setOverflows] = useState(false);
  const helpId = useId();

  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    const measure = () => setOverflows(container.scrollWidth > container.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    if (container.firstElementChild) observer.observe(container.firstElementChild);
    return () => observer.disconnect();
  }, [models]);

  return (
    <div className="hidden lg:block">
      <div
        ref={scrollRef}
        role="region"
        aria-label="Configured models and local budget"
        aria-describedby={overflows ? helpId : undefined}
        tabIndex={overflows ? 0 : undefined}
        className="signal-model-capacity-scroll"
      >
        <table className="signal-model-capacity-table">
          <caption>Configured models and local budget</caption>
          <thead><tr>
            <th scope="col">Requested model</th>
            <th scope="col">Local budget status</th>
            <th scope="col" className="signal-model-capacity-number">Recorded budget / Configured limit</th>
            <th scope="col" className="signal-model-capacity-number">Local budget remaining</th>
            <th scope="col" className="signal-model-capacity-number">Cached responses reused</th>
            <th scope="col">Image requests enabled</th>
          </tr></thead>
          <tbody>{models.map((model) => <ModelRow key={model.model} model={model} />)}</tbody>
        </table>
      </div>
      {overflows && <p id={helpId} className="mt-2 text-xs text-ink-secondary">Scroll across to see all model details.</p>}
    </div>
  );
}

function QuotaSection() {
  const { data: quota, isPending, isError, refetch } = useQuota();

  if (isPending) {
    return (
      <SectionCard headingId="quota-heading" title="Local AI budget" budget>
        <div role="status" aria-live="polite" aria-busy="true" className="text-sm text-ink-tertiary">
          Loading local AI budget...
        </div>
      </SectionCard>
    );
  }
  if (isError || !quota) {
    return (
      <SectionCard headingId="quota-heading" title="Local AI budget" budget>
        <div
          role="alert"
          className="rounded-md border border-status-attention-text/25 bg-status-attention-bg p-3 text-sm text-status-attention-text"
        >
          Could not load the local AI budget right now.{" "}
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
  const setup: AISetup = quota.mode === "fake" ? "test" : quota.key_state === "configured"
    ? "configured" : quota.key_state === "unavailable" ? "unavailable" : "unknown";

  return (
    <SectionCard headingId="quota-heading" title="Local AI budget" budget>
      <div className="signal-settings-budget-copy flex flex-col gap-2">
        {quota.mode === "live" && (
          <StatusPill tone={quota.key_source === "own" ? "success" : "neutral"}>
            {quota.key_source === "own" ? "Quota record: personal key" : "Quota record: shared key"}
          </StatusPill>
        )}
        {quota.mode === "fake" && (
          <p className="text-xs font-medium text-status-caution-text">
            Test mode is active. These budget figures do not represent live provider usage.
          </p>
        )}
        {setup === "unavailable" && <div role="status" aria-live="polite">
          <p>No usable AI configuration was available for this status check. Saved shared-key budget records are shown below.</p>
          <p>Review the Personal key section, or ask the deployment administrator to check shared AI configuration. You can still review available results and record your own judgments.</p>
        </div>}
        {setup === "configured" && <p role="status">AI configuration found. Provider access has not been verified.</p>}
        {setup === "unknown" && <p role="status">AI setup status is unknown. These local budget records do not establish whether AI requests can run.</p>}
        {(setup === "configured" || setup === "test") && <div
          aria-hidden="true"
          className="h-2 w-full max-w-xs overflow-hidden rounded-full"
          style={{ backgroundColor: "var(--color-neutral-150)" }}
        >
          <span
            className="block h-full rounded-full"
            style={{ width: `${remainingPct}%`, backgroundColor: fillColor }}
          />
        </div>}
        <p className="text-sm text-ink">
          {setup === "unavailable" || setup === "unknown" ? "Remaining budget in this local record" : "Remaining local request budget"}: {quota.calls_remaining}
        </p>
        <p>Configured daily limit: {quota.daily_limit}</p>
        <p>Recorded local budget: {quota.recorded_budget_debits ?? quota.calls_used}</p>
        <p>These are configured limits and local budget records. Provider allowance and current availability are not verified here.</p>
        <p>Recorded local budget can include a closed limit after a provider limit response. It is not an exact count of API requests.</p>
        <p className="text-sm text-ink-tertiary">
          Local budget resets {new Date(quota.reset_at).toLocaleString()}
        </p>
        <p className="text-sm text-ink-tertiary">
          Cached responses reused today: {quota.cache_hits_today}
        </p>
      </div>

      {quota.models && quota.models.length > 0 && (
        <div className="mt-4">
          <ul className="flex flex-col gap-2 lg:hidden">
            {quota.models.map((model) => (
              <li key={model.model} className="rounded-lg border border-border p-3 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="min-w-0 wrap-anywhere font-medium text-ink">
                    {model.model}
                  </span>
                  <ModelLocalBudgetPill model={model} />
                </div>
                <dl className="signal-settings-model-metrics">
                  <div><dt>Recorded budget / Configured limit</dt><dd>{model.recorded_budget_debits ?? model.calls_used} / {model.daily_limit}</dd></div>
                  <div><dt>Local budget remaining</dt><dd>{model.calls_remaining}</dd></div>
                  <div><dt>Cached responses reused</dt><dd>{model.cache_hits_today}</dd></div>
                  <div><dt>Image requests enabled</dt><dd>{model.vision ? "Yes" : "No"}</dd></div>
                </dl>
              </li>
            ))}
          </ul>
          <ModelCapacityTable models={quota.models} />
          {quota.models.filter((model) => (model.historical_budget_debits ?? 0) > 0).map((model) => <p key={model.model} className="signal-settings-budget-copy mt-2 text-sm text-ink-secondary wrap-anywhere">
            Requested model {model.model} includes {model.historical_budget_debits} recorded budget debits from earlier identifiers: {model.budget_aliases?.join(", ")}. The remaining local budget includes a conservative reservation for earlier model records. A reservation is a local safety measure, not a count of additional API requests.
          </p>)}
        </div>
      )}
      {quota.models?.length === 0 && <p>No models are currently configured for new requests.</p>}
      {!!quota.legacy_models?.length && <section className="mt-4" aria-label="Earlier model records for this budget day">
        <h3 className="font-semibold">Earlier model records for this budget day</h3>
        <p>These requested model identifiers are no longer used for new requests. Their saved budget and cache records remain available.</p>
        <ul className="mt-2 flex flex-col gap-2">{quota.legacy_models.map((record) => <li key={record.model} className="rounded-md border border-border p-3 text-sm">
          <h4 className="font-medium wrap-anywhere">{record.model}</h4><p>No longer configured</p>
          <p>Recorded local budget: {record.recorded_budget_debits} · Cached responses reused: {record.cache_hits_today}</p>
          {record.budget_model && <p>Included in the local budget for requested model {record.budget_model}.</p>}
        </li>)}</ul>
      </section>}
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
          <h3 className="font-semibold text-ink">Models and prompts recorded for your manuscripts</h3>
          <p className="mt-1 text-xs text-ink-tertiary">
            Latest recorded use for each check type, from manuscripts owned by your account.
            Activity without a manuscript ownership link, including format-upload calls, is not included.
          </p>
          {prompt_versions.length === 0 ? (
            <p className="mt-2 text-ink-secondary">
              No attributable model activity is available for your manuscripts yet.
              This does not mean no AI calls have occurred.
            </p>
          ) : (
            <ul className="mt-2 flex flex-col gap-2">
              {prompt_versions.map((p) => (
                <li key={p.prompt_type} className="rounded-md border border-border p-2.5 text-xs">
                  <span className="font-medium text-ink">{p.prompt_type}</span>
                  <span className="text-ink-tertiary">
                    {" "}
                    · Requested model: {p.requested_model ?? p.model} · prompt v{p.prompt_version.replace(/^v/, "")} · Last recorded{" "}
                    {new Date(p.observed_at).toLocaleString()}
                  </span>
                  <p>{p.response_source === "cache" ? "Provider-reported model version of cached response" : "Provider-reported model version"}: {p.response_source === "test" ? "Not applicable: test-mode response" : p.model_version ?? "Not recorded"}</p>
                  {p.response_source === "cache" && <p>This entry reused a stored response. No new generation is recorded by this event.</p>}
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
        <div><p className="signal-eyebrow">Workspace utility</p><h1 ref={headingRef} tabIndex={-1}>Workspace settings</h1><p className="signal-route-header__intro">Manage account access, local AI budget, review rules, and technical records.</p></div>
      </header>
      <nav aria-label="Settings sections" className="signal-settings-nav"><a href="#profile-heading" onClick={focusSettingsSection}>Account</a><a href="#api-key-heading" onClick={focusSettingsSection}>Personal key</a><a href="#quota-heading" onClick={focusSettingsSection}>Local AI budget</a><a href="#transparency-heading" onClick={focusSettingsSection}>Review rules</a></nav>
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
