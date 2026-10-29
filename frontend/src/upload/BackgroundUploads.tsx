// V-090: large manuscripts upload and prepare in the background, so the
// instructor can keep working instead of waiting on the upload dialog.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import { directManuscriptUpload } from "../api/directManuscriptUpload";
import { validateIngestReceipt } from "../api/uploadOutcome";
import { useUploadScope } from "../auth/useUploadScope";
import { BACKGROUND_UPLOAD_BUSY_RETRY } from "../config/ui";
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
  manuscriptId?: number;
  checkRunId?: number;
  error?: string;
}

interface BackgroundUploadsValue {
  uploads: BackgroundUpload[];
  start: (file: File) => void;
  dismiss: (id: string) => void;
}

const BackgroundUploadsContext = createContext<BackgroundUploadsValue | null>(null);

export function useBackgroundUploads() {
  return useContext(BackgroundUploadsContext);
}

const directUpload = import.meta.env.VITE_DIRECT_MANUSCRIPT_UPLOAD_ENABLED === "true";

export function BackgroundUploadsProvider({ children }: { children: ReactNode }) {
  const scope = useUploadScope("background-uploads");
  const queryClient = useQueryClient();
  const [uploads, setUploads] = useState<BackgroundUpload[]>([]);
  const files = useRef(new Map<string, File>());
  const update = useCallback((id: string, patch: Partial<BackgroundUpload>) =>
    setUploads((current) => current.map((item) => item.id === id ? { ...item, ...patch } : item)), []);

  const run = useCallback(async (id: string, file: File, busyAttempt = 0): Promise<void> => {
    const attempt = scope.capture();
    update(id, { phase: "sending", percent: directUpload ? 0 : null, error: undefined, serverOwned: false });
    try {
      await attempt.verify();
      const out = directUpload
        ? await directManuscriptUpload(file, attempt, {
          onProgress: (percent) => update(id, { percent }),
          onPreparing: () => update(id, { phase: "preparing", percent: null, serverOwned: true }),
        })
        : await api.post<unknown>("/manuscripts/ingest", (() => { const form = new FormData(); form.append("file", file); return form; })(),
          { "X-Veridical-Expected-Instructor": String(attempt.actorId) });
      const receipt = validateIngestReceipt(out);
      files.current.delete(id);
      update(id, { phase: "done", percent: null, serverOwned: true, manuscriptId: receipt.manuscript_id });
      void queryClient.invalidateQueries({ queryKey: ["manuscripts"] });
      void queryClient.invalidateQueries({ queryKey: ["stats"] });
    } catch (error) {
      // The server takes one streamed transfer at a time; another upload in
      // progress is a reason to wait, not a failure.
      if (error instanceof ApiError && error.code === "document_preparation_busy" && busyAttempt + 1 < BACKGROUND_UPLOAD_BUSY_RETRY.maxAttempts) {
        update(id, { phase: "waiting", percent: null });
        await new Promise((resolve) => setTimeout(resolve, BACKGROUND_UPLOAD_BUSY_RETRY.delayMs));
        if (files.current.has(id)) return run(id, file, busyAttempt + 1);
        return;
      }
      update(id, { phase: "failed", percent: null,
        error: error instanceof ApiError ? error.message : "The upload did not finish. Try again, or check the Review Desk for a saved record." });
    }
  }, [queryClient, scope, update]);

  const start = useCallback((file: File) => {
    const id = crypto.randomUUID();
    files.current.set(id, file);
    setUploads((current) => [{ id, fileName: file.name, size: file.size, phase: "sending", percent: null, serverOwned: false }, ...current]);
    void run(id, file);
  }, [run]);

  const retry = useCallback((id: string) => {
    const file = files.current.get(id);
    if (file) void run(id, file);
  }, [run]);

  const dismiss = useCallback((id: string) => {
    files.current.delete(id);
    setUploads((current) => current.filter((item) => item.id !== id));
  }, []);

  // Closing the tab only stops an upload while its bytes are still on the
  // way; after that the server finishes on its own.
  const leaving = uploads.some((item) => (item.phase === "waiting" || item.phase === "sending" || item.phase === "preparing") && !item.serverOwned);
  useEffect(() => {
    if (!leaving) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [leaving]);

  const value = useMemo(() => ({ uploads, start, dismiss }), [uploads, start, dismiss]);
  return <BackgroundUploadsContext.Provider value={value}>
    {children}
    {uploads.length > 0 && <UploadTray uploads={uploads} onDismiss={dismiss} onRetry={retry} />}
  </BackgroundUploadsContext.Provider>;
}

function sizeLabel(bytes: number) {
  return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function UploadTray({ uploads, onDismiss, onRetry }: { uploads: BackgroundUpload[]; onDismiss: (id: string) => void; onRetry: (id: string) => void }) {
  return <aside className="signal-upload-tray" aria-label="Uploads in progress">
    <h2 className="signal-upload-tray__title">Uploads</h2>
    <ul>
      {uploads.map((item) => <li key={item.id} className="signal-upload-tray__item" data-phase={item.phase}>
        <div className="signal-upload-tray__name"><strong>{item.fileName}</strong><span>{sizeLabel(item.size)}</span></div>
        <p role="status" aria-live="polite" className="signal-upload-tray__state">
          {item.phase === "waiting" ? "Waiting for another upload to finish. It will start on its own."
            : item.phase === "sending" ? item.percent === null ? "Sending file" : `Sending file ${item.percent}%`
            : item.phase === "preparing" ? "Preparing: reading the pages. You can keep working or close this tab."
            : item.phase === "done" ? "Ready." : item.error}
        </p>
        {item.phase === "sending" && <progress max={100} value={item.percent ?? undefined} aria-label={`Upload progress for ${item.fileName}`} />}
        {item.phase === "sending" && !item.serverOwned && <p className="signal-field-hint">Keep this tab open until the file is sent.</p>}
        <div className="signal-upload-tray__actions">
          {item.phase === "done" && item.manuscriptId !== undefined && <ActionLink variant="secondary" to={`/dashboard?check=${item.manuscriptId}`}>Start a check</ActionLink>}
          {item.phase === "failed" && <Button variant="secondary" onClick={() => onRetry(item.id)}>Try again</Button>}
          {(item.phase === "done" || item.phase === "failed") && <Button variant="quiet" onClick={() => onDismiss(item.id)} aria-label={`Dismiss ${item.fileName}`}>Dismiss</Button>}
        </div>
      </li>)}
    </ul>
  </aside>;
}
