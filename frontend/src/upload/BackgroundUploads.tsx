// V-090: large manuscripts upload and prepare in the background, so the
// instructor can keep working instead of waiting on the upload dialog.
import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import { directManuscriptUpload, UploadRefusedError, type UploadStatus } from "../api/directManuscriptUpload";
import { UPLOAD_REFUSAL_COPY } from "../domain/uploadRefusal";
import { followUpload, listActiveUploads, startedCheckId } from "../api/uploadTray";
import { StaleUploadContextError, validateIngestReceipt } from "../api/uploadOutcome";
import { useUploadScope, type UploadAttemptScope } from "../auth/useUploadScope";
import { BACKGROUND_UPLOAD_BUSY_RETRY, BACKGROUND_UPLOAD_DISMISSED_KEPT } from "../config/ui";
import { ActionLink } from "../ui/ActionLink";
import { Button } from "../ui/Button";

export type UploadPhase = "waiting" | "sending" | "preparing" | "done" | "failed";

export interface BackgroundUpload {
  id: string;
  fileName: string;
  size: number;
  phase: UploadPhase;
  percent: number | null;
  // True once the file bytes reached storage; from then on the server keeps
  // preparing even if this tab closes.
  serverOwned: boolean;
  // The server's id for this upload, once it has answered.
  attemptId?: string;
  // The instructor asked for a check to start as soon as the file is ready.
  startsCheck?: boolean;
  manuscriptId?: number;
  checkRunId?: number;
  // The file was ready but the check could not be started; the reason.
  checkStartError?: string;
  // On a refusal: whether the server kept a record of the attempt.
  keptRecord?: boolean;
  error?: string;
}

export interface BackgroundUploadOptions {
  startCheckRubricId?: number;
}

interface BackgroundUploadsValue {
  uploads: BackgroundUpload[];
  // Starting a check on arrival needs the direct upload route.
  canStartCheck: boolean;
  start: (file: File, options?: BackgroundUploadOptions) => void;
  dismiss: (id: string) => void;
}

const BackgroundUploadsContext = createContext<BackgroundUploadsValue | null>(null);

export function useBackgroundUploads() {
  return useContext(BackgroundUploadsContext);
}

const directUpload = () => import.meta.env.VITE_DIRECT_MANUSCRIPT_UPLOAD_ENABLED === "true";
const UNCONFIRMED = "VERIDICAL could not confirm this upload's result. Check the Review Desk for a saved record.";

// Dismissed uploads stay dismissed on this device. Keyed by instructor so a
// shared lab computer never hides one instructor's uploads from another.
const DISMISSED_KEY_PREFIX = "veridical.uploads-dismissed.v1";

function readDismissed(actorId: number): Set<string> {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(`${DISMISSED_KEY_PREFIX}.${actorId}`) ?? "[]");
    return new Set(Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string") : []);
  } catch {
    return new Set();
  }
}

function rememberDismissed(actorId: number, attemptId: string) {
  try {
    // The server only lists the last day's uploads, so a short list is enough.
    const kept = [...new Set([...readDismissed(actorId), attemptId])].slice(-BACKGROUND_UPLOAD_DISMISSED_KEPT);
    window.localStorage.setItem(`${DISMISSED_KEY_PREFIX}.${actorId}`, JSON.stringify(kept));
  } catch {
    // Storage unavailable: the upload may show again after a reload.
  }
}

// What a server status means for a tray row.
function fromStatus(status: UploadStatus): Partial<BackgroundUpload> {
  const checkRunId = startedCheckId(status);
  const checkStartError = status.check_start_error?.trim() || undefined;
  if (status.status === "done") {
    try {
      return { phase: "done", percent: null, serverOwned: true, manuscriptId: validateIngestReceipt(status.result).manuscript_id,
        checkRunId, checkStartError };
    } catch {
      return { phase: "failed", percent: null, error: UNCONFIRMED };
    }
  }
  if (status.status === "failed" || status.status === "expired") {
    return { phase: "failed", percent: null, error: status.error?.trim() || "Document processing did not finish.",
      keptRecord: Number.isSafeInteger(status.manuscript_id) && (status.manuscript_id ?? 0) > 0 };
  }
  return { phase: "preparing", percent: null, serverOwned: true };
}

