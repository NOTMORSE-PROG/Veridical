import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./client";

describe("api client", () => {
  afterEach(() => vi.unstubAllGlobals());

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
