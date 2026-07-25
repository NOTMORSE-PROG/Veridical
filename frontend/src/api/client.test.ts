import { afterEach, describe, expect, it, vi } from "vitest";
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
