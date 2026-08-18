import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../test/renderWithProviders";
import { UploadManuscriptModal } from "./UploadManuscriptModal";

const SUMMARY = {
  manuscript_id: 42,
  group_label: "Ungrouped",
  ingest_status: "done",
  page_count: 12,
  anchor_kind: "page",
  image_only: false,
  text_chars: 5000,
  images: 0,
  tables: 0,
  equations: 0,
  citations: 5,
  vision_status: "none",
  notes: [],
};

function chooseFile() {
  const file = new File(["dummy"], "thesis.pdf", { type: "application/pdf" });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

describe("UploadManuscriptModal", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows an inline validation message when uploading with no file chosen", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderWithProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Choose a manuscript file before uploading.",
    );
  });

  it("BUG-043: uploads as multipart form data with group_label as a form field, not a query param", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(SUMMARY), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);

    chooseFile();
    fireEvent.change(screen.getByLabelText("Group name or label"), { target: { value: "Group 5" } });
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/manuscripts/ingest");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("group_label")).toBe("Group 5");
    expect((init.headers as Record<string, string> | undefined)?.["Content-Type"]).toBeUndefined();
  });

  it("omits group_label from the form entirely when left blank (server applies its own default)", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(SUMMARY), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);

    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/manuscripts/ingest");
    expect((init.body as FormData).get("group_label")).toBeNull();
  });

  it("shows real, honest facts on success, not a bare checkmark, and offers to start a check", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(SUMMARY), { status: 200 })));
    const onUploadSuccess = vi.fn();
    renderWithProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={onUploadSuccess} />);

    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));

    expect(await screen.findByText("Uploaded. 12 page(s) parsed, 5 citation(s) found.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start a check with this manuscript" }));
    expect(onUploadSuccess).toHaveBeenCalledWith(42);
  });

  it("honestly states zero citations rather than silently omitting the fact", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ ...SUMMARY, citations: 0 }), { status: 200 })),
    );
    renderWithProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    expect(await screen.findByText("Uploaded. 12 page(s) parsed. No citations were found.")).toBeInTheDocument();
  });

  it("surfaces the scanned-document note verbatim when the upload is image-only", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ ...SUMMARY, image_only: true, notes: ["This document contains little or no selectable text."] }),
          { status: 200 },
        ),
      ),
    );
    renderWithProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    expect(await screen.findByText("This document contains little or no selectable text.")).toBeInTheDocument();
  });

  it("shows the server's structured error message on a 413/422 failure (charter honest-wording path)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ error: { code: "too_large", message: "The file is larger than the 40 MB upload limit." } }),
          { status: 413 },
        ),
      ),
    );
    renderWithProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The file is larger than the 40 MB upload limit.",
    );
  });

  it("BUG-032-adjacent: a mid-upload connection loss reads as a real, actionable network error, not a silent stall", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    renderWithProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not reach VERIDICAL to upload this file. Check your connection and try again.",
    );
    // Busy state cannot outlive the failed request -- retry is live immediately.
    expect(screen.getByRole("button", { name: "Upload manuscript" })).not.toBeDisabled();
  });

  it("moves focus to the error message on a failed upload, instead of stranding it at <body>", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ error: { code: "file_malformed", message: "Bad file." } }), { status: 422 }),
      ),
    );
    renderWithProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    const alert = await screen.findByRole("alert");
    await waitFor(() => expect(document.activeElement).toBe(alert));
  });

  it("'Upload another' resets the form back to a fresh idle state", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(SUMMARY), { status: 200 })));
    renderWithProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    await screen.findByText(/^Uploaded\./);
    fireEvent.click(screen.getByRole("button", { name: "Upload another" }));
    expect(screen.getByLabelText("Choose file")).toBeInTheDocument();
    expect(screen.getByText("No file chosen yet.")).toBeInTheDocument();
  });
});
