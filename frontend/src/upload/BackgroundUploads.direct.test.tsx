import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithUploadProviders } from "../test/renderWithUploadProviders";
import { BackgroundUploadsProvider, useBackgroundUploads } from "./BackgroundUploads";

const RECEIPT = {
  manuscript_id: 42, group_label: "Ungrouped", ingest_status: "done", page_count: 312, anchor_kind: "page",
  image_only: false, text_chars: 5000, images: 0, tables: 0, equations: 0, citations: 5, vision_status: "none",
  notes: [], group_proposal: { title: null, short_name: null, members: [], program: null, adviser: null, extraction_failed: false },
  existing_upload: null,
};
const POLLING = { poll_after_ms: 1, poll_timeout_ms: 5000 };

type Route = (path: string, init?: RequestInit) => unknown;

function serve(route: Route) {
  const calls: { path: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.replace(/^\/api/, "") + url.search;
    calls.push({ path, body: typeof init?.body === "string" ? JSON.parse(init.body) : init?.body });
    const out = route(path, init);
    return out instanceof Response ? out : new Response(JSON.stringify(out ?? []), { status: 200 });
  }));
  return calls;
}

function Starter({ rubricId }: { rubricId?: number }) {
  const background = useBackgroundUploads();
  return <input type="file" aria-label="Manuscript" onChange={event => {
    const file = event.currentTarget.files?.[0];
    if (file) background?.start(file, rubricId === undefined ? {} : { startCheckRubricId: rubricId });
  }} />;
}

function choose(name = "large-thesis.pdf") {
  fireEvent.change(screen.getByLabelText("Manuscript"), { target: { files: [new File(["%PDF"], name, { type: "application/pdf" })] } });
}

// A relayed upload: begin, send the file, then one status poll.
function relay(done: Record<string, unknown>): Route {
  let attempt = "";
  return (path, init) => {
    if (path === "/manuscripts/uploads?active=true") return [];
    if (path === "/manuscripts/uploads" && init?.method === "POST") {
      attempt = JSON.parse(String(init.body)).attempt_id;
      return { attempt_id: attempt, status: "pending", transport: "relay", ...POLLING };
    }
    if (path.endsWith("/file")) return { attempt_id: attempt, status: "queued", ...POLLING };
    if (path === `/manuscripts/uploads/${attempt}`) return { attempt_id: attempt, status: "done", result: RECEIPT, ...POLLING, ...done };
    return [];
  };
}

describe("background uploads that start their check on the server", () => {
  beforeEach(() => { vi.stubEnv("VITE_DIRECT_MANUSCRIPT_UPLOAD_ENABLED", "true"); window.localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

  it("asks the server to start the check and links to it once the file is ready", async () => {
    const calls = serve(relay({ check_run_id: 77, check_start_error: null }));
    await renderWithUploadProviders(<BackgroundUploadsProvider><Starter rubricId={5} /></BackgroundUploadsProvider>);
    choose();
    expect(await screen.findByText("Ready. The check has started.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the check" })).toHaveAttribute("href", "/checks/77");
    const begin = calls.find((call) => call.path === "/manuscripts/uploads");
    expect(begin?.body).toMatchObject({ start_check: { rubric_id: 5 } });
  });

  it("says plainly when the file is ready but the check could not start", async () => {
    serve(relay({ check_run_id: null, check_start_error: "Only a confirmed, active rubric version can be used to start a check." }));
    await renderWithUploadProviders(<BackgroundUploadsProvider><Starter rubricId={5} /></BackgroundUploadsProvider>);
    choose();
    expect(await screen.findByText(/Ready, but the check did not start: Only a confirmed, active rubric version/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Start a check" })).toHaveAttribute("href", "/dashboard?check=42");
  });

  it("finishes two large files sent one after the other while the first is still on its way", async () => {
    const attempts: string[] = [];
    serve((path, init) => {
      if (path === "/manuscripts/uploads?active=true") return [];
      if (path === "/manuscripts/uploads" && init?.method === "POST") {
        const attempt = JSON.parse(String(init.body)).attempt_id as string;
        attempts.push(attempt);
        return { attempt_id: attempt, status: "pending", transport: "relay", ...POLLING };
      }
      const attempt = attempts.find((id) => path.includes(id)) ?? "";
      if (path.endsWith("/file")) return { attempt_id: attempt, status: "queued", ...POLLING };
      return { attempt_id: attempt, status: "done", result: { ...RECEIPT, manuscript_id: 40 + attempts.indexOf(attempt) }, ...POLLING };
    });
    await renderWithUploadProviders(<BackgroundUploadsProvider><Starter /></BackgroundUploadsProvider>);
    choose("first.pdf");
    choose("second.pdf");
    await waitFor(() => expect(screen.getAllByText("Ready.")).toHaveLength(2));
  });

  it("does not ask for a check unless the instructor chose one", async () => {
    const calls = serve(relay({ check_run_id: null }));
    await renderWithUploadProviders(<BackgroundUploadsProvider><Starter /></BackgroundUploadsProvider>);
    choose();
    expect(await screen.findByText("Ready.")).toBeInTheDocument();
    expect(calls.find((call) => call.path === "/manuscripts/uploads")?.body).not.toHaveProperty("start_check");
  });

  it("shows uploads another tab sent, follows the one still preparing, and remembers a dismissal", async () => {
    const listed = [
      { attempt_id: "a1", status: "done", filename: "earlier.pdf", size: 40 * 1024 * 1024, result: RECEIPT, check_run_id: null, ...POLLING },
      { attempt_id: "a2", status: "processing", filename: "preparing.pdf", size: 90 * 1024 * 1024, ...POLLING },
      { attempt_id: "a3", status: "pending", filename: "unsent.pdf", size: 1024, ...POLLING },
      { attempt_id: "a4", status: "expired", filename: "replaced.pdf", size: 1024, error: "A newer upload of this file replaced this attempt.", ...POLLING },
    ];
    serve((path) => path === "/manuscripts/uploads?active=true" ? listed
      : path === "/manuscripts/uploads/a2" ? { attempt_id: "a2", status: "done", result: { ...RECEIPT, manuscript_id: 43 }, check_run_id: 9, ...POLLING }
        : []);
    const first = await renderWithUploadProviders(<BackgroundUploadsProvider><Starter /></BackgroundUploadsProvider>);
    const tray = await screen.findByRole("complementary", { name: "Uploads in progress" });
    expect(tray).toHaveTextContent("earlier.pdf");
    expect(tray).not.toHaveTextContent("unsent.pdf");
    expect(tray).not.toHaveTextContent("replaced.pdf");
    expect(await screen.findByText("Ready. The check has started.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the check" })).toHaveAttribute("href", "/checks/9");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss earlier.pdf" }));
    first.unmount();

    await renderWithUploadProviders(<BackgroundUploadsProvider><Starter /></BackgroundUploadsProvider>);
    await waitFor(() => expect(screen.getByRole("complementary", { name: "Uploads in progress" })).toHaveTextContent("preparing.pdf"));
    expect(screen.queryByText("earlier.pdf")).not.toBeInTheDocument();
  });
});
