import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import type { ReportOut } from "../api/types";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { useDecideReport, useReopenReport } from "./useReport";

function setup(action: "decision" | "reopen") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(ME_QUERY_KEY, { id: 1 });
  let finish: (response: Response) => void;
  const fetch = vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; }));
  vi.stubGlobal("fetch", fetch);
  const hook = renderHook(() => {
    const decide = useDecideReport(5);
    const reopen = useReopenReport(5);
    return action === "decision" ? { start: () => decide.mutate({ decision: "returned", note: "Instructor reason", expected_revision: 0 }), done: !decide.isPending && !decide.isIdle }
      : { start: () => reopen.mutate({ reason: "Instructor reason", expected_revision: 0 }), done: !reopen.isPending && !reopen.isIdle };
  }, { wrapper: ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  return { client, hook, fetch, invalidate, finish: (status = 200, run = 5) => finish(new Response(JSON.stringify(status === 200
    ? { assessment_revision: 1, decision: action === "decision" ? "returned" : null, decision_note: action === "decision" ? "Instructor reason" : null, check_run_id: run, manuscript_group_label: "Private report from instructor A" }
    : { error: { code: "conflict", message: "Controlled conflict" } }), { status })) };
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each(["decision", "reopen"] as const)("keeps a current-owner %s acknowledgment and its stats refresh", async (action) => {
  const c = setup(action); act(() => c.hook.result.current.start()); await waitFor(() => expect(c.fetch).toHaveBeenCalledOnce());
  await act(async () => { c.finish(); }); await waitFor(() => expect(c.hook.result.current.done).toBe(true));
  expect(c.client.getQueryData<ReportOut>(["report", 5])?.manuscript_group_label).toBe("Private report from instructor A");
  expect(c.invalidate).toHaveBeenCalledWith({ queryKey: ["stats"] });
});

it.each((["decision", "reopen"] as const).flatMap((action) => (["account", "relogin", "identity"] as const).map((change) => ({ action, change }))))(
  "fences late $action success after $change replacement", async ({ action, change }) => {
    const c = setup(action); act(() => c.hook.result.current.start()); await waitFor(() => expect(c.fetch).toHaveBeenCalledOnce());
    act(() => {
      if (change !== "identity") c.client.clear();
      c.client.setQueryData(ME_QUERY_KEY, { id: change === "relogin" ? 1 : 2 });
      c.client.setQueryData(["report", 5], { check_run_id: 5, manuscript_group_label: "New authentication lifetime" });
    });
    await act(async () => { c.finish(); }); await waitFor(() => expect(c.hook.result.current.done).toBe(true));
    expect(c.client.getQueryData<ReportOut>(["report", 5])?.manuscript_group_label).toBe("New authentication lifetime");
    expect(c.invalidate).not.toHaveBeenCalled();
  });

it.each(["decision", "reopen"] as const)("does not refresh a new account after an old %s conflict", async (action) => {
  const c = setup(action); act(() => c.hook.result.current.start()); await waitFor(() => expect(c.fetch).toHaveBeenCalledOnce());
  act(() => { c.client.clear(); c.client.setQueryData(ME_QUERY_KEY, { id: 2 }); });
  await act(async () => { c.finish(409); }); await waitFor(() => expect(c.hook.result.current.done).toBe(true));
  expect(c.invalidate).not.toHaveBeenCalled(); expect(c.client.getQueryData(["report", 5])).toBeUndefined();
});

it.each(["decision", "reopen"] as const)("does not install a wrong-run %s acknowledgment", async (action) => {
  const c = setup(action); act(() => c.hook.result.current.start()); await waitFor(() => expect(c.fetch).toHaveBeenCalledOnce());
  await act(async () => { c.finish(200, 6); }); await waitFor(() => expect(c.hook.result.current.done).toBe(true));
  expect(c.client.getQueryData(["report", 5])).toBeUndefined(); expect(c.invalidate).not.toHaveBeenCalledWith({ queryKey: ["stats"] });
});
