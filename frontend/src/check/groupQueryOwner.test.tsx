import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, renderHook, screen, fireEvent, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../api/client";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { captureAuthOwner } from "../auth/queryOwner";
import { GroupAssignmentFields } from "./GroupAssignmentFields";
import { groupOwnerKey, ownedGroupQueryKey, readOwnedGroup } from "./groupQueryOwner";
import { useGroupProposal, useManuscriptGroup } from "./useCheckRun";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function fixture() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(ME_QUERY_KEY, { id: 4 });
  const owner = captureAuthOwner(client);
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return { client, owner, wrapper };
}
afterEach(() => vi.restoreAllMocks());

describe("group read ownership", () => {
  it("does not display prior-owner cached team names while the verified new owner's list is pending", async () => {
    const { client, owner, wrapper: Wrapper } = fixture();
    const oldGroups = [{ id: 7, name: "PRIVATE OLD ACTOR TEAM", program: "Private program" }];
    client.setQueryData(["groups"], oldGroups);
    client.setQueryData(ownedGroupQueryKey("groups", owner), oldGroups);
    client.setQueryData(ME_QUERY_KEY, { id: 5 }); // Same auth query; no explicit logout/cache clear.
    const list = deferred<unknown>();
    vi.spyOn(api, "get").mockImplementation(async (path) => {
      if (path === "/auth/session") return { instructor: { id: 5 } } as never;
      if (path === "/manuscripts/19/group") return { manuscript_id: 19, group: null, confirmation: "not_recorded" } as never;
      if (path === "/programs") return [] as never;
      if (path === "/groups") return list.promise as never;
      throw new Error("Unexpected read: " + path);
    });
    render(<Wrapper><GroupAssignmentFields manuscriptId={19} onDone={() => {}} /></Wrapper>);
    const choice = await screen.findByRole("radio", { name: "Use a saved group" });
    await waitFor(() => expect(choice).toBeEnabled()); fireEvent.click(choice);
    await screen.findByText("Loading saved groups.");
    expect(screen.queryByText("PRIVATE OLD ACTOR TEAM")).not.toBeInTheDocument();
    expect(screen.queryByText("Private program")).not.toBeInTheDocument();
    await act(async () => list.resolve([{ id: 9, name: "Current owner's team", program: null }]));
    expect(await screen.findByRole("radio", { name: /Current owner's team/ })).toBeInTheDocument();
    expect(screen.queryByText("PRIVATE OLD ACTOR TEAM")).not.toBeInTheDocument();
  });
  it.each(["current", "proposal"] as const)("never displays prior-owner %s residue while authorization refusal is pending", async kind => {
    const { client, owner, wrapper } = fixture();
    const privateValue = kind === "current"
      ? { manuscript_id: 19, group: { id: 7, name: "Private owner A", member_names: [], program_id: null, program: null, title: null }, confirmation: "recorded" }
      : { title: { value: "Private manuscript title", anchor: "paragraph 1" }, short_name: null, members: [], program: null, adviser: null, extraction_failed: false };
    const prefix = kind === "current" ? "manuscript-group" : "group-proposal";
    client.setQueryData([prefix, 19], privateValue);
    client.setQueryData(ownedGroupQueryKey(prefix, owner, 19), privateValue);
    client.setQueryData(ME_QUERY_KEY, { id: 5 });
    const pending = deferred<unknown>();
    const get = vi.spyOn(api, "get").mockReturnValue(pending.promise);
    const hook = renderHook(() => {
      const query = kind === "current" ? useManuscriptGroup(19) : useGroupProposal(19);
      return { data: query.data, isError: query.isError };
    }, { wrapper });
    expect(hook.result.current.data).toBeUndefined();
    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    await act(async () => pending.reject(new ApiError(404, "not_found", "Not found")));
    await waitFor(() => expect(hook.result.current.isError).toBe(true));
    expect(hook.result.current.data).toBeUndefined();
  });
  it("a replacement authentication query creates a new cache lifetime even for the same actor", async () => {
    const { client, owner, wrapper } = fixture();
    const originalKey = groupOwnerKey(owner);
    client.setQueryData(ownedGroupQueryKey("manuscript-group", owner, 19), { private: "Old lifetime" });
    client.removeQueries({ queryKey: ME_QUERY_KEY, exact: true });
    client.setQueryData(ME_QUERY_KEY, { id: 4 });
    const replacement = captureAuthOwner(client);
    expect(groupOwnerKey(replacement)[0]).toBe(originalKey[0]);
    expect(groupOwnerKey(replacement)[1]).not.toBe(originalKey[1]);
    const pending = deferred<unknown>();
    vi.spyOn(api, "get").mockReturnValue(pending.promise);
    const hook = renderHook(() => useManuscriptGroup(19), { wrapper });
    expect(hook.result.current.data).toBeUndefined();
    await act(async () => pending.resolve({ manuscript_id: 19, group: null, confirmation: "not_recorded" }));
    await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
    expect(hook.result.current.data?.group).toBeNull();
  });
  it("retires a read that completes after its actor changes without adopting old private facts", async () => {
    const { client, owner } = fixture();
    const pending = deferred<string>();
    const result = readOwnedGroup(owner, () => pending.promise);
    client.setQueryData(ME_QUERY_KEY, { id: 5 });
    pending.resolve("Old private facts");
    await expect(result).rejects.toThrow("Instructor access changed");
  });
});
