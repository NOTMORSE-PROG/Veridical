import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, VALIDATION_FALLBACK } from "./client";

describe("api client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each([null, "b".repeat(64)])("withholds source bytes without the requested identity header (%s)", async identity => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("PDF bytes", {
      headers: { "Content-Type": "application/pdf", ...(identity ? { "X-Evidence-Source-Identity": identity } : {}) },
    })));
    await expect(api.getSourceBytes("/source", "a".repeat(64))).rejects.toMatchObject({ status: 409, code: "conflict" });
  });

  it("delivers bound PDF bytes with cancellation and credentials", async () => {
    const identity = "a".repeat(64);
    const fetch = vi.fn().mockResolvedValue(new Response(new Uint8Array([1, 2, 3]), {
      headers: { "Content-Type": "application/pdf", "X-Evidence-Source-Identity": identity },
    }));
    vi.stubGlobal("fetch", fetch);
    const controller = new AbortController();
    await expect(api.getSourceBytes("/source", identity, { signal: controller.signal })).resolves.toEqual(new Uint8Array([1, 2, 3]));
    expect(fetch.mock.calls[0][1]).toMatchObject({ signal: controller.signal, credentials: "include" });
  });

  it("rejects other media types even with the expected source image identity", async () => {
    const identity = "a".repeat(64);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("untrusted body", {
      headers: { "Content-Type": "text/html", "X-Evidence-Source-Identity": identity },
    })));
    await expect(api.getSourceImage("/source-image", identity)).rejects.toMatchObject({ status: 409, code: "conflict" });
  });

  it("parses a successful JSON response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 })),
    );
    await expect(api.get<{ ok: boolean }>("/health")).resolves.toEqual({ ok: true });
  });

  it("throws an ApiError carrying the taxonomy code and message on a non-2xx response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ error: { code: "unauthenticated", message: "Not signed in." } }),
          { status: 401 },
        ),
      ),
    );
    await expect(api.get("/auth/me")).rejects.toMatchObject({
      status: 401,
      code: "unauthenticated",
      message: "Not signed in.",
    });
  });

  it("falls back to a generic message when the error body isn't JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not json", { status: 500 })));
    await expect(api.get("/x")).rejects.toThrow("Request failed (500).");
  });

  it("POST sends credentials and a JSON body", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ id: 1 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await api.post("/auth/login", { email: "a@b.com", password: "x" });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.credentials).toBe("include");
    expect(init.body).toBe(JSON.stringify({ email: "a@b.com", password: "x" }));
  });
});

// BUG-003: BASE_URL is computed at module load time from
// import.meta.env.VITE_API_BASE_URL, so testing its default requires a
// fresh module instance per case (vi.resetModules + dynamic import) — the
// static `import { api } from "./client"` above already froze its own
// BASE_URL at file-load time and can't be reused for this.
describe("api/client BASE_URL default", () => {
  // The "api client" describe block above already loaded ./client via a
  // static top-level import, freezing its BASE_URL from whatever the real
  // frontend/.env gave it — resetModules must run BEFORE the first
  // dynamic import here too, not just between this block's own tests.
  beforeEach(() => vi.resetModules());
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("falls back to /api (the vercel.json same-origin proxy) when VITE_API_BASE_URL is unset", async () => {
    vi.stubEnv("VITE_API_BASE_URL", undefined);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({}), { status: 200 })),
    );
    const { api: freshApi } = await import("./client");
    await freshApi.get("/auth/me");
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/api/auth/me");
  });

  it("uses an explicit VITE_API_BASE_URL when set (local dev talking directly to the backend)", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8000");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({}), { status: 200 })),
    );
    const { api: freshApi } = await import("./client");
    await freshApi.get("/auth/me");
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("http://localhost:8000/auth/me");
  });
});

describe("validation refusals", () => {
  afterEach(() => vi.unstubAllGlobals());
  const respond = (body: unknown, status = 422) => vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status })));

  it("shows the server's own sentence for a check written for instructors, with the field", async () => {
    respond({ detail: [{ type: "value_error", loc: ["body", "reason"], msg: "Value error, a reason is required to dismiss findings", input: null }] });
    const error = await api.post("/x", {}).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, code: "validation_error", message: "A reason is required to dismiss findings.", fields: ["reason"] });
  });

  it("names a character that cannot be saved as the server says it", async () => {
    respond({ detail: [{ type: "text_not_storable", loc: ["body", "criteria", 2, "text"], msg: "This text contains a character that cannot be saved. Remove it and try again.", input: "x" }] });
    await expect(api.post("/x", {})).rejects.toMatchObject({ code: "validation_error", message: "This text contains a character that cannot be saved. Remove it and try again.", fields: ["criteria.2.text"] });
  });

  it("replaces technical validator text with a plain sentence", async () => {
    respond({ detail: [{ type: "string_too_short", loc: ["body", "note"], msg: "String should have at least 1 character", input: "" }] });
    await expect(api.post("/x", {})).rejects.toMatchObject({ code: "validation_error", message: VALIDATION_FALLBACK, fields: ["note"] });
  });

  it("keeps each field's own message when one refusal names several", async () => {
    respond({ detail: [
      { type: "text_not_storable", loc: ["body", "criteria", 0, "evidence"], msg: "This text contains an invisible character that can't be saved.", input: "x" },
      { type: "string_too_short", loc: ["body", "criteria", 2, "text"], msg: "String should have at least 1 character", input: "" },
    ] });
    const error = await api.post("/x", {}).catch((e: unknown) => e) as ApiError;
    expect(error.fieldMessages.get("criteria.0.evidence")).toBe("This text contains an invisible character that can't be saved.");
    expect(error.fieldMessages.get("criteria.2.text")).toBe(VALIDATION_FALLBACK);
  });

  it("keeps the app's own error envelope as it was", async () => {
    respond({ error: { code: "file_malformed", message: "That file could not be read." } });
    await expect(api.post("/x", {})).rejects.toMatchObject({ code: "file_malformed", message: "That file could not be read." });
  });
});

