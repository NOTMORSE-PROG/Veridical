import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithUploadProviders } from "../test/renderWithUploadProviders";
import { BackgroundUploadsProvider } from "../upload/BackgroundUploads";
import { UploadManuscriptModal } from "./UploadManuscriptModal";

const FORMAT = { rubric_family_id: "fam-1", version: 1, is_active: true, created_at: "2026-10-01T08:00:00Z", criteria_count: 9, report_count: 0, program: null };
const CS = { ...FORMAT, id: 9, title: "CS capstone format" };
const IT = { ...FORMAT, id: 10, rubric_family_id: "fam-2", title: "IT capstone format" };

function serve(families: unknown[]) {
  const begins: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.replace(/^\/api/, "") + url.search;
    if (path === "/rubric-families") return new Response(JSON.stringify(families));
    if (path === "/manuscripts/uploads" && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      begins.push(body);
      return new Response(JSON.stringify({ attempt_id: body.attempt_id, status: "pending", transport: "relay", poll_after_ms: 1, poll_timeout_ms: 5000 }));
    }
    return new Response(JSON.stringify([]));
  }));
  return begins;
}

function chooseLargeFile() {
  const file = new File(["%PDF"], "big-thesis.pdf", { type: "application/pdf" });
  Object.defineProperty(file, "size", { value: 60 * 1024 * 1024 });
  fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [file] } });
}

async function open() {
  const onClose = vi.fn();
  await renderWithUploadProviders(<BackgroundUploadsProvider>
    <UploadManuscriptModal onClose={onClose} onUploadSuccess={vi.fn()} />
  </BackgroundUploadsProvider>);
  return onClose;
}

describe("upload dialog for a large file", () => {
  beforeEach(() => { vi.stubEnv("VITE_DIRECT_MANUSCRIPT_UPLOAD_ENABLED", "true"); window.localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

  it("says the file uploads in the background and can start the check with a chosen format", async () => {
    const begins = serve([CS, IT]);
    const onClose = await open();
    chooseLargeFile();
    expect(await screen.findByRole("heading", { name: "This file uploads in the background" })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Start a check when it's ready" }));
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose the required format for the check");
    expect(onClose).not.toHaveBeenCalled();
    const format = screen.getByLabelText(/^Required format for the check/);
    expect(format).toBeRequired();
    expect(format).toHaveAttribute("aria-invalid", "true");
    expect(format).toHaveAccessibleDescription(/The check grades the manuscript against this format/);
    fireEvent.change(format, { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    expect(onClose).toHaveBeenCalled();
    await waitFor(() => expect(begins).toHaveLength(1));
    expect(begins[0]).toMatchObject({ filename: "big-thesis.pdf", start_check: { rubric_id: 10 } });
  });

  it("uses the only active format without asking, and starts no check unless ticked", async () => {
    // Left unticked, the dialog says where the manuscript waits.
    const begins = serve([CS]);
    await open();
    chooseLargeFile();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Start a check when it's ready" }));
    expect(screen.getByText(/The check uses CS capstone format \(v1\)/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "Start a check when it's ready" }));
    expect(screen.getByText(/waits under Not checked on the Review Desk/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    await waitFor(() => expect(begins).toHaveLength(1));
    expect(begins[0]).not.toHaveProperty("start_check");
  });

  it("explains that the check is started later when no format is active", async () => {
    serve([{ ...CS, is_active: false }]);
    await open();
    chooseLargeFile();
    expect(await screen.findByText(/No required format is active yet/)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Start a check when it's ready" })).not.toBeInTheDocument();
  });
});
