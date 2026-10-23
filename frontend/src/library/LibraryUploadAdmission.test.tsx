// F7, screen 4aa: attempt certainty, pause ownership and deliberate safe recovery.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LibraryItemOut } from "../api/types";
import { renderWithUploadProviders } from "../test/renderWithUploadProviders";
import { SignalLibraryAddPage } from "./SignalLibraryAdd";

const ITEM: LibraryItemOut = {
  manuscript_id: 21, is_own: true, label: "Synthetic source", title: null, team_name: null,
  authors: [], program: null, school_year: null, original_filename: "first.pdf",
  status: "active", source: "library_submission", added_at: null,
  created_at: "2026-10-23T00:00:00Z", proposed_fields: [], possible_duplicate_of: null,
  failure_detail: null,
};
const added = (item = ITEM) => new Response(JSON.stringify({
  outcome: "added", item, duplicate_of: null, message: "Added",
}), { status: 200 });
const busy = () => new Response(JSON.stringify({ error: {
  code: "document_preparation_busy",
  message: "VERIDICAL is busy preparing a document. This attempt did not add a document. Try again later.",
} }), { status: 503 });

async function start(names: string[], response: (file: File, form: FormData) => Response | Promise<Response>) {
  const attempts: File[] = [];
  const forms: FormData[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname === "/library/entries" && init?.method === "POST") {
      const form = init.body as FormData;
      const file = form.get("file") as File;
      attempts.push(file);
      forms.push(form);
      return response(file, form);
    }
    return new Response(JSON.stringify([]), { status: 200 });
  }));
  await renderWithUploadProviders(<SignalLibraryAddPage />, { route: "/library/add", path: "/library/add" });
  const files = names.map((name) => new File([name], name, { type: "application/pdf" }));
  fireEvent.change(screen.getByLabelText("Manuscript files"), { target: { files } });
  fireEvent.change(screen.getByLabelText("School year (optional)"), { target: { value: "2025-2026" } });
  fireEvent.click(screen.getByRole("button", { name: `Add ${files.length} ${files.length === 1 ? "file" : "files"} to the Library` }));
  return { attempts, forms, files };
}

function row(name: string) {
  return screen.getByText(name).closest("li")!;
}

afterEach(() => vi.unstubAllGlobals());

