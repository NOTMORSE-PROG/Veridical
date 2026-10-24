import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { type ReactNode, useRef } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { Button } from "../ui/Button";
import { useExportReportPdf } from "./useReport";
import { useReportPdfExportAction } from "./useReportPdfExportAction";

let requests: { resolve: (blob: Blob) => void; reject: (error: Error) => void }[];
let delivered: string[];
let getBlob: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  requests = []; delivered = [];
  getBlob = vi.spyOn(api, "getBlob").mockImplementation(() => new Promise<Blob>((resolve, reject) => requests.push({ resolve, reject })));
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = vi.fn(() => "blob:controlled-export");
    static revokeObjectURL = vi.fn();
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { delivered.push(this.download); });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function setup(owner: number | null = 1) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  if (owner !== null) client.setQueryData(ME_QUERY_KEY, { id: owner });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return { client, wrapper };
}
async function resolve(index = 0) { await act(async () => { requests[index].resolve(new Blob(["synthetic bytes"])); }); }
async function reject(index = 0) { await act(async () => { requests[index].reject(new Error("Controlled export failure")); }); }

it.each(["account", "relogin", "identity", "signedout"])("suppresses delayed delivery after %s replacement", async change => {
  const c = setup();
  const h = renderHook(() => useExportReportPdf(11), { wrapper: c.wrapper });
  act(() => h.result.current.mutate(h.result.current.createRequest()));
  await waitFor(() => expect(getBlob).toHaveBeenCalledOnce());
  h.unmount();
  if (change !== "identity") c.client.clear();
  c.client.setQueryData(ME_QUERY_KEY, change === "signedout" ? null : { id: change === "relogin" ? 1 : 2 });
  await resolve();
  expect(delivered).toEqual([]); expect(URL.createObjectURL).not.toHaveBeenCalled();
});

it("fails closed before a request without authenticated ownership", async () => {
  const c = setup(null);
  const h = renderHook(() => useExportReportPdf(11), { wrapper: c.wrapper });
  act(() => h.result.current.mutate(h.result.current.createRequest()));
  await waitFor(() => expect(h.result.current.isError).toBe(true));
  expect(getBlob).not.toHaveBeenCalled(); expect(delivered).toEqual([]);
});

it.each([false, true])("same-owner navigation preserves original run filename, unmount=%s", async unmount => {
  const c = setup();
  const h = renderHook(({ run }) => useExportReportPdf(run), { initialProps: { run: 11 }, wrapper: c.wrapper });
  act(() => h.result.current.mutate(h.result.current.createRequest()));
  await waitFor(() => expect(getBlob).toHaveBeenCalledWith("/check-runs/11/report/export.pdf"));
  h.rerender({ run: 22 }); if (unmount) h.unmount();
  await resolve();
  expect(delivered).toEqual(["veridical-report-11.pdf"]);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:controlled-export");
});

function Probe({ run }: { run: number }) {
  const error = useRef<HTMLDivElement>(null);
  const action = useReportPdfExportAction(run, error);
  return <><Button aria-busy={action.isPending || undefined} aria-disabled={action.isPending || undefined}
    onClick={event => action.start(event.currentTarget)}>{action.isPending ? "Preparing PDF" : "Export PDF"}</Button>
    <button>Another task</button>
    {action.isReady && <p role="status">PDF is ready.</p>}
    {action.isError && <div tabIndex={-1} ref={error} role="alert">Export failed</div>}</>;
}
function start() { const trigger = screen.getByRole("button", { name: "Export PDF" }); trigger.focus(); fireEvent.click(trigger); return trigger; }

it("keeps the real Button focusable and synchronously deduplicates pending activations", async () => {
  const c = setup(); render(<Probe run={11} />, { wrapper: c.wrapper });
  const trigger = start(); fireEvent.click(trigger); fireEvent.keyDown(trigger, { key: "Enter" }); fireEvent.click(trigger); fireEvent.keyDown(trigger, { key: " " }); fireEvent.click(trigger);
  expect(trigger).toHaveAttribute("aria-busy", "true"); expect(trigger).toHaveAttribute("aria-disabled", "true"); expect(trigger).not.toBeDisabled(); expect(trigger).toHaveFocus();
  await waitFor(() => expect(getBlob).toHaveBeenCalledOnce()); await resolve();
  await screen.findByText("PDF is ready."); expect(trigger).toHaveFocus(); expect(trigger).not.toHaveAttribute("aria-busy"); expect(trigger).not.toHaveAttribute("aria-disabled");
});

