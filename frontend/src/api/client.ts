// Typed fetch wrapper — the ONE place the app talks to the backend
// (CODING.md §3: components never call fetch directly). Sends cookies
// (the session cookie, V-014) and maps the failure-taxonomy envelope
// ({error: {code, message}}) into a typed error every caller can branch on.
//
// BUG-003: default is "/api", not "" — production routes through
// `vercel.json`'s same-origin rewrite proxy to the Render backend, so the
// browser only ever sees one site (veridical-app.vercel.app) and the
// SameSite=Lax session cookie survives every follow-up fetch. Before this,
// the frontend called the Render URL directly (a genuinely cross-*site*
// subresource request, not just cross-origin), and Lax cookies are never
// attached to those — login set the cookie, but every call after it read
// as logged-out. Local dev still sets VITE_API_BASE_URL explicitly
// (frontend/.env) to talk to the backend directly; this default only
// matters when it's unset.
// Exported for V-065's PDF.js pane: pdf.js loads a PDF by URL (with
// `withCredentials: true` for the session cookie) rather than through this
// module's own JSON/blob request functions, so it needs the same base URL
// this file already resolves once, not a second guess at it.
export const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  // For a validation refusal: the fields the server named, e.g. "reason".
  readonly fields: readonly string[];

  constructor(status: number, code: string, message: string, fields: readonly string[] = []) {
    super(message);
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

// Validation messages the server writes for instructors; any other
// validator text is technical and is replaced by a plain sentence.
const READABLE_VALIDATION_TYPES = new Set(["value_error", "text_not_storable"]);
export const VALIDATION_FALLBACK = "Some of the information sent was not accepted. Check what you entered and try again.";

type ValidationItem = { type?: unknown; loc?: unknown; msg?: unknown };

function validationError(items: ValidationItem[]): ApiError {
  const fields = [...new Set(items.flatMap((item) => Array.isArray(item.loc)
    ? [item.loc.filter((part) => part !== "body" && part !== "query" && part !== "path").map(String).join(".")].filter(Boolean) : []))];
  const readable = items.find((item) => typeof item.type === "string" && READABLE_VALIDATION_TYPES.has(item.type) && typeof item.msg === "string" && item.msg.trim());
  // Pydantic prefixes a custom check's own sentence with "Value error, ".
  const text = typeof readable?.msg === "string" ? readable.msg.replace(/^Value error,\s*/i, "").trim() : "";
  const message = text ? text.charAt(0).toUpperCase() + text.slice(1) + (/[.!?]$/.test(text) ? "" : ".") : VALIDATION_FALLBACK;
  return new ApiError(422, "validation_error", message, fields);
}

// One reading of an error body: the app's own {error: {code, message}}, or
// FastAPI's request validation {detail: [...]}, which would otherwise reach
// the screen as "Request failed (422)."
function errorFromResponse(status: number, body: unknown): ApiError {
  const value = (body && typeof body === "object" ? body : {}) as { error?: { code?: string; message?: string }; detail?: unknown };
  if (value.error?.code || value.error?.message) {
    return new ApiError(status, value.error.code ?? "internal", value.error.message ?? `Request failed (${status}).`);
  }
  if (status === 422 && Array.isArray(value.detail) && value.detail.length > 0) return validationError(value.detail as ValidationItem[]);
  return new ApiError(status, "internal", `Request failed (${status}).`);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // A FormData body (file upload) must NOT get a manual Content-Type —
  // the browser sets the multipart boundary itself.
  const isFormData = init?.body instanceof FormData;
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    credentials: init?.credentials ?? "include",
    headers: isFormData ? init?.headers : { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    throw errorFromResponse(res.status, await res.json().catch(() => null));
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// V-039: the report PDF export returns raw bytes (application/pdf), not
// the JSON envelope every other endpoint uses -- a separate path that
// still shares the same session-cookie/error-envelope handling as
// `request()`, not a second ad-hoc fetch call.
async function requestBlob(path: string, options?: Pick<RequestInit, "signal">): Promise<Blob> {
  const res = await fetch(`${BASE_URL}${path}`, { ...options, credentials: "include" });
  if (!res.ok) {
    throw errorFromResponse(res.status, await res.json().catch(() => null));
  }
  return res.blob();
}

async function requestBoundSource(path: string, sourceIdentity: string, contentTypes: readonly string[], options?: Pick<RequestInit, "signal">) {
  const res = await fetch(`${BASE_URL}${path}`, { ...options, credentials: "include" });
  if (!res.ok) {
    const body = await res.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
    throw new ApiError(res.status, body?.error?.code ?? "internal", body?.error?.message ?? `Request failed (${res.status}).`);
  }
  if (res.headers.get("X-Evidence-Source-Identity") !== sourceIdentity
    || !contentTypes.includes(res.headers.get("Content-Type")?.split(";")[0].trim() ?? "")) {
    throw new ApiError(409, "conflict", "This source copy could not be linked to the selected evidence. Reload its location.");
  }
  return res;
}

export const api = {
  putUpload: async (url: string, file: File, headers: Record<string, string>, onProgress?: (percent: number) => void): Promise<void> => {
    if (onProgress) {
      // XMLHttpRequest is the browser API that reports upload progress.
      await new Promise<void>((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open("PUT", url);
        for (const [name, value] of Object.entries(headers)) request.setRequestHeader(name, value);
        request.upload.onprogress = (event) => { if (event.lengthComputable) onProgress(Math.min(99, Math.floor((event.loaded / event.total) * 100))); };
        request.onload = () => request.status >= 200 && request.status < 300 ? (onProgress(100), resolve())
          : reject(new ApiError(request.status, "upload_transport", "The file transfer did not finish. Retry this upload to check its saved status."));
        request.onerror = () => reject(new ApiError(0, "upload_transport", "The file transfer did not finish. Retry this upload to check its saved status."));
        request.send(file);
      });
      return;
    }
    const response = await fetch(url, { method: "PUT", body: file, headers,
      credentials: "omit", redirect: "error" });
    if (!response.ok) throw new ApiError(response.status, "upload_transport", "The file transfer did not finish. Retry this upload to check its saved status.");
  },
  // Streams a file as the raw request body to the API. XMLHttpRequest is
  // the browser API that reports upload progress; the percent stays below
  // 100 until the server confirms the stored file.
  putBody: <T>(path: string, file: File, headers: Record<string, string>, onProgress?: (percent: number) => void): Promise<T> =>
    new Promise<T>((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("PUT", `${BASE_URL}${path}`);
      request.withCredentials = true;
      for (const [name, value] of Object.entries(headers)) request.setRequestHeader(name, value);
      request.setRequestHeader("Content-Type", "application/octet-stream");
      request.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) onProgress(Math.min(99, Math.floor((event.loaded / event.total) * 100)));
      };
      request.onload = () => {
        let body: unknown = null;
        try { body = JSON.parse(request.responseText); } catch { body = null; }
        if (request.status >= 200 && request.status < 300) {
          onProgress?.(100);
          resolve(body as T);
          return;
        }
        const error = (body as { error?: { code?: string; message?: string } } | null)?.error;
        reject(new ApiError(request.status, error?.code ?? "upload_transport",
          error?.message ?? "The file transfer did not finish. Retry this upload to check its saved status."));
      };
      request.onerror = () => reject(new ApiError(0, "upload_transport", "The file transfer did not finish. Retry this upload to check its saved status."));
      request.send(file);
    }),
  getPublic: <T>(path: string, options?: Pick<RequestInit, "signal">): Promise<T> => request<T>(path, { ...options, credentials: "omit" }),
  get: <T>(path: string, options?: Pick<RequestInit, "signal">): Promise<T> => request<T>(path, options),
  getBlob: (path: string, options?: Pick<RequestInit, "signal">): Promise<Blob> => requestBlob(path, options),
  getSourceBytes: async (path: string, identity: string, options?: Pick<RequestInit, "signal">) =>
    new Uint8Array(await (await requestBoundSource(path, identity, ["application/pdf"], options)).arrayBuffer()),
  getSourceImage: async (path: string, identity: string, options?: Pick<RequestInit, "signal">) =>
    (await requestBoundSource(path, identity, ["image/png", "image/jpeg"], options)).blob(),
  post: <T>(path: string, body?: unknown, headers?: Record<string, string>): Promise<T> =>
    request<T>(path, {
      method: "POST",
      headers,
      body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
    }),
  put: <T>(path: string, body: unknown): Promise<T> =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown): Promise<T> =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T = void>(path: string): Promise<T> => request<T>(path, { method: "DELETE" }),
};