describe("Preparation admission and unconfirmed Library attempts", () => {
  it("pauses added/busy/untouched and retries only the known eligible files with metadata", async () => {
    let available = false;
    const { attempts, forms } = await start(["first.pdf", "busy.pdf", "later.pdf"], (file) =>
      file.name === "busy.pdf" && !available ? busy() : added(),
    );
    await screen.findByRole("heading", { name: "Paused: document preparation unavailable" });
    expect(attempts.map((file) => file.name)).toEqual(["first.pdf", "busy.pdf"]);
    expect(within(row("first.pdf")).getByText("Added")).toBeInTheDocument();
    expect(within(row("busy.pdf")).getByText("Not added: preparation unavailable")).toBeInTheDocument();
    expect(within(row("later.pdf")).getByText("Not attempted")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Paused: document preparation unavailable");
    expect(screen.queryByRole("heading", { name: /Finished/ })).not.toBeInTheDocument();
    // An explicit repeated busy attempt still must not submit the untouched file.
    fireEvent.click(screen.getByRole("button", { name: "Try remaining files" }));
    await waitFor(() => expect(attempts).toHaveLength(3));
    await screen.findByRole("heading", { name: "Paused: document preparation unavailable" });
    expect(attempts[2]).toBe(attempts[1]);
    available = true;
    fireEvent.click(screen.getByRole("button", { name: "Try remaining files" }));
    await screen.findByRole("heading", { name: "Finished: 3 files added to the Library" });
    expect(attempts.map((file) => file.name)).toEqual(["first.pdf", "busy.pdf", "busy.pdf", "busy.pdf", "later.pdf"]);
    expect(forms.every((form) => form.get("school_year") === "2025-2026")).toBe(true);
  });

  it.each(["transport", "generic503", "malformed200", "wrongStatus", "nonJSON", "contradictory200", "malformedDuplicateDate", "invalidDuplicateDate"])("preserves %s uncertainty through a later busy rejection and safe continuation", async (failure) => {
    const { attempts } = await start(["first.pdf", "unknown.pdf", "later.pdf"], (file) => {
      if (file.name === "unknown.pdf") {
        if (failure === "transport") throw new TypeError("Synthetic lost response");
        if (failure === "generic503") return new Response(JSON.stringify({ error: { code: "internal", message: "Unavailable" } }), { status: 503 });
        if (failure === "wrongStatus") return new Response(JSON.stringify({ error: { code: "document_preparation_busy", message: "Not a promised rejection" } }), { status: 500 });
        if (failure === "nonJSON") return new Response("Unusable success", { status: 200 });
        if (failure === "contradictory200") return new Response(JSON.stringify({ outcome: "failed", item: ITEM, duplicate_of: null, message: "Contradictory" }), { status: 200 });
        if (failure === "malformedDuplicateDate" || failure === "invalidDuplicateDate") return new Response(JSON.stringify({
          outcome: "possible_duplicate", item: { ...ITEM, status: "needs_decision" }, message: "Possible duplicate",
          duplicate_of: { manuscript_id: 2, label: "Previous", added_at: failure === "malformedDuplicateDate" ? { toString: null, valueOf: null } : "invalid-date" },
        }), { status: 200 });
        return new Response(JSON.stringify({ outcome: "added", item: { manuscript_id: 999 }, message: "Malformed" }), { status: 200 });
      }
      if (file.name === "later.pdf" && attempts.length === 3) return busy();
      return added();
    });
    await screen.findByRole("heading", { name: "Paused: an upload result was not confirmed" });
    expect(attempts.map((file) => file.name)).toEqual(["first.pdf", "unknown.pdf"]);
    expect(within(row("unknown.pdf")).getByText("Result not confirmed")).toBeInTheDocument();
    expect(within(row("unknown.pdf")).queryByRole("button", { name: /Try/ })).not.toBeInTheDocument();
    expect(within(row("unknown.pdf")).getByRole("link", { name: "Go to Library to review the result for unknown.pdf" })).toHaveAttribute("href", "/library");
    expect(screen.queryByText(/Nothing was added/)).not.toBeInTheDocument();
    expect(screen.getByText(/Confirmed added: 1/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("unknown.pdf: result not confirmed. The batch is paused");
    fireEvent.click(screen.getByRole("button", { name: "Try remaining files" }));
    await screen.findByRole("heading", { name: "Paused: document preparation unavailable" });
    expect(within(row("unknown.pdf")).getByText("Result not confirmed")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try remaining files" }));
    await screen.findByRole("heading", { name: "Upload results need review" });
    expect(attempts.map((file) => file.name)).toEqual(["first.pdf", "unknown.pdf", "later.pdf", "later.pdf"]);
    expect(screen.getByRole("status")).toHaveTextContent("1 upload results are still not confirmed");
    fireEvent.click(screen.getByRole("button", { name: "Start another upload" }));
    const dialog = screen.getByRole("dialog", { name: "Start another upload?" });
    expect(dialog).toHaveTextContent("does not cancel or settle that request");
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep these results" }));
    expect(within(row("unknown.pdf")).getByText("Result not confirmed")).toBeInTheDocument();
  });

  it.each(["first", "last"])("pauses on a busy %s file without a false finished result", async (position) => {
    const names = position === "first" ? ["busy.pdf", "later.pdf"] : ["earlier.pdf", "busy.pdf"];
    const { attempts } = await start(names, (file) => file.name === "busy.pdf" ? busy() : added());
    await screen.findByRole("heading", { name: "Paused: document preparation unavailable" });
    expect(attempts.map((file) => file.name)).toEqual(position === "first" ? ["busy.pdf"] : names);
    expect(screen.queryByRole("heading", { name: /Finished/ })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).not.toHaveTextContent("Finished");
    expect(screen.getByRole("button", { name: "Try remaining files" })).toBeEnabled();
  });

  it("requires confirmation before clearing unknown results for an explicitly new upload", async () => {
    const { attempts } = await start(["unknown.pdf"], () => { throw new TypeError("Lost response"); });
    await screen.findByRole("heading", { name: "Upload results need review" });
    expect(screen.queryByRole("button", { name: /Try/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start another upload" }));
    const dialog = screen.getByRole("dialog", { name: "Start another upload?" });
    expect(dialog).toHaveTextContent("clears this page’s local results");
    expect(within(row("unknown.pdf")).getByText("Result not confirmed")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Start another upload" }));
    expect(await screen.findByRole("button", { name: "Add files to the Library" })).toBeInTheDocument();
    expect(attempts).toHaveLength(1);
  });

  it("retains the actual file rejection instead of a busy or unknown result", async () => {
    await start(["bad.pdf"], () => new Response(JSON.stringify({ error: { code: "file_malformed", message: "The file could not be read." } }), { status: 422 }));
    await screen.findByRole("heading", { name: "Finished: nothing was added to the Library" });
    expect(within(row("bad.pdf")).getByText("Could not add")).toBeInTheDocument();
    expect(within(row("bad.pdf")).getByText("The file could not be read.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Try/ })).not.toBeInTheDocument();
  });

  it("keeps 429 distinct and never counts a processing response as confirmed added", async () => {
    const { attempts } = await start(["reading.pdf", "limited.pdf", "later.pdf"], (file) => file.name === "limited.pdf"
      ? new Response(JSON.stringify({ error: { code: "rate_limit", message: "Try later" } }), { status: 429 })
      : added({ ...ITEM, status: "processing" }),
    );
    await screen.findByRole("heading", { name: "Paused: limit for adding files reached" });
    expect(attempts.map((file) => file.name)).toEqual(["reading.pdf", "limited.pdf"]);
    expect(screen.getByText(/Added: 0. still being prepared: 1/)).toBeInTheDocument();
    expect(within(row("limited.pdf")).getByText("Not added: upload limit reached")).toBeInTheDocument();
    expect(screen.queryByText("Not added: preparation unavailable")).not.toBeInTheDocument();
  });
});