it("focuses an owned failure only after its actual error node commits and permits retry", async () => {
  const c = setup(); render(<Probe run={11} />, { wrapper: c.wrapper }); start();
  await waitFor(() => expect(getBlob).toHaveBeenCalledOnce()); await reject();
  await waitFor(() => expect(screen.getByRole("alert")).toHaveFocus());
  const trigger = start(); await waitFor(() => expect(getBlob).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument(); await resolve(1); await screen.findByText("PDF is ready."); expect(trigger).toHaveFocus();
});

it.each(["Tab", "pointer", "focus", "wheel", "blur"])("newer %s intent retires failure focus", async intent => {
  const c = setup(); render(<Probe run={11} />, { wrapper: c.wrapper }); const trigger = start();
  await waitFor(() => expect(getBlob).toHaveBeenCalledOnce());
  const other = screen.getByRole("button", { name: "Another task" });
  if (intent === "Tab") fireEvent.keyDown(trigger, { key: "Tab" });
  else if (intent === "pointer") fireEvent.pointerDown(other);
  else if (intent === "wheel") fireEvent.wheel(window);
  else if (intent === "blur") fireEvent.blur(window);
  if (intent === "focus" || intent === "Tab" || intent === "pointer") other.focus();
  const focused = document.activeElement; await reject(); await screen.findByRole("alert");
  expect(document.activeElement).toBe(focused); expect(screen.getByRole("alert")).not.toHaveFocus();
});

it("leaves newer task focus alone on success", async () => {
  const c = setup(); render(<Probe run={11} />, { wrapper: c.wrapper }); start();
  await waitFor(() => expect(getBlob).toHaveBeenCalledOnce()); const other = screen.getByRole("button", { name: "Another task" }); other.focus();
  await resolve(); await screen.findByText("PDF is ready."); expect(other).toHaveFocus();
});

it("a reused report retires old errors and old settlement cannot release the new attempt", async () => {
  const c = setup(); const view = render(<Probe run={11} />, { wrapper: c.wrapper }); start();
  await waitFor(() => expect(getBlob).toHaveBeenCalledOnce()); view.rerender(<Probe run={22} />); const trigger = start();
  await waitFor(() => expect(getBlob).toHaveBeenCalledTimes(2)); await reject(0);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument(); expect(trigger).toHaveAttribute("aria-busy", "true");
  fireEvent.click(trigger); await act(async () => {}); expect(getBlob).toHaveBeenCalledTimes(2);
  await resolve(1); await screen.findByText("PDF is ready."); expect(delivered).toEqual(["veridical-report-22.pdf"]);
});

it.each(["account", "relogin"])("replacement %s retires current UI error and pending state", async change => {
  const c = setup(); render(<Probe run={11} />, { wrapper: c.wrapper }); start();
  await waitFor(() => expect(getBlob).toHaveBeenCalledOnce());
  act(() => { c.client.clear(); c.client.setQueryData(ME_QUERY_KEY, { id: change === "relogin" ? 1 : 2 }); });
  await screen.findByRole("button", { name: "Export PDF" }); await reject();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument(); expect(screen.queryByText("PDF is ready.")).not.toBeInTheDocument();
});

it("unmount disposes focus listeners while same-owner delivery remains allowed", async () => {
  const c = setup(); const view = render(<Probe run={11} />, { wrapper: c.wrapper }); start();
  await waitFor(() => expect(getBlob).toHaveBeenCalledOnce()); const remove = vi.spyOn(document, "removeEventListener");
  view.unmount(); expect(remove).toHaveBeenCalledWith("focusin", expect.any(Function), true);
  await resolve(); expect(delivered).toEqual(["veridical-report-11.pdf"]);
});

it.each(["account", "relogin", "identity"])("rejects a stale action before React commits %s replacement", async change => {
  const c = setup(); const target = { current: null };
  const h = renderHook(() => useReportPdfExportAction(11, target), { wrapper: c.wrapper });
  const oldStart = h.result.current.start;
  const button = document.createElement("button");
  act(() => {
    if (change !== "identity") c.client.clear();
    c.client.setQueryData(ME_QUERY_KEY, { id: change === "relogin" ? 1 : 2 });
    oldStart(button);
  });
  await act(async () => {});
  expect(getBlob).not.toHaveBeenCalled(); expect(h.result.current.isPending).toBe(false);
});

it("a settled old failure is absent when the report parameter is reused", async () => {
  const c = setup(); const view = render(<Probe run={11} />, { wrapper: c.wrapper }); start();
  await waitFor(() => expect(getBlob).toHaveBeenCalledOnce()); await reject(); await screen.findByRole("alert");
  view.rerender(<Probe run={22} />);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument(); expect(screen.getByRole("button", { name: "Export PDF" })).not.toHaveAttribute("aria-busy");
});
