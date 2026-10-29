// V-090: uploads keep preparing on the server after the tab that sent them
// closes, so a fresh page asks the server which ones are still running or
// finished recently and shows them in the uploads tray again.
import { api } from "./client";
import type { UploadStatus } from "./directManuscriptUpload";

const STATUSES = new Set<UploadStatus["status"]>(["pending", "queued", "processing", "done", "failed", "expired"]);

function isUploadStatus(value: unknown): value is UploadStatus {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Partial<UploadStatus>;
  return typeof item.attempt_id === "string" && item.attempt_id.length > 0
    && typeof item.status === "string" && STATUSES.has(item.status);
}

export async function listActiveUploads(): Promise<UploadStatus[]> {
  const out = await api.get<unknown>("/manuscripts/uploads?active=true");
  return Array.isArray(out) ? out.filter(isUploadStatus) : [];
}

export function startedCheckId(status: UploadStatus): number | undefined {
  return Number.isSafeInteger(status.check_run_id) && (status.check_run_id ?? 0) > 0 ? status.check_run_id ?? undefined : undefined;
}

// Polls one upload that another tab sent until the server says it is done
// or failed. Gives up (returns null) at the server's own poll limit, or when
// the signed-in instructor changes.
export async function followUpload(initial: UploadStatus, isCurrent: () => boolean,
  onStatus: (status: UploadStatus) => void): Promise<UploadStatus | null> {
  let state = initial;
  const started = Date.now();
  while (state.status === "queued" || state.status === "processing") {
    if (!Number.isFinite(state.poll_after_ms) || state.poll_after_ms <= 0
      || !Number.isFinite(state.poll_timeout_ms) || state.poll_timeout_ms <= 0
      || Date.now() - started > state.poll_timeout_ms) return null;
    await new Promise((resolve) => setTimeout(resolve, state.poll_after_ms));
    if (!isCurrent()) return null;
    const next = await api.get<unknown>(`/manuscripts/uploads/${encodeURIComponent(state.attempt_id)}`);
    if (!isCurrent()) return null;
    if (!isUploadStatus(next) || next.attempt_id !== state.attempt_id) return null;
    state = next;
    onStatus(state);
  }
  return state;
}