// The upload dialog sends one file, so each new capture retires the one
// before it. Here several files can be on their way at once, so each one
// stays current for as long as the same instructor is signed in.
function sharedAttempt(base: UploadAttemptScope, scope: { isCurrent: () => boolean; verify: (afterPending?: boolean) => Promise<void> }): UploadAttemptScope {
  return {
    actorId: base.actorId,
    isCurrent: scope.isCurrent,
    async verify() { await scope.verify(true); if (!scope.isCurrent()) throw new StaleUploadContextError(); },
    rejectActor: base.rejectActor,
  };
}

export function BackgroundUploadsProvider({ children }: { children: ReactNode }) {
  const scope = useUploadScope("background-uploads");
  const queryClient = useQueryClient();
  const [uploads, setUploads] = useState<BackgroundUpload[]>([]);
  const files = useRef(new Map<string, { file: File; options: BackgroundUploadOptions }>());
  const restoredFor = useRef<number | null>(null);
  const update = useCallback((id: string, patch: Partial<BackgroundUpload>) =>
    setUploads((current) => current.map((item) => item.id === id ? { ...item, ...patch } : item)), []);

  const settled = useCallback((patch: Partial<BackgroundUpload>) => {
    if (patch.phase !== "done") return;
    void queryClient.invalidateQueries({ queryKey: ["manuscripts"] });
    void queryClient.invalidateQueries({ queryKey: ["stats"] });
    if (patch.checkRunId !== undefined) void queryClient.invalidateQueries({ queryKey: ["check-runs"] });
  }, [queryClient]);

  const run = useCallback(async (id: string, file: File, options: BackgroundUploadOptions, busyAttempt = 0): Promise<void> => {
    const attempt = sharedAttempt(scope.capture(), scope);
    update(id, { phase: "sending", percent: directUpload() ? 0 : null, error: undefined, serverOwned: false });
    try {
      await attempt.verify();
      let last: UploadStatus | undefined;
      const out = directUpload()
        ? await directManuscriptUpload(file, attempt, {
          onProgress: (percent) => update(id, { percent }),
          onPreparing: () => update(id, { phase: "preparing", percent: null, serverOwned: true }),
          onStatus: (status) => { last = status; update(id, { attemptId: status.attempt_id }); },
        }, options.startCheckRubricId === undefined ? {} : { startCheck: { rubricId: options.startCheckRubricId } })
        : await api.post<unknown>("/manuscripts/ingest", (() => { const form = new FormData(); form.append("file", file); return form; })(),
          { "X-Veridical-Expected-Instructor": String(attempt.actorId) });
      const receipt = validateIngestReceipt(out);
      files.current.delete(id);
      const patch: Partial<BackgroundUpload> = { phase: "done", percent: null, serverOwned: true, manuscriptId: receipt.manuscript_id,
        checkRunId: last ? startedCheckId(last) : undefined, checkStartError: last?.check_start_error?.trim() || undefined };
      update(id, patch);
      settled(patch);
    } catch (error) {
      // The server takes one streamed transfer at a time; another upload in
      // progress is a reason to wait, not a failure.
      if (error instanceof ApiError && error.code === "document_preparation_busy" && busyAttempt + 1 < BACKGROUND_UPLOAD_BUSY_RETRY.maxAttempts) {
        update(id, { phase: "waiting", percent: null });
        await new Promise((resolve) => setTimeout(resolve, BACKGROUND_UPLOAD_BUSY_RETRY.delayMs));
        if (files.current.has(id)) return run(id, file, options, busyAttempt + 1);
        return;
      }
      update(id, { phase: "failed", percent: null, keptRecord: error instanceof UploadRefusedError ? error.keptManuscriptId !== null : undefined,
        error: error instanceof ApiError ? error.message : "The upload did not finish. Try again, or check the Review Desk for a saved record." });
    }
  }, [scope, update, settled]);

  const start = useCallback((file: File, options: BackgroundUploadOptions = {}) => {
    const id = crypto.randomUUID();
    files.current.set(id, { file, options });
    setUploads((current) => [{ id, fileName: file.name, size: file.size, phase: "sending", percent: null, serverOwned: false,
      startsCheck: options.startCheckRubricId !== undefined }, ...current]);
    void run(id, file, options);
  }, [run]);

  const retry = useCallback((id: string) => {
    const entry = files.current.get(id);
    if (entry) void run(id, entry.file, entry.options);
  }, [run]);

  const dismiss = useCallback((id: string) => {
    files.current.delete(id);
    const attemptId = uploads.find((upload) => upload.id === id)?.attemptId;
    if (attemptId && restoredFor.current !== null) rememberDismissed(restoredFor.current, attemptId);
    setUploads((current) => current.filter((upload) => upload.id !== id));
  }, [uploads]);

  // A new page shows the uploads that are still preparing on the server, or
  // finished since, even when another tab sent them.
  const { status: scopeStatus, capture, isCurrent: scopeCurrent } = scope;
  useEffect(() => {
    if (!directUpload() || scopeStatus !== "ready" || restoredFor.current !== null) return;
    let actorId: number;
    try { actorId = capture().actorId; } catch { return; }
    restoredFor.current = actorId;
    const owner = { isCurrent: scopeCurrent };
    void listActiveUploads().then((statuses) => {
      if (!owner.isCurrent()) return;
      const dismissed = readDismissed(actorId);
      // A file still waiting for its bytes is being sent by another tab, or
      // was abandoned; this tab cannot tell which, so it shows neither. An
      // expired upload never reached the server (it timed out, or a newer
      // upload of the same file replaced it), so there is nothing to open.
      const shown = statuses.filter((status) => status.status !== "pending" && status.status !== "expired"
        && !dismissed.has(status.attempt_id));
      setUploads((current) => {
        const known = new Set(current.map((item) => item.attemptId).filter(Boolean));
        const added = shown.filter((status) => !known.has(status.attempt_id)).map((status): BackgroundUpload => ({
          id: `server-${status.attempt_id}`, attemptId: status.attempt_id, fileName: status.filename?.trim() || "Manuscript",
          size: Number.isFinite(status.size) ? status.size ?? 0 : 0, phase: "preparing", percent: null, serverOwned: true,
          startsCheck: status.check_requested === true, ...fromStatus(status) }));
        return [...current, ...added];
      });
      for (const status of shown) {
        if (status.status !== "queued" && status.status !== "processing") continue;
        const id = `server-${status.attempt_id}`;
        void followUpload(status, owner.isCurrent, (next) => update(id, fromStatus(next)))
          .then((last) => {
            if (!owner.isCurrent()) return;
            if (last === null) { update(id, { phase: "failed", error: UNCONFIRMED }); return; }
            settled(fromStatus(last));
          })
          .catch(() => { if (owner.isCurrent()) update(id, { phase: "failed", error: UNCONFIRMED }); });
      }
    }).catch(() => {
      // An older server has no upload list; the tray simply starts empty.
    });
  }, [scopeStatus, capture, scopeCurrent, update, settled]);

  // Closing the tab only stops an upload while its bytes are still on the
  // way; after that the server finishes on its own.
  const leaving = uploads.some((item) => (item.phase === "waiting" || item.phase === "sending" || item.phase === "preparing") && !item.serverOwned);
  useEffect(() => {
    if (!leaving) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [leaving]);

  const value = useMemo(() => ({ uploads, canStartCheck: directUpload(), start, dismiss }), [uploads, start, dismiss]);
  return <BackgroundUploadsContext.Provider value={value}>
    {children}
    {uploads.length > 0 && <UploadTray uploads={uploads} canRetry={(id) => files.current.has(id)} onDismiss={dismiss} onRetry={retry} />}
  </BackgroundUploadsContext.Provider>;
}

function sizeLabel(bytes: number) {
  if (bytes <= 0) return null;
  return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function stateText(item: BackgroundUpload) {
  switch (item.phase) {
    case "waiting": return "Waiting for another upload to finish. It will start on its own.";
    case "sending": return item.percent === null ? "Sending file" : `Sending file ${item.percent}%`;
    case "preparing": return item.startsCheck
      ? "Preparing: reading the pages. The check starts when this finishes. You can keep working or close this tab."
      : "Preparing: reading the pages. You can keep working or close this tab.";
    case "done":
      if (item.checkRunId !== undefined) return "Ready. The check has started.";
      // The server's reason already says what happened and where to go next.
      if (item.checkStartError) return `Ready. ${item.checkStartError}`;
      return "Ready.";
    case "failed": return item.error;
  }
}

// BUG-558: kept uploads pile up in the corner and cover page actions, so the
// tray can be folded to one line. A new upload unfolds it again.
const TRAY_FOLDED_KEY = "veridical.uploads-tray-folded.v1";

function readFolded() {
  try { return window.localStorage.getItem(TRAY_FOLDED_KEY) === "true"; } catch { return false; }
}

function rememberFolded(folded: boolean) {
  try { window.localStorage.setItem(TRAY_FOLDED_KEY, String(folded)); } catch { /* Not remembered on this device. */ }
}

const isActive = (item: BackgroundUpload) => item.phase === "waiting" || item.phase === "sending" || item.phase === "preparing";

export function traySummary(uploads: BackgroundUpload[]) {
  const active = uploads.filter(isActive).length;
  const ready = uploads.filter((item) => item.phase === "done").length;
  const failed = uploads.filter((item) => item.phase === "failed").length;
  return [active && `${active} in progress`, ready && `${ready} ready`, failed && `${failed} not uploaded`].filter(Boolean).join(" · ");
}

function UploadTray({ uploads, canRetry, onDismiss, onRetry }: {
  uploads: BackgroundUpload[]; canRetry: (id: string) => boolean; onDismiss: (id: string) => void; onRetry: (id: string) => void;
}) {
  const listId = useId();
  const [folded, setFolded] = useState(readFolded);
  const active = uploads.filter(isActive).length;
  const seenActive = useRef(active);
  useEffect(() => {
    if (active > seenActive.current) setFolded(false);
    seenActive.current = active;
  }, [active]);
  function toggle() {
    setFolded(!folded);
    rememberFolded(!folded);
  }
  return <aside className="signal-upload-tray" aria-label="Uploads in progress" data-folded={folded ? "true" : undefined}>
    <div className="signal-upload-tray__header">
      <h2 className="signal-upload-tray__title">Uploads</h2>
      <Button variant="quiet" aria-expanded={!folded} aria-controls={listId} onClick={toggle}>{folded ? "Show" : "Hide"}</Button>
    </div>
    {/* Folded, the per-upload messages are not on the page, so the summary
        carries what changed. */}
    {folded && <p role="status" aria-live="polite" className="signal-upload-tray__summary">{traySummary(uploads)}</p>}
    <ul id={listId} hidden={folded}>
      {uploads.map((item) => <li key={item.id} className="signal-upload-tray__item" data-phase={item.phase}
        data-attention={item.phase === "done" && item.checkStartError ? "true" : undefined}>
        <div className="signal-upload-tray__name"><strong>{item.fileName}</strong>{sizeLabel(item.size) && <span>{sizeLabel(item.size)}</span>}</div>
        <p role="status" aria-live="polite" className="signal-upload-tray__state">{stateText(item)}</p>
        {item.phase === "sending" && <progress max={100} value={item.percent ?? undefined} aria-label={`Upload progress for ${item.fileName}`} />}
        {item.phase === "sending" && !item.serverOwned && <p className="signal-field-hint">Keep this tab open until the file is sent.</p>}
        {item.phase === "failed" && item.keptRecord !== undefined && <p className="signal-field-hint">{item.keptRecord ? UPLOAD_REFUSAL_COPY.kept : UPLOAD_REFUSAL_COPY.notKept}</p>}
        {item.phase === "failed" && item.keptRecord && <ActionLink variant="secondary" to="/dashboard">{UPLOAD_REFUSAL_COPY.openDesk}</ActionLink>}
        {item.phase === "failed" && !canRetry(item.id) && <p className="signal-field-hint">Choose the file again in Upload manuscript.</p>}
        <div className="signal-upload-tray__actions">
          {item.phase === "done" && item.checkRunId !== undefined && <ActionLink variant="secondary" to={`/checks/${item.checkRunId}`}>Open the check</ActionLink>}
          {/* A refused start may still have created the check, so the Review Desk is
              offered instead of a second start. */}
          {item.phase === "done" && item.checkRunId === undefined && item.checkStartError
            && <ActionLink variant="secondary" to="/dashboard">Open the Review Desk</ActionLink>}
          {item.phase === "done" && item.checkRunId === undefined && !item.checkStartError && item.manuscriptId !== undefined
            && <ActionLink variant="secondary" to={`/dashboard?check=${item.manuscriptId}`}>Start a check</ActionLink>}
          {item.phase === "failed" && canRetry(item.id) && <Button variant="secondary" onClick={() => onRetry(item.id)}>Try again</Button>}
          {(item.phase === "done" || item.phase === "failed") && <Button variant="quiet" onClick={() => onDismiss(item.id)} aria-label={`Dismiss ${item.fileName}`}>Dismiss</Button>}
        </div>
      </li>)}
    </ul>
  </aside>;
}
