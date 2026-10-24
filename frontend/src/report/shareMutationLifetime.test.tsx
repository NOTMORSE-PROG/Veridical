import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { useCreateShareLink, useRevokeShareLink, useShareLink } from "./useShare";

const syntheticLink = (run: number) => ({ check_run_id: run, token: "synthetic-not-a-capability", created_at: "2026-10-24T00:00:00Z", expires_at: null });
function setup(action: "create" | "revoke") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(ME_QUERY_KEY, { id: 1 });
  vi.spyOn(api, "get").mockResolvedValue(action === "create" ? syntheticLink(11) : null);
  let resolve: (value: never) => void;
  const request = vi.spyOn(api, action === "create" ? "post" : "delete").mockImplementation(() => new Promise(r => { resolve = r; }));
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const hook = renderHook(({ run }) => {
    const create = useCreateShareLink(run); const revoke = useRevokeShareLink(run);
    return { start: () => action === "create" ? create.mutate({ expires_at: null }) : revoke.mutate(),
      done: action === "create" ? create.isSuccess || create.isError : revoke.isSuccess || revoke.isError };
  }, { initialProps: { run: 11 }, wrapper });
  return { client, request, hook, resolveRaw: (value: unknown) => resolve(value as never),
    resolve: (run = 11) => resolve((action === "create" ? syntheticLink(run) : { ok: true }) as never) };
}
afterEach(() => vi.restoreAllMocks());

it.each((["create", "revoke"] as const).flatMap(action => (["account", "relogin", "identity"] as const).map(change => ({ action, change }))))(
  "does not apply a late $action after $change replacement", async ({ action, change }) => {
    const c = setup(action); act(() => c.hook.result.current.start()); await waitFor(() => expect(c.request).toHaveBeenCalledOnce());
    act(() => {
      if (change !== "identity") c.client.clear();
      c.client.setQueryData(ME_QUERY_KEY, { id: change === "relogin" ? 1 : 2 });
      c.client.setQueryData(["share", 11], { ...syntheticLink(11), token: "new-lifetime-synthetic-value" });
    });
    await act(async () => { c.resolve(); }); await waitFor(() => expect(c.hook.result.current.done).toBe(true));
    expect(c.client.getQueryData(["share", 11])).toEqual({ ...syntheticLink(11), token: "new-lifetime-synthetic-value" });
  },
);

it.each(["create", "revoke"] as const)("keeps %s acknowledgment on its original run after parameter reuse", async action => {
  const c = setup(action); act(() => c.hook.result.current.start()); await waitFor(() => expect(c.request).toHaveBeenCalledOnce());
  c.client.setQueryData(["share", 22], syntheticLink(22)); c.hook.rerender({ run: 22 });
  await act(async () => { c.resolve(); }); await waitFor(() => expect(c.hook.result.current.done).toBe(true));
  expect(c.client.getQueryData(["share", 22])).toEqual(syntheticLink(22));
  expect(c.client.getQueryData(["share", 11])).toEqual(action === "create" ? syntheticLink(11) : null);
});

it("rejects a successful create body for the wrong report", async () => {
  const c = setup("create"); act(() => c.hook.result.current.start()); await waitFor(() => expect(c.request).toHaveBeenCalledOnce());
  await act(async () => { c.resolve(22); }); await waitFor(() => expect(c.hook.result.current.done).toBe(true));
  expect(c.client.getQueryData(["share", 11])).toBeUndefined();
});

it.each(["create", "revoke"] as const)("rejects a stale %s activation before an auth replacement render", async action => {
  const c = setup(action); const oldStart = c.hook.result.current.start;
  act(() => { c.client.clear(); c.client.setQueryData(ME_QUERY_KEY, { id: 2 }); oldStart(); });
  await waitFor(() => expect(c.hook.result.current.done).toBe(true));
  expect(c.request).not.toHaveBeenCalled();
});

it.each(["create", "revoke"] as const)("checks auth again after awaiting %s read cancellation", async action => {
  const c = setup(action); let release!: () => void;
  const cancellation = vi.spyOn(c.client, "cancelQueries").mockImplementation(() => new Promise(resolve => { release = resolve; }));
  act(() => c.hook.result.current.start()); await waitFor(() => expect(c.request).toHaveBeenCalledOnce());
  await act(async () => { c.resolve(); }); await waitFor(() => expect(cancellation).toHaveBeenCalledOnce());
  act(() => { c.client.clear(); c.client.setQueryData(ME_QUERY_KEY, { id: 2 }); c.client.setQueryData(["share", 11], syntheticLink(22)); });
  await act(async () => { release(); }); await waitFor(() => expect(c.hook.result.current.done).toBe(true));
  expect(c.client.getQueryData(["share", 11])).toEqual(syntheticLink(22));
});

