import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../test/renderWithProviders";
import { UploadManuscriptModal } from "./UploadManuscriptModal";

// V-063: an "empty" proposal (nothing usable found, no extraction
// failure) -- most of this file's existing tests care about the
// pre-existing upload flow, not the group-proposal form, so they use
// this shape to keep the plain "Upload another"/"Start a check" footer.
const EMPTY_PROPOSAL = {
  title: null,
  short_name: null,
  members: [],
  program: null,
  adviser: null,
  extraction_failed: false,
};

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
  group_proposal: EMPTY_PROPOSAL,
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

  it("V-063 (owner's call, 2026-08-19): uploads as multipart form data with no group_label field at all -- the group-proposal dialog is the only place a real group gets set now", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(SUMMARY), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);

    expect(screen.queryByLabelText(/Group name or label/)).not.toBeInTheDocument();
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/manuscripts/ingest");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("group_label")).toBeNull();
    expect((init.headers as Record<string, string> | undefined)?.["Content-Type"]).toBeUndefined();
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

  it("V-063: shows the auto-proposed group after upload when the title page had something worth confirming, and 'Skip for now' returns to the plain footer", async () => {
    const proposalSummary = {
      ...SUMMARY,
      group_proposal: {
        title: { value: "AI-Powered Capstone Advisor", anchor: "p. 1" },
        short_name: { value: "VERIDICAL", anchor: "p. 1" },
        members: [
          { value: "Juan Dela Cruz", anchor: "p. 1" },
          { value: "Maria Santos", anchor: "p. 1" },
        ],
        program: null,
        adviser: { value: "Prof. Reyes", anchor: "p. 1" },
        extraction_failed: false,
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.endsWith("/programs")) return new Response(JSON.stringify([]), { status: 200 });
        return new Response(JSON.stringify(proposalSummary), { status: 200 });
      }),
    );
    renderWithProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));

    await screen.findByText(/^Uploaded\./);
    expect(screen.getByLabelText("Group name")).toHaveValue("VERIDICAL");
    expect(screen.getByDisplayValue("Juan Dela Cruz")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Start a check with this manuscript" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
    expect(
      await screen.findByRole("button", { name: "Start a check with this manuscript" }),
    ).toBeInTheDocument();
  });

  it("V-063: shows a caution banner and pre-cleared fields when the title page couldn't be read", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            ...SUMMARY,
            group_proposal: {
              title: null,
              short_name: null,
              members: [],
              program: null,
              adviser: null,
              extraction_failed: true,
            },
          }),
          { status: 200 },
        ),
      ),
    );
    renderWithProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));

    await screen.findByText(/^Uploaded\./);
    expect(await screen.findByText(/Couldn't read the title page/)).toBeInTheDocument();
    expect(screen.getByLabelText("Group name")).toHaveValue("");
  });

  it("V-063: 'Confirm group' PATCHes the proposal and shows a resolved banner naming the created group", async () => {
    const proposalSummary = {
      ...SUMMARY,
      group_proposal: {
        title: { value: "AI-Powered Capstone Advisor", anchor: "p. 1" },
        short_name: { value: "VERIDICAL", anchor: "p. 1" },
        members: [{ value: "Juan Dela Cruz", anchor: "p. 1" }],
        program: null,
        adviser: null,
        extraction_failed: false,
      },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/programs")) return new Response(JSON.stringify([]), { status: 200 });
      if (init?.method === "PATCH") {
        return new Response(
          JSON.stringify({ group_id: 9, group_label: "VERIDICAL", program: null, matched: false }),
          { status: 200 },
        );
      }
      return new Response(JSON.stringify(proposalSummary), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));

    await screen.findByText(/^Uploaded\./);
    fireEvent.click(screen.getByRole("button", { name: "Confirm group" }));

    expect(await screen.findByText(/Created a new group/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(
      await screen.findByRole("button", { name: "Start a check with this manuscript" }),
    ).toBeInTheDocument();
    // ux-critic (V-063 review): focus used to strand at <body> on this
    // transition -- must land somewhere real in the reverted footer.
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Upload another" }));
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
