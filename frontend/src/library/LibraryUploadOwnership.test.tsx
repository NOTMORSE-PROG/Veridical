// F7/F9, screen 4aa: real component/query lifetime with controlled request timing.
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ME_QUERY_KEY } from "../auth/useAuth";
import type { LibraryItemOut } from "../api/types";
import { renderWithProviders } from "../test/renderWithProviders";
import { SignalLibraryAddPage } from "./SignalLibraryAdd";

const ITEM: LibraryItemOut = {
  manuscript_id: 21, is_own: true, label: "Owned source", title: null, team_name: null,
  authors: [], program: null, school_year: null, original_filename: "first-private.pdf",
  status: "active", source: "library_submission", added_at: null,
  created_at: "2026-10-23T00:00:00Z", proposed_fields: [], possible_duplicate_of: null, failure_detail: null,
};
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const added = () => json({ outcome: "added", item: ITEM, duplicate_of: null, message: "Added" });
const possible = () => json({ outcome: "possible_duplicate", item: { ...ITEM, status: "needs_decision" }, duplicate_of: null, message: "Review this copy" });
const failed = () => json({ outcome: "failed", item: { ...ITEM, status: "failed" }, duplicate_of: null, message: "Could not read this synthetic file" });
const removed = () => json({ manuscript_id: 21, status: "removed" });
const unavailable = () => json({ error: { code: "internal", message: "Controlled unavailable session read." } }, 503);
function gate<T>() {
  let release!: (value: T) => void;
  const promise = new Promise<T>(resolve => { release = resolve; });
  return { promise, release };
}

async function mount(options: {
  auth?: (read: number) => Response | Promise<Response>;
  post?: (path: string, init: RequestInit, ordinal: number) => Response | Promise<Response>;
} = {}) {
  let client!: QueryClient;
  let reads = 0;
  const posts: { path: string; init: RequestInit; file: File | null }[] = [];
  function Probe() { client = useQueryClient(); return <SignalLibraryAddPage />; }
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://localhost").pathname;
    if (path === "/auth/session") {
      reads += 1;
      return options.auth ? options.auth(reads) : json({ instructor: { id: 1 } });
    }
    if (init?.method === "POST") {
      posts.push({ path, init, file: init.body instanceof FormData ? init.body.get("file") as File : null });
      return options.post ? options.post(path, init, posts.length) : added();
    }
    return json([]);
  }));
  const view = renderWithProviders(<Probe />, { route: "/library/add", path: "/library/add", uploadInstructorId: 1 });
  await waitFor(() => expect(screen.getByLabelText("Manuscript files")).not.toBeDisabled());
  return { ...view, client, posts, reads: () => reads };
}

function choose(names = ["first-private.pdf", "second-private.pdf"]) {
  const files = names.map(name => new File(["synthetic source"], name, { type: "application/pdf" }));
  fireEvent.change(screen.getByLabelText("Manuscript files"), { target: { files } });
  fireEvent.change(screen.getByLabelText("School year (optional)"), { target: { value: "2025-2026" } });
  return files;
}
function start(count = 2) {
  fireEvent.click(screen.getByRole("button", { name: `Add ${count} ${count === 1 ? "file" : "files"} to the Library` }));
}
afterEach(() => vi.unstubAllGlobals());

