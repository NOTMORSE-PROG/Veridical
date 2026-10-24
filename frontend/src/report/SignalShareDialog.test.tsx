import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { SignalShareDialog } from "./SignalShareDialog";

const LINK = { check_run_id: 11, token: "synthetic-not-a-capability", created_at: "2026-10-24T00:00:00Z", expires_at: null };
function setup(initial: typeof LINK | null | undefined, read: () => Promise<unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(ME_QUERY_KEY, { id: 1 });
  if (initial !== undefined) client.setQueryData(["share", 11], initial);
  vi.spyOn(api, "get").mockImplementation(read as typeof api.get);
  const close = vi.fn();
  const view = render(<QueryClientProvider client={client}><SignalShareDialog checkRunId={11} manuscriptLabel="Synthetic manuscript" onClose={close} /></QueryClientProvider>);
  return { client, view, close };
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("confirmed absence uses prospective sharing copy", async () => {
  setup(undefined, async () => null);
  await screen.findByText("No active link.");
  expect(screen.getByText(/Creating a link will share Synthetic manuscript/)).toBeVisible();
  expect(screen.queryByText(/Anyone with the link can view/)).not.toBeInTheDocument();
});
it("an ordinary active read does not announce creation", async () => {
  setup(undefined, async () => LINK);
  await screen.findByText("Active read-only link.");
  expect(screen.queryByText("Share link created.")).not.toBeInTheDocument();
});
it.each([undefined, null, LINK])("withholds capabilities and creation on failed read with cached=%s", async initial => {
  setup(initial, async () => { throw new Error("Controlled status failure"); });
  await screen.findByRole("button", { name: "Retry link status" });
  expect(screen.queryByRole("button", { name: "Create read-only link" })).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "Report share link" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Revoke link" })).not.toBeInTheDocument();
});
it("confirmed revocation changes both access copy and expiry form", async () => {
  setup(undefined, async () => LINK); vi.spyOn(api, "delete").mockImplementation(async () => { vi.mocked(api.get).mockResolvedValue(null); return { ok: true } as never; });
  fireEvent.click(await screen.findByRole("button", { name: "Revoke link" }));
  fireEvent.click(screen.getByRole("button", { name: "Revoke link" }));
  await screen.findByText("Share link revoked."); await screen.findByText("No active link.");
  expect(screen.queryByText(/This link opens/)).not.toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Link expires" })).toHaveValue("none");
});
it("a failed mutation requires authoritative retry and preserves the expiry draft", async () => {
  const get = vi.fn(async () => null); setup(undefined, get);
  vi.spyOn(api, "post").mockRejectedValue(new Error("Controlled response lost"));
  const create = await screen.findByRole("button", { name: "Create read-only link" });
  fireEvent.change(screen.getByRole("combobox", { name: "Link expires" }), { target: { value: "7d" } });
  fireEvent.click(create);
  await screen.findByText("Could not confirm the change.");
  expect(screen.queryByRole("button", { name: "Create read-only link" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry link status" }));
  await screen.findByRole("button", { name: "Create read-only link" });
  expect(get).toHaveBeenCalledTimes(2); expect(screen.getByRole("combobox", { name: "Link expires" })).toHaveValue("7d");
});
it("never shows cached link actions while status is refreshing", async () => {
  let finish!: (value: unknown) => void; setup(LINK, () => new Promise(resolve => { finish = resolve; }));
  await screen.findByText("Updating link status…");
  expect(screen.queryByRole("button", { name: "Copy link" })).not.toBeInTheDocument();
  await act(async () => { finish(LINK); }); await screen.findByRole("button", { name: "Copy link" });
});
it("resets an open destructive confirmation across a report change", async () => {
  const c = setup(undefined, async () => LINK);
  fireEvent.click(await screen.findByRole("button", { name: "Revoke link" }));
  await screen.findByText(/Revoke this link now/);
  c.client.setQueryData(["share", 22], { ...LINK, check_run_id: 22 });
  vi.mocked(api.get).mockResolvedValue({ ...LINK, check_run_id: 22 });
  c.view.rerender(<QueryClientProvider client={c.client}><SignalShareDialog checkRunId={22} manuscriptLabel="Other synthetic" onClose={c.close} /></QueryClientProvider>);
  await screen.findByRole("button", { name: "Revoke link" });
  expect(screen.queryByText(/Revoke this link now/)).not.toBeInTheDocument();
  expect(screen.getByText(/This link opens Other synthetic/)).toBeVisible();
});

it.each([false, true])("creation focuses the new URL only with retained intent, retired=%s", async retired => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  setup(undefined, async () => null); let finish!: (value: never) => void;
  vi.spyOn(api, "post").mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const trigger = await screen.findByRole("button", { name: "Create read-only link" });
  trigger.focus(); fireEvent.click(trigger); await waitFor(() => expect(api.post).toHaveBeenCalledOnce());
  if (retired) fireEvent.wheel(window);
  await act(async () => { vi.mocked(api.get).mockResolvedValue(LINK); finish(LINK as never); });
  const url = await screen.findByRole("textbox", { name: "Report share link" });
  if (retired) expect(url).not.toHaveFocus(); else await waitFor(() => expect(url).toHaveFocus());
  expect(screen.getByText("Share link created.")).toBeVisible();
});

it("unmount disposes an in-flight creation focus claim", async () => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const c = setup(undefined, async () => null); let finish!: (value: never) => void;
  vi.spyOn(api, "post").mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const trigger = await screen.findByRole("button", { name: "Create read-only link" }); trigger.focus(); fireEvent.click(trigger);
  await waitFor(() => expect(api.post).toHaveBeenCalledOnce());
  const remove = vi.spyOn(document, "removeEventListener"); c.view.unmount();
  expect(remove).toHaveBeenCalledWith("focusin", expect.any(Function), true);
  await act(async () => { vi.mocked(api.get).mockResolvedValue(LINK); finish(LINK as never); });
  await waitFor(() => expect(c.client.getQueryData(["share", 11])).toEqual(LINK));
});

it("old clipboard completion does not announce a copy in the replacement report", async () => {
  let finish!: () => void; const writeText = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  const c = setup(undefined, async () => LINK);
  fireEvent.click(await screen.findByRole("button", { name: "Copy link" }));
  expect(writeText).toHaveBeenCalledOnce();
  vi.mocked(api.get).mockResolvedValue({ ...LINK, check_run_id: 22 });
  c.view.rerender(<QueryClientProvider client={c.client}><SignalShareDialog checkRunId={22} manuscriptLabel="Other synthetic" onClose={c.close} /></QueryClientProvider>);
  await screen.findByText(/This link opens Other synthetic/); await act(async () => { finish(); });
  expect(screen.queryByText("Link copied to clipboard.")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Copied" })).not.toBeInTheDocument();
});

it.each(["account", "relogin"])("authentication %s resets expiry and confirmation before new content", async change => {
  const c = setup(undefined, async () => LINK);
  fireEvent.change(await screen.findByRole("combobox", { name: "Replacement link expiry" }), { target: { value: "7d" } });
  fireEvent.click(screen.getByRole("button", { name: "Revoke link" }));
  act(() => { c.client.clear(); c.client.setQueryData(ME_QUERY_KEY, { id: change === "relogin" ? 1 : 2 }); });
  const expiry = await screen.findByRole("combobox", { name: "Replacement link expiry" });
  expect(expiry).toHaveValue("current"); expect(screen.queryByText(/Revoke this link now/)).not.toBeInTheDocument();
});

it("a successful read retains the reader's current dialog focus", async () => {
  let finish!: (value: unknown) => void; setup(undefined, () => new Promise(resolve => { finish = resolve; }));
  const close = screen.getByRole("button", { name: "Close" }); close.focus();
  await act(async () => { finish(LINK); }); await screen.findByRole("textbox", { name: "Report share link" });
  expect(close).toHaveFocus(); expect(screen.queryByText("Share link created.")).not.toBeInTheDocument();
});

it.each([{ check_run_id: 11 }, { ...LINK, token: " " }, { ...LINK, created_at: "invalid" },
  { ...LINK, expires_at: "invalid" }])("a malformed GET %# stays recoverable and cannot enable capability actions", async value => {
    setup(undefined, async () => value);
    await screen.findByRole("button", { name: "Retry link status" });
    expect(screen.getByText("Could not check link status.")).toBeVisible();
    expect(screen.queryByRole("textbox", { name: "Report share link" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create read-only link" })).not.toBeInTheDocument();
  });

it.each([null, { ...LINK, token: "replacement-synthetic-value" }])("retires confirmation after the same run's token changes to %s", async next => {
  const c = setup(undefined, async () => LINK); const revoke = vi.spyOn(api, "delete");
  fireEvent.click(await screen.findByRole("button", { name: "Revoke link" }));
  await screen.findByText(/Revoke this link now/);
  vi.mocked(api.get).mockResolvedValue(next);
  await act(async () => { await c.client.invalidateQueries({ queryKey: ["share", 11] }); });
  await screen.findByText(next ? "Active read-only link." : "No active link.");
  expect(screen.queryByText(/Revoke this link now/)).not.toBeInTheDocument(); expect(revoke).not.toHaveBeenCalled();
});

it.each([false, true])("keyboard Retry owns only its resulting status, retired=%s", async retired => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const c = setup(undefined, async () => { throw new Error("Controlled read failure"); });
  const retry = await screen.findByRole("button", { name: "Retry link status" }); let finish!: (value: never) => void;
  vi.mocked(api.get).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  retry.focus(); fireEvent.click(retry); await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
  if (retired) fireEvent.wheel(window);
  await act(async () => { finish(null as never); }); await screen.findByText("No active link.");
  const status = screen.getByRole("status");
  if (retired) expect(status).not.toHaveFocus(); else await waitFor(() => expect(status).toHaveFocus());
  expect(c.client.getQueryData(["share", 11])).toBeNull();
});

it("a create acknowledgment with failed status reconciliation stays uncertain until a real retry", async () => {
  setup(undefined, async () => null);
  const create = await screen.findByRole("button", { name: "Create read-only link" });
  vi.spyOn(api, "post").mockResolvedValue(LINK);
  vi.mocked(api.get).mockRejectedValue(new Error("Controlled reconciliation failure"));
  fireEvent.click(create); await screen.findByText("Could not confirm the change.");
  expect(screen.queryByText("Share link created.")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Create read-only link" })).not.toBeInTheDocument();
  vi.mocked(api.get).mockResolvedValue(LINK);
  fireEvent.click(screen.getByRole("button", { name: "Retry link status" }));
  await screen.findByText("Active read-only link."); expect(api.post).toHaveBeenCalledOnce();
  expect(screen.queryByText("Share link created.")).not.toBeInTheDocument();
});
