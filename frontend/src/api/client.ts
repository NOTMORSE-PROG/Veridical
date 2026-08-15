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
const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // A FormData body (file upload) must NOT get a manual Content-Type —
  // the browser sets the multipart boundary itself.
  const isFormData = init?.body instanceof FormData;
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: isFormData ? init?.headers : { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as
      | { error?: { code?: string; message?: string } }
      | null;
    throw new ApiError(
      res.status,
      body?.error?.code ?? "internal",
      body?.error?.message ?? `Request failed (${res.status}).`,
    );
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// V-039: the report PDF export returns raw bytes (application/pdf), not
// the JSON envelope every other endpoint uses -- a separate path that
// still shares the same session-cookie/error-envelope handling as
// `request()`, not a second ad-hoc fetch call.
async function requestBlob(path: string): Promise<Blob> {
  const res = await fetch(`${BASE_URL}${path}`, { credentials: "include" });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as
      | { error?: { code?: string; message?: string } }
      | null;
    throw new ApiError(
      res.status,
      body?.error?.code ?? "internal",
      body?.error?.message ?? `Request failed (${res.status}).`,
    );
  }
  return res.blob();
}

export const api = {
  get: <T>(path: string): Promise<T> => request<T>(path),
  getBlob: (path: string): Promise<Blob> => requestBlob(path),
  post: <T>(path: string, body?: unknown): Promise<T> =>
    request<T>(path, {
      method: "POST",
      body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
    }),
  put: <T>(path: string, body: unknown): Promise<T> =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  delete: <T = void>(path: string): Promise<T> => request<T>(path, { method: "DELETE" }),
};
