import { fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithUploadProviders } from "../test/renderWithUploadProviders";
import { BackgroundUploadsProvider } from "../upload/BackgroundUploads";
import { UploadManuscriptModal } from "./UploadManuscriptModal";

const REFUSAL = "This PDF contains 66,933 small image pieces, more than VERIDICAL can read reliably. Export a flattened copy and upload that.";

function serve(manuscriptId: number | null) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.replace(/^\/api/, "") + url.search;
    if (path === "/manuscripts/uploads" && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      return new Response(JSON.stringify({ attempt_id: body.attempt_id, status: "pending", transport: "relay", poll_after_ms: 1, poll_timeout_ms: 5000 }));
    }
    const attempt = path.split("/")[3];
    if (path.endsWith("/file")) return new Response(JSON.stringify({ attempt_id: attempt, status: "queued", poll_after_ms: 1, poll_timeout_ms: 5000 }));
    if (path.startsWith("/manuscripts/uploads/")) {
      return new Response(JSON.stringify({ attempt_id: attempt, status: "failed", error: REFUSAL, manuscript_id: manuscriptId, poll_after_ms: 1, poll_timeout_ms: 5000 }));
    }
    return new Response(JSON.stringify([]));
  }));
}

async function upload() {
  await renderWithUploadProviders(<BackgroundUploadsProvider><UploadManuscriptModal onClose={vi.fn()} onUploadSuccess={vi.fn()} /></BackgroundUploadsProvider>);
  fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement,
    { target: { files: [new File(["%PDF"], "OnlineRetail-KSA-PhD-2014.pdf", { type: "application/pdf" })] } });
  fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
}

describe("a file the server prepared and refused", () => {
  beforeEach(() => { vi.stubEnv("VITE_DIRECT_MANUSCRIPT_UPLOAD_ENABLED", "true"); });
  afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

  it("shows the server's reason and the kept record, never an unconfirmed result", async () => {
    serve(77);
    await upload();
    expect(await screen.findByRole("alert")).toHaveTextContent(REFUSAL);
    expect(screen.getByText(/A record of this upload was kept\. Open it in Review Desk to see its state\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Review Desk" })).toHaveAttribute("href", "/dashboard");
    expect(screen.queryByText(/remain unconfirmed/)).not.toBeInTheDocument();
    expect(screen.queryByText(/could not confirm whether these attempts saved a record/)).not.toBeInTheDocument();
  });

  it("says no record was saved when the server kept none", async () => {
    serve(null);
    await upload();
    expect(await screen.findByRole("alert")).toHaveTextContent(REFUSAL);
    expect(screen.getByText("No record of this upload was saved.")).toBeInTheDocument();
    expect(screen.queryByText(/remain unconfirmed/)).not.toBeInTheDocument();
  });
});
