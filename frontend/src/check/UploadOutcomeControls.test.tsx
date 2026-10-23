import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode, type ReactElement } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { UploadRubricModal } from "../rubric/UploadRubricModal";
import { ingestReceipt, rubricReceipt } from "../test/uploadReceipts";
import { UploadManuscriptModal } from "./UploadManuscriptModal";

const instructor = (id: number) => ({ id, email: `actor${id}@example.test`, display_name: "Instructor", onboarding_dismissed_at: null });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
function setup(ui: ReactElement, post: (init: RequestInit) => Promise<Response>, session: () => Promise<Response> = async () => json({ instructor: instructor(1) }), strict = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(ME_QUERY_KEY, instructor(1));
  const posts = vi.fn(post);
  const gets = vi.fn(session);
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    if (new URL(String(input), "http://localhost").pathname.endsWith("/auth/session")) return gets();
    if (init?.method === "POST") return posts(init);
    throw new Error(`Unexpected request ${String(input)}`);
  });
  const router = createMemoryRouter([{ path: "*", element: ui }], { initialEntries: ["/"] });
  const tree = <QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>;
  const view = render(strict ? <StrictMode>{tree}</StrictMode> : tree);
  return { client, posts, gets, router, ...view };
}
async function choose() {
  const input = await screen.findByLabelText<HTMLInputElement>("Choose file");
  await waitFor(() => expect(input).not.toBeDisabled());
  const file = new File(["document"], "private.docx");
  fireEvent.change(input, { target: { files: [file] } });
  return { input, file };
}
afterEach(() => vi.unstubAllGlobals());

