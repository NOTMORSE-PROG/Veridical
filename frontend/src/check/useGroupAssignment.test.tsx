import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../api/client";
import { ME_QUERY_KEY } from "../auth/useAuth";
import type { GroupAssignmentSubmission, GroupSelectionOutput } from "../api/groupAssignmentOutcome";
import { useGroupAssignment } from "./useGroupAssignment";

const saved = { id: 7, name: "Practice team", member_names: ["Maria"], program_id: null, program: null, title: null, fingerprint: "a".repeat(64) };
const submission: GroupAssignmentSubmission = { kind: "saved", body: { saved_group_id: 7, fingerprint: saved.fingerprint, expected_group_id: 3 }, reviewed: saved };
const response = (): GroupSelectionOutput => ({ action: "select_saved_group", manuscript_id: 9, outcome: "selected", receipt: {
  version: 2, manuscript_id: 9, instructor_id: 4, assigned_group_id: 7, request: { ...submission.body }, effective: structuredClone(saved),
} });
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(ME_QUERY_KEY, { id: 4 });
  const get = vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path === "/auth/session") return { instructor: { id: 4 } } as never;
    if (path === "/manuscripts/9/group") {
      const { fingerprint: _fingerprint, ...group } = saved;
      return { manuscript_id: 9, group, confirmation: "recorded" } as never;
    }
    throw new Error("Unexpected GET: " + path);
  });
  const post = vi.spyOn(api, "post").mockResolvedValue(response());
  const patch = vi.spyOn(api, "patch").mockResolvedValue({ group_id: 7, group_label: "Practice team", program: null, title: null, matched: false, outcome: "created" });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const hook = renderHook(({ id }) => useGroupAssignment(id), { wrapper, initialProps: { id: 9 } });
  return { ...hook, client, get, post, patch };
}
afterEach(() => vi.restoreAllMocks());

