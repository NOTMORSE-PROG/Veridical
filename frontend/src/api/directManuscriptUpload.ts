// F1/F9: file bytes go directly to the private object store; API requests are metadata only.
import { api, ApiError } from "./client";
import { uploadParts, type SignedPart } from "./multipartUpload";
import { StaleUploadContextError, UnconfirmedUploadError } from "./uploadOutcome";
import type { UploadAttemptScope } from "../auth/useUploadScope";

// Transfers this client can perform. A server that does not know a value
// ignores it and answers with the original single-request transfer.
const SUPPORTED_TRANSPORTS = "parts, stream";

interface UploadStatus {
  attempt_id: string;
  status: "pending" | "queued" | "processing" | "done" | "failed" | "expired";
  upload_url?: string;
  transport?: "r2" | "relay";
  relay_mode?: "stream";
  parts?: SignedPart[];
  part_concurrency?: number;
  part_retries?: number;
  upload_headers?: Record<string, string>;
  result?: unknown;
  error?: string;
  poll_after_ms: number;
  poll_timeout_ms: number;
}

const attempts = new WeakMap<File, Map<number, string>>();

export interface DirectUploadProgress {
  onProgress?: (percent: number) => void;
  // The bytes are stored and the server is preparing the file.
  onPreparing?: () => void;
}

export async function directManuscriptUpload(file: File, scope: UploadAttemptScope, progress: DirectUploadProgress = {}): Promise<unknown> {
  const ownerAttempts = attempts.get(file) ?? new Map<number, string>();
  attempts.set(file, ownerAttempts);
  const attemptId = ownerAttempts.get(scope.actorId) ?? crypto.randomUUID();
  ownerAttempts.set(scope.actorId, attemptId);
  const headers = { "X-Veridical-Expected-Instructor": String(scope.actorId) };
  const current = () => { if (!scope.isCurrent()) throw new StaleUploadContextError(); };
  current();
  let state = await api.post<UploadStatus>("/manuscripts/uploads", {
    attempt_id: attemptId, filename: file.name, size: file.size,
  }, { ...headers, "X-Veridical-Upload-Transports": SUPPORTED_TRANSPORTS });
  current();
  if (state.attempt_id !== attemptId) throw new UnconfirmedUploadError();
  if (state.status === "pending") {
    // A mounted attempt retry reuses its identity. A lost completion response
    // queries that same attempt instead of creating another manuscript.
    if (state.transport === "relay" && state.relay_mode === "stream") {
      // The server stores the body in parts while it is still arriving.
      state = await api.putBody<UploadStatus>(`/manuscripts/uploads/${attemptId}/body`, file, headers, progress.onProgress);
    } else if (state.transport === "relay") {
      const form = new FormData();
      form.append("file", file);
      state = await api.post<UploadStatus>(`/manuscripts/uploads/${attemptId}/file`, form, headers);
    } else if (state.parts) {
      await uploadParts(file, {
        parts: state.parts,
        concurrency: state.part_concurrency ?? 1,
        retries: state.part_retries ?? 0,
      }, progress.onProgress);
      current();
      await scope.verify();
      current();
      state = await api.post<UploadStatus>(`/manuscripts/uploads/${attemptId}/complete`, undefined, headers);
      if (state.status !== "pending") progress.onProgress?.(100);
    } else {
      if (!state.upload_url || !state.upload_headers) throw new UnconfirmedUploadError();
      await api.putUpload(state.upload_url, file, state.upload_headers, progress.onProgress);
      current();
      await scope.verify();
      current();
      state = await api.post<UploadStatus>(`/manuscripts/uploads/${attemptId}/complete`, undefined, headers);
    }
    current();
  }
  const started = Date.now();
  if (state.status === "queued" || state.status === "processing") progress.onPreparing?.();
  while (state.status === "queued" || state.status === "processing") {
    if (!Number.isFinite(state.poll_after_ms) || state.poll_after_ms <= 0
      || !Number.isFinite(state.poll_timeout_ms) || state.poll_timeout_ms <= 0
      || Date.now() - started > state.poll_timeout_ms) throw new UnconfirmedUploadError();
    await new Promise((resolve) => setTimeout(resolve, state.poll_after_ms));
    current();
    state = await api.get<UploadStatus>(`/manuscripts/uploads/${attemptId}`);
    current();
    if (state.attempt_id !== attemptId) throw new UnconfirmedUploadError();
  }
  if (state.status === "failed" || state.status === "expired") {
    ownerAttempts.delete(scope.actorId);
    throw new ApiError(422, "upload_processing_failed",
      state.error ?? "Document processing did not finish. Check the saved record in Review Desk.");
  }
  if (state.status !== "done" || !state.result) throw new UnconfirmedUploadError();
  return state.result;
}
