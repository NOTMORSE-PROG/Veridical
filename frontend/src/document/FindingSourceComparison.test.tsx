import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FlagOut } from "../api/types";
import { ME_QUERY_KEY } from "../auth/useAuth";
import type { EvidenceManifest, EvidenceReference } from "../report/evidenceTypes";
import { FindingSourceComparison } from "./FindingSourceComparison";

const snapshot = "a".repeat(64);
const source = "b".repeat(64);
const left = "c".repeat(64), right = "d".repeat(64);
const quote = "The same saved statement can occupy both recorded roles.";
const flag = { id: 12, check_run_id: 7, assessment_revision: 3, check_kind: "internal_agreement",
  agreement_pair: { intent_text: quote, intent_anchor: "page 2", outcome_text: quote, outcome_anchor: "page 2" },
  passage_pair: null, affects_readiness: false } as FlagOut;
const refs: EvidenceReference[] = [left, right].map((id, index) => ({ reference_id: id, flag_id: 12, criterion_id: null,
  role: index === 0 ? "agreement_intent" : "agreement_outcome", index, quote, anchor: "page 2",
  source_scope: "own_manuscript", source_state: "not_checked" }));
const manifest: EvidenceManifest = { snapshot_id: snapshot, assessment_revision: 3, llm_mode: "fake", references: refs };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
function placement(id: string) { return { reference_id: id, state: "page_only", page: 2, paragraph: null, boxes: [], reason: null }; }
function location(id: string, docx = false) { return { snapshot_id: snapshot, source_identity: source, source_format: docx ? "docx" : "pdf",
  placement: { ...placement(id), state: docx ? "paragraph_only" : "page_only", paragraph: docx ? 8 : null }, caption: `Qualified location ${id}` }; }
function page(id: string) {
  const base64 = id === left ? "AAAA" : "BBBB";
  return { snapshot_id: snapshot, placement: placement(id), image_data_url: `data:image/png;base64,${base64}`,
    image_sha256: id === left ? "709e80c88487a2411e1ee4dfb9f22a861492d20c4765150c0c794abd70f8147c" : "09c08a63fc2b11a50cf88eb6f6c062c727964a0c828808fe46c740af3a33897a", caption: `Qualified page ${id}` };
}

