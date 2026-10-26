import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { useAnnotateFlag, useConfirmCitationSource, useOverrideFlag } from "./useFlag";

const actor = (id = 1) => ({ id, email: `reviewer-${id}@example.test`, display_name: "Reviewer", onboarding_dismissed_at: null });
const reply = { id: 7, check_run_id: 1, annotation: "private note", overridden: true, override_reason: "checked source", confirmed_citation_source: true, report: { check_run_id: 1 } };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function harness(mutationCache?: MutationCache) {
  const client = new QueryClient({ mutationCache, defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(ME_QUERY_KEY, actor());
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return { client, wrapper };
}
function replaceActor(client: QueryClient, id = 2) { client.clear(); client.setQueryData(ME_QUERY_KEY, actor(id)); }
const response = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });

describe("guarded embedded finding actions", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("rejects a retired action before queued dispatch even when the actor ID returns", async () => {
    const { client, wrapper } = harness();
    const fetch = vi.fn(async () => response(reply));
    vi.stubGlobal("fetch", fetch);
    const { result } = renderHook(() => useAnnotateFlag(7, 1), { wrapper });
    await act(async () => {
      const pending = result.current.mutateAsync("private note").catch(error => error);
      replaceActor(client, 1);
      expect(await pending).toBeInstanceOf(Error);
    });
    expect(fetch).not.toHaveBeenCalled();
    expect(client.getQueryData(["flag", 7])).toBeUndefined();
  });

  it("does not accept an old actor's response after the POST was sent", async () => {
    const { client, wrapper } = harness();
    const gate = deferred<Response>();
    const fetch = vi.fn(() => gate.promise);
    vi.stubGlobal("fetch", fetch);
    const { result } = renderHook(() => useAnnotateFlag(7, 1), { wrapper });
    await act(async () => {
      const pending = result.current.mutateAsync("private note").catch(error => error);
      await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
      replaceActor(client);
      gate.resolve(response(reply));
      expect(await pending).toBeInstanceOf(Error);
    });
    expect(client.getQueryData(["flag", 7])).toBeUndefined();
  });

  it("checks the origin again immediately before success cache writes", async () => {
    const gate = deferred<void>();
    let reachedSuccess = false;
    const { client, wrapper } = harness(new MutationCache({ onSuccess: async () => { reachedSuccess = true; await gate.promise; } }));
    vi.stubGlobal("fetch", vi.fn(async () => response(reply)));
    const { result } = renderHook(() => useAnnotateFlag(7, 1), { wrapper });
    await act(async () => {
      const pending = result.current.mutateAsync("private note").catch(error => error);
      await waitFor(() => expect(reachedSuccess).toBe(true));
      replaceActor(client);
      gate.resolve();
      expect(await pending).toBeInstanceOf(Error);
    });
    expect(client.getQueryData(["flag", 7])).toBeUndefined();
  });

  it.each([{ id: 8 }, { check_run_id: 2 }, { annotation: "another note" }])("requires a matching annotation acknowledgement: %j", async change => {
    const { client, wrapper } = harness();
    vi.stubGlobal("fetch", vi.fn(async () => response({ ...reply, ...change })));
    const { result } = renderHook(() => useAnnotateFlag(7, 1), { wrapper });
    await act(async () => { await expect(result.current.mutateAsync("private note")).rejects.toThrow(); });
    expect(client.getQueryData(["flag", 7])).toBeUndefined();
  });

  it("accepts only the intended valid actions and updates their own report", async () => {
    const { client, wrapper } = harness();
    vi.stubGlobal("fetch", vi.fn(async () => response(reply)));
    const { result } = renderHook(() => ({ annotate: useAnnotateFlag(7, 1), override: useOverrideFlag(7, 1), confirm: useConfirmCitationSource(7, 1) }), { wrapper });
    await act(async () => {
      await result.current.annotate.mutateAsync("private note");
      await result.current.override.mutateAsync("checked source");
      await result.current.confirm.mutateAsync("checked source");
    });
    expect(client.getQueryData(["flag", 7])).toEqual(reply);
    expect(client.getQueryData(["report", 1])).toEqual(reply.report);
  });
});