describe.each(["manuscript", "format"] as const)("%s upload uncertainty", (kind) => {
  const initial = kind === "manuscript" ? "Upload manuscript" : "Continue to review";
  const confirm = kind === "manuscript" ? "Upload a new copy" : "Upload a new draft";
  const receipt = kind === "manuscript" ? ingestReceipt : rubricReceipt;
  const modal = (success = vi.fn()) => kind === "manuscript"
    ? <UploadManuscriptModal onClose={vi.fn()} onUploadSuccess={success} />
    : <UploadRubricModal onClose={vi.fn()} />;

  it("StrictMode retires the first effect's session read and becomes usable from the replayed setup", async () => {
    const first = deferred<Response>();
    const second = deferred<Response>();
    let reads = 0;
    const out = setup(modal(), async () => json(receipt), () => ++reads === 1 ? first.promise : second.promise, true);
    await waitFor(() => expect(reads).toBe(2));
    await act(async () => { second.resolve(json({ instructor: instructor(1) })); });
    await choose();
    await act(async () => { first.resolve(json({ instructor: instructor(2) })); });
    expect(screen.getByText("private.docx")).toBeVisible();
    expect(out.client.getQueryData<{ id: number }>(ME_QUERY_KEY)?.id).toBe(1);
    expect(out.posts).not.toHaveBeenCalled();
  });

  it("retains unknown results across a new busy response and a later confirmed result; no automatic POST", async () => {
    const post = vi.fn().mockRejectedValueOnce(new TypeError("response lost"))
      .mockResolvedValueOnce(json({ error: { code: "document_preparation_busy", message: "This attempt did not add a document." } }, 503))
      .mockResolvedValueOnce(json(receipt));
    const out = setup(modal(), post);
    const { input, file } = await choose();
    fireEvent.click(screen.getByRole("button", { name: initial }));
    await screen.findByRole("region", { name: "Unconfirmed upload results" });
    expect(input).not.toHaveAttribute("aria-invalid");
    expect(input.files?.[0]).toBe(file);
    expect(out.posts).toHaveBeenCalledTimes(1);
    const inspection = screen.getByRole("link", { name: /opens in new tab/ });
    expect(inspection).toHaveAttribute("target", "_blank");
    expect(inspection).toHaveAttribute("rel", "noopener noreferrer");
    expect(inspection).toHaveAttribute("href", kind === "format" ? "/rubric" : "/dashboard?queue=not_checked&sort=newest&page=1");
    fireEvent.click(screen.getByRole("button", { name: "Start another upload attempt" }));
    expect(out.posts).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { name: "Confirm a new upload request" })).toHaveFocus();
    expect(screen.getByText(/New request: private.docx/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Keep reviewing" }));
    expect(screen.getByRole("button", { name: "Start another upload attempt" })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Start another upload attempt" }));
    fireEvent.click(screen.getByRole("button", { name: confirm }));
    await screen.findByText("This attempt did not add a document.");
    expect(screen.getByRole("region", { name: "Unconfirmed upload results" })).toBeInTheDocument();
    expect(out.posts).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Start another upload attempt" }));
    fireEvent.click(screen.getByRole("button", { name: confirm }));
    await screen.findByRole("heading", { name: kind === "format" ? "Latest draft confirmed" : "Upload complete" });
    expect(screen.getByRole("heading", { name: kind === "format" ? "Latest draft confirmed" : "Upload complete" })).toHaveFocus();
    expect(out.posts).toHaveBeenCalledTimes(3);
    expect(out.router.state.location.pathname).toBe("/");
    expect(screen.getByText(/earlier upload results remain unconfirmed|One or more upload results remain unconfirmed/)).toBeInTheDocument();
    for (const [init] of out.posts.mock.calls) {
      expect(init.headers).toEqual({ "X-Veridical-Expected-Instructor": "1" });
      expect((init.body as FormData).get("file")).toBe(file);
    }
  });

  it("rejects an unusable 2xx without a receipt, callback or guessed navigation", async () => {
    const success = vi.fn();
    const out = setup(modal(success), async () => json({ id: 99, manuscript_id: 99 }));
    await choose();
    fireEvent.click(screen.getByRole("button", { name: initial }));
    await screen.findByRole("region", { name: "Unconfirmed upload results" });
    expect(success).not.toHaveBeenCalled();
    expect(out.router.state.location.pathname).toBe("/");
    expect(screen.queryByRole("button", { name: /Start a check with|Review latest draft/ })).not.toBeInTheDocument();
  });

  it("blocks a cookie actor change detected immediately before POST and clears private context", async () => {
    let actor = 1;
    const out = setup(modal(), async () => json(receipt), async () => json({ instructor: instructor(actor) }));
    await choose();
    actor = 2;
    fireEvent.click(screen.getByRole("button", { name: initial }));
    await screen.findByText(/Your sign-in context changed/);
    expect(out.posts).not.toHaveBeenCalled();
    expect(screen.queryByText("private.docx")).not.toBeInTheDocument();
    expect(out.client.getQueryData<{ id: number }>(ME_QUERY_KEY)?.id).toBe(2);
  });

  it("a confirmed prebusiness actor conflict clears the old File without replay", async () => {
    const out = setup(modal(), async () => json({ error: { code: "actor_context_changed", message: "Account changed." } }, 409));
    await choose();
    fireEvent.click(screen.getByRole("button", { name: initial }));
    await screen.findByText(/Your sign-in context changed/);
    expect(screen.queryByText("private.docx")).not.toBeInTheDocument();
    expect(out.posts).toHaveBeenCalledTimes(1);
  });

  it("withholds a late success after cache clear and same-account reauthentication", async () => {
    const pending = deferred<Response>();
    const success = vi.fn();
    const out = setup(modal(success), () => pending.promise);
    await choose();
    fireEvent.click(screen.getByRole("button", { name: initial }));
    await waitFor(() => expect(out.posts).toHaveBeenCalledTimes(1));
    act(() => { out.client.clear(); out.client.setQueryData(ME_QUERY_KEY, instructor(1)); });
    await act(async () => { pending.resolve(json(receipt)); await pending.promise; });
    await screen.findByText(/Your sign-in context changed/);
    expect(success).not.toHaveBeenCalled();
    expect(screen.queryByText("private.docx")).not.toBeInTheDocument();
    expect(screen.queryByText("Upload complete")).not.toBeInTheDocument();
    expect(out.router.state.location.pathname).toBe("/");
  });

  it("returning from inspection hides context until a fresh session check, and never submits", async () => {
    let pending: ReturnType<typeof deferred<Response>> | undefined;
    const out = setup(modal(), async () => json(receipt), async () => pending ? pending.promise : json({ instructor: instructor(1) }));
    await choose();
    pending = deferred<Response>();
    fireEvent.focus(window);
    await screen.findByText("Checking your sign-in status.");
    expect(screen.queryByRole("button", { name: initial })).not.toBeInTheDocument();
    expect(out.posts).not.toHaveBeenCalled();
    await act(async () => { pending!.resolve(json({ instructor: instructor(1) })); });
    expect(await screen.findByText("private.docx")).toBeInTheDocument();
    expect(out.posts).not.toHaveBeenCalled();
  });

  it("overlapping post-request verification and inspection focus cannot erase an unknown result", async () => {
    const checking = deferred<Response>();
    let reads = 0;
    const out = setup(modal(), async () => { throw new TypeError("response lost after server work"); }, async () => {
      reads += 1;
      return reads >= 3 ? checking.promise : json({ instructor: instructor(1) });
    });
    await choose();
    fireEvent.click(screen.getByRole("button", { name: initial }));
    await waitFor(() => expect(reads).toBe(3));
    fireEvent.focus(window);
    fireEvent(document, new Event("visibilitychange"));
    expect(reads).toBe(3);
    await act(async () => { checking.resolve(json({ instructor: instructor(1) })); });
    expect(await screen.findByRole("region", { name: "Unconfirmed upload results" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start another upload attempt" })).toBeInTheDocument();
    expect(out.posts).toHaveBeenCalledTimes(1);
  });

  it("a focus check begun during POST cannot replace fresh verification after its response", async () => {
    const post = deferred<Response>();
    const focusCheck = deferred<Response>();
    let reads = 0;
    const out = setup(modal(), () => post.promise, async () => {
      reads += 1;
      if (reads === 3) return focusCheck.promise;
      return json({ instructor: instructor(reads >= 4 ? 2 : 1) });
    });
    await choose();
    fireEvent.click(screen.getByRole("button", { name: initial }));
    await waitFor(() => expect(out.posts).toHaveBeenCalledTimes(1));
    fireEvent.focus(window);
    await waitFor(() => expect(reads).toBe(3));
    await act(async () => { post.resolve(json(receipt)); });
    expect(reads).toBe(3);
    await act(async () => { focusCheck.resolve(json({ instructor: instructor(1) })); });
    await screen.findByText(/Your sign-in context changed/);
    expect(reads).toBe(4);
    expect(out.router.state.location.pathname).toBe("/");
    expect(screen.queryByText("private.docx")).not.toBeInTheDocument();
  });

  it("preflight session failure sends no upload; explicit recovery does not replay it", async () => {
    let reads = 0;
    const out = setup(modal(), async () => json(receipt), async () => {
      reads += 1;
      if (reads === 2) throw new TypeError("session read unavailable");
      return json({ instructor: instructor(1) });
    });
    await choose();
    fireEvent.click(screen.getByRole("button", { name: initial }));
    const check = await screen.findByRole("button", { name: "Check sign-in status" });
    expect(screen.queryByText("private.docx")).not.toBeVisible();
    expect(out.posts).not.toHaveBeenCalled();
    check.focus();
    fireEvent.click(check);
    const input = await screen.findByLabelText("Choose file");
    await waitFor(() => expect(input).not.toBeDisabled());
    expect(screen.getByText("private.docx")).toBeVisible();
    expect(out.posts).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "Unconfirmed upload results" })).not.toBeInTheDocument();
  });

  it("post-response session failure retains uncertainty after recovery without publishing a receipt", async () => {
    let reads = 0;
    const out = setup(modal(), async () => json(receipt), async () => {
      reads += 1;
      if (reads === 3) throw new TypeError("verification unavailable after upload");
      return json({ instructor: instructor(1) });
    });
    await choose();
    fireEvent.click(screen.getByRole("button", { name: initial }));
    fireEvent.click(await screen.findByRole("button", { name: "Check sign-in status" }));
    await screen.findByRole("region", { name: "Unconfirmed upload results" });
    expect(out.posts).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("heading", { name: "Upload complete" })).not.toBeInTheDocument();
    expect(out.router.state.location.pathname).toBe("/");
  });

  it("initial session recovery restores an owned input focus without sending a request", async () => {
    let reads = 0;
    const out = setup(modal(), async () => json(receipt), async () => {
      reads += 1;
      if (reads === 1) throw new TypeError("initial session unavailable");
      return json({ instructor: instructor(1) });
    });
    const check = await screen.findByRole("button", { name: "Check sign-in status" });
    check.focus();
    fireEvent.click(check);
    await waitFor(() => expect(screen.getByLabelText("Choose file")).toHaveFocus());
    expect(out.posts).not.toHaveBeenCalled();
  });

  it("unmounting a pending upload prevents late callbacks and private cache invalidation", async () => {
    const pending = deferred<Response>();
    const success = vi.fn();
    const out = setup(modal(success), () => pending.promise);
    const invalidate = vi.spyOn(out.client, "invalidateQueries");
    await choose();
    fireEvent.click(screen.getByRole("button", { name: initial }));
    await waitFor(() => expect(out.posts).toHaveBeenCalledTimes(1));
    out.unmount();
    await act(async () => { pending.resolve(json(receipt)); });
    expect(success).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
    expect(out.router.state.location.pathname).toBe("/");
  });
});