describe("Library mounted upload ownership", () => {
  it("does not confuse initial checking with loss and verifies before and after each same-owner file", async () => {
    const current = await mount();
    expect(screen.queryByText(/sign-in context changed/)).not.toBeInTheDocument();
    const files = choose(); start();
    await screen.findByRole("heading", { name: "Finished: 2 files added to the Library" });
    expect(current.posts.map(post => post.file)).toEqual(files);
    expect(current.reads()).toBe(5);
    expect(current.posts.every(post => new Headers(post.init.headers).get("X-Veridical-Expected-Instructor") === "1")).toBe(true);
    expect(current.posts.every(post => (post.init.body as FormData).get("school_year") === "2025-2026")).toBe(true);
  });

  it.each(["different actor", "same actor new lifetime", "logout"])("retires a held batch and all private DOM after %s", async replacement => {
    const first = gate<Response>();
    const current = await mount({ post: () => first.promise });
    choose(); start();
    await waitFor(() => expect(current.posts).toHaveLength(1));
    const invalidation = vi.spyOn(current.client, "invalidateQueries"); invalidation.mockClear();
    act(() => {
      if (replacement === "same actor new lifetime") current.client.clear();
      current.client.setQueryData(ME_QUERY_KEY, replacement === "logout" ? null : { id: replacement === "different actor" ? 2 : 1 });
    });
    expect(await screen.findByText("Your sign-in context changed. Close this form and start a new upload.")).toBeVisible();
    expect(screen.queryByText("first-private.pdf")).not.toBeInTheDocument();
    expect(screen.queryByText("second-private.pdf")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("School year (optional)")).not.toBeInTheDocument();
    await act(async () => { first.release(added()); });
    expect(current.posts).toHaveLength(1);
    expect(invalidation).not.toHaveBeenCalled();
    expect(screen.queryByRole("heading", { name: /Finished/ })).not.toBeInTheDocument();
  });

  it("detects a changed cookie at postflight even when the cached reader has not changed", async () => {
    const first = gate<Response>(); let actor = 1;
    const current = await mount({ auth: () => json({ instructor: { id: actor } }), post: () => first.promise });
    choose(); start();
    await waitFor(() => expect(current.posts).toHaveLength(1));
    actor = 2;
    await act(async () => { first.release(added()); });
    await screen.findByText(/Your sign-in context changed/);
    expect(current.posts).toHaveLength(1);
    expect(current.client.getQueryData(ME_QUERY_KEY)).toMatchObject({ id: 2 });
    expect(screen.queryByText("Owned source")).not.toBeInTheDocument();
  });

  it("rechecks the next file rather than relying on the preceding file's successful postflight", async () => {
    const current = await mount({ auth: read => json({ instructor: { id: read >= 4 ? 2 : 1 } }) });
    choose(); start();
    await screen.findByText(/Your sign-in context changed/);
    expect(current.posts).toHaveLength(1);
  });

  it("does not advance or invalidate after unmounting a held request", async () => {
    const first = gate<Response>();
    const current = await mount({ post: () => first.promise });
    choose(); start();
    await waitFor(() => expect(current.posts).toHaveLength(1));
    const invalidation = vi.spyOn(current.client, "invalidateQueries"); invalidation.mockClear();
    current.unmount();
    await act(async () => { first.release(added()); });
    expect(current.posts).toHaveLength(1);
    expect(invalidation).not.toHaveBeenCalled();
  });

  it("retains definitely unsent files through preflight outage and uses GET-only recovery before explicit retry", async () => {
    const preflight = gate<Response>();
    const current = await mount({ auth: read => read === 2 ? preflight.promise : json({ instructor: { id: 1 } }) });
    const files = choose(); start();
    await waitFor(() => expect(current.reads()).toBe(2));
    expect(screen.queryByText("Checking your sign-in status.")).not.toBeInTheDocument();
    expect(screen.getByText("first-private.pdf")).toBeVisible();
    expect(screen.queryByText(/Reading the text/)).not.toBeInTheDocument();
    expect(current.posts).toHaveLength(0);
    await act(async () => { preflight.release(unavailable()); });
    await screen.findByRole("heading", { name: "Paused: sign-in status not confirmed" });
    expect(screen.getByText("first-private.pdf")).toBeVisible();
    expect(screen.getAllByText("Not attempted")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Check sign-in status" }));
    await screen.findByText(/Sign-in status confirmed. Review these results, then choose Try remaining files/);
    expect(screen.getAllByRole("status").map(status => status.textContent).join(" "))
      .not.toContain("paused until your sign-in status is confirmed");
    expect(current.posts).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Try remaining files" }));
    await screen.findByRole("heading", { name: "Finished: 2 files added to the Library" });
    expect(current.posts.map(post => post.file)).toEqual(files);
  });

  it("never repeats an unconfirmed first upload after session recovery and a deliberate remaining-file retry", async () => {
    const current = await mount({ auth: read => read === 3 ? unavailable() : json({ instructor: { id: 1 } }) });
    const files = choose(); start();
    await screen.findByRole("heading", { name: "Paused: an upload result was not confirmed" });
    expect(current.posts.map(post => post.file)).toEqual([files[0]]);
    const unknownRow = screen.getByText("first-private.pdf").closest("li")!;
    expect(within(unknownRow).getByText("Result not confirmed")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Check sign-in status" }));
    await screen.findByText(/Sign-in status confirmed/);
    expect(current.posts).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Try remaining files" }));
    await screen.findByRole("heading", { name: "Upload results need review" });
    expect(current.posts.map(post => post.file)).toEqual(files);
    expect(within(unknownRow).getByText("Result not confirmed")).toBeVisible();
  });

  it.each(["skip", "delete"])("cancels an unsent %s action even when the held sign-in read later succeeds", async action => {
    const preflight = gate<Response>();
    const current = await mount({
      auth: read => read === 4 ? preflight.promise : json({ instructor: { id: 1 } }),
      post: (_path, _init, ordinal) => ordinal === 1 ? action === "skip" ? possible() : failed() : removed(),
    });
    choose(["first-private.pdf"]); start(1);
    const opener = await screen.findByRole("button", { name: action === "skip" ? "Skip this copy: first-private.pdf" : "Delete upload" });
    fireEvent.click(opener);
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: action === "skip" ? "Skip and delete this copy" : "Delete upload" }));
    await waitFor(() => expect(current.reads()).toBe(4));
    expect(screen.queryByText("Checking your sign-in status.")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: action === "skip" ? "Keep deciding" : "Cancel" })).not.toBeDisabled();
    fireEvent.click(within(dialog).getByRole("button", { name: action === "skip" ? "Keep deciding" : "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await act(async () => { preflight.release(json({ instructor: { id: 1 } })); });
    expect(current.posts).toHaveLength(1);
    await waitFor(() => expect(opener).not.toBeDisabled());
    fireEvent.click(opener);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: action === "skip" ? "Skip and delete this copy" : "Delete upload" }));
    await waitFor(() => expect(current.posts).toHaveLength(2));
    expect(new Headers(current.posts[1].init.headers).get("X-Veridical-Expected-Instructor")).toBe("1");
    await screen.findByText(action === "skip" ? "Not added. This copy was not kept." : "The unreadable upload was deleted.");
  });

  it.each(["skip", "delete"])("blocks closing a sent %s and never repeats its unconfirmed result", async action => {
    const response = gate<Response>();
    const current = await mount({
      auth: read => read === 5 ? unavailable() : json({ instructor: { id: 1 } }),
      post: (_path, _init, ordinal) => ordinal === 1 ? action === "skip" ? possible() : failed() : response.promise,
    });
    choose(["first-private.pdf"]); start(1);
    fireEvent.click(await screen.findByRole("button", { name: action === "skip" ? "Skip this copy: first-private.pdf" : "Delete upload" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: action === "skip" ? "Skip and delete this copy" : "Delete upload" }));
    await waitFor(() => expect(current.posts).toHaveLength(2));
    expect(within(dialog).getByRole("button", { name: action === "skip" ? "Keep deciding" : "Cancel" })).toBeDisabled();
    await act(async () => { response.release(removed()); });
    await screen.findByText(action === "skip" ? "Decision result not confirmed" : "Deletion result not confirmed");
    fireEvent.click(screen.getByRole("button", { name: "Check sign-in status" }));
    await screen.findByText("Sign-in status confirmed. Review these results before continuing.");
    expect(screen.queryByRole("button", { name: "Try remaining files" })).not.toBeInTheDocument();
    expect(current.posts).toHaveLength(2);
    if (action === "skip") {
      expect(screen.getByRole("button", { name: "Skip this copy: first-private.pdf" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Add anyway: first-private.pdf" })).toBeDisabled();
      expect(screen.getByRole("link", { name: "Review decision result for first-private.pdf" })).toHaveAttribute("href", "/library/21");
    } else {
      expect(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete upload" })).toBeDisabled();
      fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Delete upload" })).toBeDisabled();
    }
  });

  it.each(["skip", "delete"])("removes the private %s portal and withholds completion after owner loss", async action => {
    const response = gate<Response>();
    const current = await mount({ post: (_path, _init, ordinal) => ordinal === 1 ? action === "skip" ? possible() : failed() : response.promise });
    choose(["first-private.pdf"]); start(1);
    fireEvent.click(await screen.findByRole("button", { name: action === "skip" ? "Skip this copy: first-private.pdf" : "Delete upload" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: action === "skip" ? "Skip and delete this copy" : "Delete upload" }));
    await waitFor(() => expect(current.posts).toHaveLength(2));
    const invalidation = vi.spyOn(current.client, "invalidateQueries"); invalidation.mockClear();
    act(() => { current.client.setQueryData(ME_QUERY_KEY, { id: 2 }); });
    await screen.findByText(/Your sign-in context changed/);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText(/first-private.pdf/)).not.toBeInTheDocument();
    await act(async () => { response.release(removed()); });
    expect(invalidation).not.toHaveBeenCalled();
    expect(screen.queryByText(action === "skip" ? "Skipped" : "Upload deleted")).not.toBeInTheDocument();
  });
});