describe("group assignment form lifetime and unknown results", () => {
  it("accepts the reviewed receipt only after the completion session check", async () => {
    const control = mount();
    await waitFor(() => expect(control.result.current.scope.status).toBe("ready"));
    await act(() => control.result.current.submit(submission));
    expect(control.post).toHaveBeenCalledExactlyOnceWith("/manuscripts/9/group-selection", submission.body);
    expect(control.result.current.state.kind).toBe("saved");
    expect(control.get.mock.calls.filter(([path]) => path === "/auth/session")).toHaveLength(3);
  });
  it("keeps unknown attempts frozen and readback makes no POST or success claim", async () => {
    const control = mount();
    control.post.mockRejectedValue(new TypeError("Connection lost after commit"));
    await waitFor(() => expect(control.result.current.scope.status).toBe("ready"));
    await act(() => control.result.current.submit(submission));
    expect(control.result.current.state.kind).toBe("unknown");
    expect(control.result.current.locked).toBe(true);
    await act(() => control.result.current.submit(submission));
    expect(control.post).toHaveBeenCalledTimes(1);
    await act(() => control.result.current.checkSavedAssignment());
    const state = control.result.current.state;
    expect(state.kind).toBe("unknown");
    if (state.kind === "unknown") expect(state.current?.group?.id).toBe(7);
    expect(control.post).toHaveBeenCalledTimes(1);
    let current;
    act(() => { current = control.result.current.continueAfterReadback(); });
    expect(current).toMatchObject({ manuscript_id: 9, group: { id: 7 } });
    expect(control.result.current.state.kind).toBe("idle");
    expect(control.post).toHaveBeenCalledTimes(1);
  });
  it("does not enable another attempt after a failed or wrong-manuscript readback", async () => {
    const control = mount();
    control.post.mockRejectedValue(new Error("Response lost"));
    await waitFor(() => expect(control.result.current.scope.status).toBe("ready"));
    await act(() => control.result.current.submit(submission));
    control.get.mockImplementation(async (path) => path === "/auth/session" ? { instructor: { id: 4 } } as never : { manuscript_id: 10, group: null, confirmation: "recorded" } as never);
    await act(() => control.result.current.checkSavedAssignment());
    expect(control.result.current.state).toMatchObject({ kind: "unknown", readError: expect.any(String) });
    act(() => expect(control.result.current.continueAfterReadback()).toBeUndefined());
    expect(control.result.current.locked).toBe(true);
  });
  it("preserves a known stale-details refusal and permits deliberate correction", async () => {
    const control = mount();
    control.post.mockRejectedValueOnce(new ApiError(409, "saved_group_changed", "Saved details changed"));
    await waitFor(() => expect(control.result.current.scope.status).toBe("ready"));
    await act(() => control.result.current.submit(submission));
    expect(control.result.current.state).toEqual({ kind: "rejected", message: "Saved details changed", recovery: "saved_details" });
    expect(control.result.current.locked).toBe(false);
    await act(() => control.result.current.submit(submission));
    expect(control.post).toHaveBeenCalledTimes(1);
    act(() => control.result.current.markRecoveryReviewed("saved_details"));
    await act(() => control.result.current.submit(submission));
    expect(control.result.current.state.kind).toBe("saved");
  });
  it("malformed success never becomes a saved assignment", async () => {
    const control = mount();
    control.post.mockResolvedValue({ ...response(), manuscript_id: 10 });
    await waitFor(() => expect(control.result.current.scope.status).toBe("ready"));
    await act(() => control.result.current.submit(submission));
    expect(control.result.current.state.kind).toBe("unknown");
  });
  it("retires late acknowledgements after an account change", async () => {
    const control = mount();
    const pending = deferred<unknown>();
    control.post.mockReturnValue(pending.promise);
    const invalidation = vi.spyOn(control.client, "invalidateQueries");
    await waitFor(() => expect(control.result.current.scope.status).toBe("ready"));
    let saving!: Promise<void>;
    act(() => { saving = control.result.current.submit(submission); });
    await waitFor(() => expect(control.post).toHaveBeenCalledTimes(1));
    act(() => control.client.setQueryData(ME_QUERY_KEY, { id: 5 }));
    await act(async () => { pending.resolve(response()); await saving; });
    expect(control.result.current.scope.status).toBe("lost");
    expect(control.result.current.state.kind).not.toBe("saved");
    expect(invalidation).not.toHaveBeenCalled();
  });
  it("retires a mounted form when its manuscript changes", async () => {
    const control = mount();
    const pending = deferred<unknown>();
    control.post.mockReturnValue(pending.promise);
    await waitFor(() => expect(control.result.current.scope.status).toBe("ready"));
    let saving!: Promise<void>;
    act(() => { saving = control.result.current.submit(submission); });
    await waitFor(() => expect(control.post).toHaveBeenCalledTimes(1));
    control.rerender({ id: 10 });
    await act(async () => { pending.resolve(response()); await saving; });
    expect(control.result.current.scope.status).toBe("lost");
    expect(control.result.current.state.kind).not.toBe("saved");
  });
  it("freezes a proposal creation after a lost response too", async () => {
    const control = mount();
    control.patch.mockRejectedValue(new Error("Response lost after new group created"));
    await waitFor(() => expect(control.result.current.scope.status).toBe("ready"));
    const proposal: GroupAssignmentSubmission = { kind: "proposal", body: { group_name: "New team", member_names: [], program_id: null, title: null, expected_group_id: 3 } };
    await act(() => control.result.current.submit(proposal));
    await act(() => control.result.current.submit(proposal));
    expect(control.patch).toHaveBeenCalledTimes(1);
    expect(control.result.current.state.kind).toBe("unknown");
  });
  it("captures submitted facts before awaiting a session check and blocks double clicks", async () => {
    const control = mount();
    await waitFor(() => expect(control.result.current.scope.status).toBe("ready"));
    const draft = structuredClone(submission);
    let saving!: Promise<void>;
    act(() => { saving = control.result.current.submit(draft); void control.result.current.submit(draft); });
    draft.body.saved_group_id = 8;
    draft.reviewed.name = "Changed after click";
    await act(async () => { await saving; });
    expect(control.post).toHaveBeenCalledExactlyOnceWith("/manuscripts/9/group-selection", submission.body);
    expect(control.result.current.state.kind).toBe("saved");
  });
});