it.each(["create", "revoke"] as const)("a preexisting GET cannot overwrite the %s acknowledgment", async action => {
  const c = setup(action); let finishRead!: (value: never) => void; let signal: AbortSignal | undefined;
  let reads = 0;
  vi.spyOn(api, "get").mockImplementation((_path, options) => {
    if (reads++ > 0) return Promise.resolve((action === "create" ? syntheticLink(11) : null) as never);
    signal = options?.signal ?? undefined;
    return new Promise(resolve => { finishRead = resolve; });
  });
  const read = renderHook(() => useShareLink(11), { wrapper: ({ children }: { children: ReactNode }) => <QueryClientProvider client={c.client}>{children}</QueryClientProvider> });
  await waitFor(() => expect(api.get).toHaveBeenCalledOnce());
  act(() => c.hook.result.current.start()); await waitFor(() => expect(c.request).toHaveBeenCalledOnce());
  await act(async () => { c.resolve(); }); await waitFor(() => expect(c.hook.result.current.done).toBe(true));
  expect(signal?.aborted).toBe(true);
  await act(async () => { finishRead({ ...syntheticLink(11), token: "older-read-synthetic-value" } as never); });
  expect(c.client.getQueryData(["share", 11])).toEqual(action === "create" ? syntheticLink(11) : null);
  read.unmount();
});

it.each([{ check_run_id: 11 }, { ...syntheticLink(11), token: " " }, { ...syntheticLink(11), token: 5 },
  { ...syntheticLink(11), created_at: "invalid" }, { ...syntheticLink(11), expires_at: "invalid" }, null])(
  "rejects malformed create acknowledgment %# without cache success", async value => {
    const c = setup("create"); act(() => c.hook.result.current.start()); await waitFor(() => expect(c.request).toHaveBeenCalledOnce());
    await act(async () => { c.resolveRaw(value); }); await waitFor(() => expect(c.hook.result.current.done).toBe(true));
    expect(c.client.getQueryData(["share", 11])).toBeUndefined();
  },
);

it("does not acknowledge a revoke body with ok=false", async () => {
  const c = setup("revoke"); c.client.setQueryData(["share", 11], syntheticLink(11));
  act(() => c.hook.result.current.start()); await waitFor(() => expect(c.request).toHaveBeenCalledOnce());
  await act(async () => { c.resolveRaw({ ok: false }); }); await waitFor(() => expect(c.hook.result.current.done).toBe(true));
  expect(c.client.getQueryData(["share", 11])).toEqual(syntheticLink(11));
});

it("reconciles two out-of-order create acknowledgments through current GET truth", async () => {
  const c = setup("create"); const pending: ((value: never) => void)[] = [];
  c.request.mockImplementation(() => new Promise(resolve => pending.push(resolve)));
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={c.client}>{children}</QueryClientProvider>;
  const second = renderHook(() => useCreateShareLink(11), { wrapper });
  const current = { ...syntheticLink(11), token: "current-synthetic-value" };
  vi.mocked(api.get).mockResolvedValue(current);
  act(() => c.hook.result.current.start()); await waitFor(() => expect(pending).toHaveLength(1));
  act(() => second.result.current.mutate({ expires_at: null })); await waitFor(() => expect(pending).toHaveLength(2));
  await act(async () => { pending[1](current as never); }); await waitFor(() => expect(second.result.current.isSuccess).toBe(true));
  await act(async () => { pending[0](syntheticLink(11) as never); }); await waitFor(() => expect(c.hook.result.current.done).toBe(true));
  expect(api.get).toHaveBeenCalledTimes(2); expect(c.client.getQueryData(["share", 11])).toEqual(current);
});

it("a late revoke acknowledgment cannot imply absence after a newer create", async () => {
  const c = setup("revoke"); let finishCreate!: (value: never) => void;
  vi.spyOn(api, "post").mockImplementation(() => new Promise(resolve => { finishCreate = resolve; }));
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={c.client}>{children}</QueryClientProvider>;
  const second = renderHook(() => useCreateShareLink(11), { wrapper }); const current = syntheticLink(11);
  vi.mocked(api.get).mockResolvedValue(current);
  act(() => c.hook.result.current.start()); await waitFor(() => expect(c.request).toHaveBeenCalledOnce());
  act(() => second.result.current.mutate({ expires_at: null })); await waitFor(() => expect(api.post).toHaveBeenCalledOnce());
  await act(async () => { finishCreate(current as never); }); await waitFor(() => expect(second.result.current.isSuccess).toBe(true));
  await act(async () => { c.resolve(); }); await waitFor(() => expect(c.hook.result.current.done).toBe(true));
  expect(api.get).toHaveBeenCalledTimes(2); expect(c.client.getQueryData(["share", 11])).toEqual(current);
});
