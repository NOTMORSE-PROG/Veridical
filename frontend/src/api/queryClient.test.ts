import { MutationObserver, QueryObserver } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API_READ_RETRIES, SHARED_REPORT_READ_RETRIES } from "../config/network";
import { recoverableReadError } from "../report/reportReadState";
import { sharedReportQueryOptions } from "../report/useShare";
import { api } from "./client";
import { createAppQueryClient } from "./queryClient";

afterEach(() => vi.unstubAllGlobals());

function response(status: number) {
  return new Response(JSON.stringify(status === 200 ? { id: 39 } : {
    error: { code: "controlled_read_failure", message: "Controlled response" },
  }), { status });
}

describe("production API query retry policy", () => {
  it.each([400, 401, 403, 404, 410, 422, 429])("settles cached private HTTP %s on its first response and allows manual recovery", async (status) => {
    const client = createAppQueryClient();
    const key = ["flags", 21];
    client.setQueryData(key, { id: 39 });
    const fetch = vi.fn().mockImplementationOnce(async () => response(status)).mockImplementation(async () => response(200));
    vi.stubGlobal("fetch", fetch);
    // The production retry predicate is untouched; only elapsed backoff is
    // removed so a regressed retry would be an observable additional GET.
    const observer = new QueryObserver(client, { queryKey: key, queryFn: () => api.get("/controlled-flags"), retryDelay: 0 });
    const states: { error: boolean; usable: boolean; fetching: boolean }[] = [];
    const unsubscribe = observer.subscribe(result => states.push({
      error: result.isError, usable: Boolean(result.data && recoverableReadError(result.error)), fetching: result.isFetching,
    }));
    try {
      const denied = await observer.refetch({ cancelRefetch: false });
      expect(fetch).toHaveBeenCalledTimes(1);
      expect(denied.isError).toBe(true);
      expect(denied.isFetching).toBe(false);
      expect(denied.data).toEqual({ id: 39 });
      expect(states.at(-1)).toEqual({ error: true, usable: false, fetching: false });
      const recovered = await observer.refetch();
      expect(recovered.isSuccess).toBe(true);
      expect(fetch).toHaveBeenCalledTimes(2);
    } finally { unsubscribe(); client.clear(); }
  });

  it("retains the existing bounded transient retry allowance", async () => {
    const client = createAppQueryClient();
    const fetch = vi.fn(async () => response(503));
    vi.stubGlobal("fetch", fetch);
    try {
      await expect(client.fetchQuery({ queryKey: ["transient"], queryFn: () => api.get("/controlled-read"), retryDelay: 0 })).rejects.toMatchObject({ status: 503 });
      expect(fetch).toHaveBeenCalledTimes(API_READ_RETRIES + 1);
    } finally { client.clear(); }
  });

  it("recovers from a transient server failure within that allowance", async () => {
    const client = createAppQueryClient();
    const fetch = vi.fn().mockImplementationOnce(async () => response(503)).mockImplementation(async () => response(200));
    vi.stubGlobal("fetch", fetch);
    try {
      await expect(client.fetchQuery({ queryKey: ["recovery"], queryFn: () => api.get("/controlled-read"), retryDelay: 0 })).resolves.toEqual({ id: 39 });
      expect(fetch).toHaveBeenCalledTimes(2);
    } finally { client.clear(); }
  });

  it("does not repeat a failed instructor mutation", async () => {
    const client = createAppQueryClient();
    const fetch = vi.fn(async () => response(503));
    vi.stubGlobal("fetch", fetch);
    const mutation = new MutationObserver(client, { mutationFn: () => api.post("/controlled-action", {}) });
    try {
      await expect(mutation.mutate()).rejects.toMatchObject({ status: 503 });
      expect(fetch).toHaveBeenCalledTimes(1);
    } finally { client.clear(); }
  });

  it.each([401, 403, 404, 410, 503])("applies terminal status handling to the public report override (%s)", async status => {
    const client = createAppQueryClient();
    const fetch = vi.fn(async () => response(status));
    vi.stubGlobal("fetch", fetch);
    try {
      await expect(client.fetchQuery({ ...sharedReportQueryOptions("controlled-local-token"), retryDelay: 0 })).rejects.toMatchObject({ status });
      expect(fetch).toHaveBeenCalledTimes(status === 503 ? SHARED_REPORT_READ_RETRIES + 1 : 1);
    } finally { client.clear(); }
  });
});