function setup(handler?: (url: URL, init?: RequestInit) => Promise<Response> | Response, value = flag) {
  const calls: { path: string; signal?: AbortSignal | null }[] = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ path: url.pathname, signal: init?.signal });
    if (handler) return handler(url, init);
    if (url.pathname.endsWith("/evidence")) return Promise.resolve(json(manifest));
    const id = url.pathname.includes(left) ? left : right;
    return Promise.resolve(json(url.pathname.endsWith("/location") ? location(id) : page(id)));
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(ME_QUERY_KEY, { id: 1 });
  const open = vi.fn();
  const ui = (f: FlagOut, current = true) => <QueryClientProvider client={client}><MemoryRouter><FindingSourceComparison flag={f} current={current} onSource={open} /></MemoryRouter></QueryClientProvider>;
  const view = render(ui(value));
  fireEvent.click(screen.getByRole("button", { name: "Show original-page previews" }));
  return { calls, client, open, view, ui };
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("finding source comparison generations", () => {
  it("keeps identical same-page roles distinct and serializes location and page work", async () => {
    let release!: (response: Response) => void;
    const control = setup(url => {
      if (url.pathname.endsWith("/evidence")) return json(manifest);
      const id = url.pathname.includes(left) ? left : right;
      if (url.pathname.endsWith(`${left}/page`)) return new Promise<Response>(resolve => { release = resolve; });
      return json(url.pathname.endsWith("/location") ? location(id) : page(id));
    });
    await waitFor(() => expect(release).toBeDefined());
    expect(control.calls.some(call => call.path.includes(right))).toBe(false);
    const sourceAction = screen.getByRole("button", { name: "Show statement A in manuscript" });
    sourceAction.focus();
    await act(async () => release(json(page(left))));
    expect(await screen.findAllByRole("img")).toHaveLength(2);
    expect(sourceAction).toHaveFocus();
    expect(control.calls.map(call => call.path.split("/").slice(-2).join("/"))).toEqual([
      "7/evidence", `${left}/location`, `${left}/page`, `${right}/location`, `${right}/page`,
    ]);
    expect(screen.getAllByText(quote)).toHaveLength(2);
    expect(screen.getByText(`Qualified page ${left}`)).toBeVisible();
    expect(screen.getByText(`Qualified page ${right}`)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Show statement B in manuscript" }));
    expect(control.open).toHaveBeenCalledWith("page 2", "result");
  });

  it.each([401, 403, 404, 409, 410])("retires both prior images when a shared read returns %s", async status => {
    let rejectRead = false;
    const control = setup(url => {
      if (url.pathname.endsWith("/evidence")) return json(manifest);
      const id = url.pathname.includes(left) ? left : right;
      if (rejectRead && id === right) return json({ error: { code: "conflict", message: "Changed comparison" } }, status);
      return json(url.pathname.endsWith("/location") ? location(id) : page(id));
    });
    expect(await screen.findAllByRole("img")).toHaveLength(2);
    rejectRead = true;
    await act(async () => { await control.client.refetchQueries({ queryKey: ["finding-source-pages"] }); });
    await waitFor(() => expect(screen.queryAllByRole("img")).toHaveLength(0));
    expect(screen.getByRole("button", { name: "Reload recorded evidence" })).toBeVisible();
    rejectRead = false;
    const before = control.calls.filter(call => call.path.endsWith("/page")).length;
    fireEvent.click(screen.getByRole("button", { name: "Reload recorded evidence" }));
    expect(await screen.findAllByRole("img")).toHaveLength(2);
    expect(control.calls.filter(call => call.path.endsWith("/page"))).toHaveLength(before + 2);
  });

  it("keeps an independently available side after an ordinary resource failure", async () => {
    setup(url => {
      if (url.pathname.endsWith("/evidence")) return json(manifest);
      const id = url.pathname.includes(left) ? left : right;
      if (id === right) return json({ error: { code: "unavailable", message: "Busy" } }, 503);
      return json(url.pathname.endsWith("/location") ? location(id) : page(id));
    });
    expect(await screen.findByRole("img")).toHaveAttribute("src", page(left).image_data_url);
    expect(screen.getByRole("button", { name: "Retry source page" })).toBeVisible();
  });

  it("uses the verified DOCX location caption without requesting a PDF image", async () => {
    const control = setup(url => {
      if (url.pathname.endsWith("/evidence")) return json(manifest);
      return json(location(url.pathname.includes(left) ? left : right, true));
    });
    expect(await screen.findByText(`Qualified location ${right}`)).toBeVisible();
    expect(control.calls.filter(call => call.path.endsWith("/page"))).toHaveLength(0);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("does not substitute a duplicate role reference", async () => {
    const control = setup(url => url.pathname.endsWith("/evidence") ? json({ ...manifest, references: [...refs, refs[0]] })
      : json(url.pathname.endsWith("/location") ? location(right) : page(right)));
    expect(await screen.findByRole("img")).toHaveAttribute("src", page(right).image_data_url);
    expect(control.calls.some(call => call.path.includes(left))).toBe(false);
  });

  it("aborts a retired authentication lifetime and rejects its delayed pixels", async () => {
    let release!: (response: Response) => void;
    let held = false;
    const control = setup(url => {
      if (url.pathname.endsWith("/evidence")) return json(manifest);
      if (!held) { held = true; return new Promise<Response>(resolve => { release = resolve; }); }
      return json({ error: { code: "not_found", message: "Not available" } }, 404);
    });
    await waitFor(() => expect(release).toBeDefined());
    const original = control.calls.find(call => call.path.endsWith("/location"))!;
    await act(async () => {
      control.client.removeQueries({ queryKey: ME_QUERY_KEY, exact: true });
      control.client.setQueryData(ME_QUERY_KEY, { id: 1 });
    });
    expect(original.signal?.aborted).toBe(true);
    await act(async () => release(json(location(left))));
    await waitFor(() => expect(control.client.isFetching()).toBe(0));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(control.calls.filter(call => call.path.endsWith("/page"))).toHaveLength(0);
  });

  it.each(["flag", "revision", "current", "unmount"])("retires a delayed location after %s changes without starting page work", async change => {
    let release!: (response: Response) => void;
    const control = setup(url => url.pathname.endsWith("/evidence") ? json(manifest)
      : new Promise<Response>(resolve => { release = resolve; }));
    await waitFor(() => expect(release).toBeDefined());
    const original = control.calls.find(call => call.path.endsWith("/location"))!;
    await act(async () => {
      if (change === "unmount") control.view.unmount();
      else control.view.rerender(control.ui({ ...flag,
        id: change === "flag" ? 99 : flag.id,
        assessment_revision: change === "revision" ? 4 : flag.assessment_revision }, change !== "current"));
    });
    expect(original.signal?.aborted).toBe(true);
    await act(async () => release(json(location(left))));
    await waitFor(() => expect(control.client.isFetching()).toBe(0));
    expect(screen.queryAllByRole("img")).toHaveLength(0);
    expect(control.calls.filter(call => call.path.endsWith("/page"))).toHaveLength(0);
  });

  it("shows bounded F7 text and requests only its own manuscript source", async () => {
    const reuseFlag = { ...flag, check_kind: "originality_reuse", agreement_pair: null, page_anchor: "page 2", passage_pair: {
      own_excerpt: quote, own_context_before: null, own_context_after: null, matched_ref: 90, matched_excerpt: "Bounded foreign text.",
      matched_context_before: "Retained context", matched_context_after: null, context_words_each_side: 10, similarity: 0.8, level: null,
    } } as FlagOut;
    const own = { ...refs[0], role: "reuse_own", index: 0 };
    const foreign = { ...refs[1], role: "reuse_match", index: 1, quote: "Bounded foreign text.", anchor: null, source_scope: "bounded_comparison_excerpt" };
    const control = setup(url => url.pathname.endsWith("/evidence") ? json({ ...manifest, references: [own, foreign] })
      : json(url.pathname.endsWith("/location") ? location(left) : page(left)), reuseFlag);
    expect(await screen.findByRole("img")).toBeVisible();
    expect(screen.getByText("Bounded foreign text.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Open in Library" })).toHaveAttribute("href", "/library/90");
    expect(control.calls.some(call => call.path.includes(right))).toBe(false);
  });
});
