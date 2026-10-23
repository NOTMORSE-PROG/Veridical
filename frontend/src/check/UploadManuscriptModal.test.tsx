import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithUploadProviders } from "../test/renderWithUploadProviders";
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
  existing_upload: null,
};

function chooseFile() {
  const file = new File(["dummy"], "thesis.pdf", { type: "application/pdf" });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

describe("UploadManuscriptModal", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keeps the file and form error focus for exact preparation busy, without file-invalid semantics", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ error: {
      code: "document_preparation_busy",
      message: "VERIDICAL is busy preparing a document. This attempt did not add a document. Try again later.",
    } }), { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    const input = screen.getByLabelText("Choose file") as HTMLInputElement;
    const file = input.files?.[0];
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    const alert = await screen.findByRole("alert");
    await waitFor(() => expect(alert).toHaveFocus());
    expect(alert).toHaveTextContent("This attempt did not add a document");
    expect(input).not.toHaveAttribute("aria-invalid");
    expect(input.getAttribute("aria-describedby")).not.toContain(alert.id);
    expect(input.files?.[0]).toBe(file);
    expect(screen.getByText("thesis.pdf")).toBeInTheDocument();
    expect(screen.queryByText("Upload complete")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect((fetchMock.mock.calls[1] as unknown as [string, RequestInit])[1].body).toBeInstanceOf(FormData);
  });

  it("shows an inline validation message when uploading with no file chosen", async () => {
    vi.stubGlobal("fetch", vi.fn());
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Choose a manuscript file before uploading.",
    );
  });

  it("V-077: says before upload that checked manuscripts are compared against the Library, not added to it", async () => {
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    expect(
      screen.getByText(/Checked manuscripts are compared against the Library and are not added to it/),
    ).toBeInTheDocument();
  });

  it("V-063 (owner's call, 2026-08-19): uploads as multipart form data with no group_label field at all -- the group-proposal dialog is the only place a real group gets set now", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(SUMMARY), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);

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
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={onUploadSuccess} />);

    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));

    expect(await screen.findByText("12 pages parsed. 5 reference records extracted.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start a check with this manuscript" }));
    await waitFor(() => expect(onUploadSuccess).toHaveBeenCalledWith(42));
  });

  it.each([null, {
    manuscript_id: 17,
    created_at: "2026-09-20T04:30:00Z",
    original_filename: "thesis.pdf",
    purged_at: null,
  }])("V-081: closing a completed upload returns its new record, including a duplicate notice", async (existingUpload) => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ...SUMMARY, existing_upload: existingUpload }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const onClose = vi.fn();
    const onUploadSuccess = vi.fn();
    await renderWithUploadProviders(<UploadManuscriptModal onClose={onClose} onUploadSuccess={onUploadSuccess} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    await screen.findByRole("heading", { name: existingUpload ? "You uploaded this file before" : "Upload complete" });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledExactlyOnceWith(42);
    expect(onUploadSuccess).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("V-081: cancelling before upload has no manuscript to reveal and sends no request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const onClose = vi.fn();
    await renderWithUploadProviders(<UploadManuscriptModal onClose={onClose} onUploadSuccess={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledExactlyOnceWith(undefined);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("V-081: cancelling Upload another still reveals the manuscript already saved", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(SUMMARY), { status: 200 })));
    const onClose = vi.fn();
    await renderWithUploadProviders(<UploadManuscriptModal onClose={onClose} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    await screen.findByRole("heading", { name: "Upload complete" });
    fireEvent.click(screen.getByRole("button", { name: "Upload another" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledExactlyOnceWith(42);
  });

  it("states zero extracted reference records without claiming the manuscript has none", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ ...SUMMARY, citations: 0 }), { status: 200 })),
    );
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    expect(await screen.findByText("12 pages parsed. 0 reference records extracted.")).toBeInTheDocument();
  });

  it.each([
    ["paragraph", 0, 0, "Word document processed using paragraph locations. Page count is not available. 0 reference records extracted."],
    ["paragraph", 7, 1, "Word document processed using paragraph locations. Page count is not available. 1 reference record extracted."],
    ["page", 0, 0, "The parser reported 0 pages for this PDF. Check the file before starting a check. 0 reference records extracted."],
    ["page", 1, 1, "1 page parsed. 1 reference record extracted."],
    [undefined, 0, 2, "Page count is not available for this upload. 2 reference records extracted."],
  ])("uses returned location kind %s, not filename or zero-page inference", async (anchorKind, pages, citations, expected) => {
    const note = "Some content needs a source-reading check.";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      ...SUMMARY, anchor_kind: anchorKind, page_count: pages, citations, notes: [note],
    }), { status: 200 })));
    const onUploadSuccess = vi.fn();
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={onUploadSuccess} />);
    chooseFile(); // The returned location contract wins even for a .pdf filename.
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    if (anchorKind === undefined) {
      expect(await screen.findByRole("alert")).toHaveTextContent("could not confirm");
      expect(screen.queryByRole("heading", { name: "Upload complete" })).not.toBeInTheDocument();
      expect(onUploadSuccess).not.toHaveBeenCalled();
      return;
    }
    expect(await screen.findByText(expected)).toBeInTheDocument();
    expect(screen.queryByText(/0 pages parsed/)).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Extraction notes" })).toHaveTextContent(note);
    if (anchorKind !== "paragraph") expect(screen.queryByText(/Word document processed/)).not.toBeInTheDocument();
    const heading = screen.getByRole("heading", { name: "Upload complete" });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    fireEvent.click(screen.getByRole("button", { name: "Start a check with this manuscript" }));
    await waitFor(() => expect(onUploadSuccess).toHaveBeenCalledWith(42));
  });

  it("displays source-reading limitations for an ordinary text PDF", async () => {
    const note = "References were not extracted because some pages cannot be read reliably.";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      ...SUMMARY, citations: 0, image_only: false, notes: [note],
    }), { status: 200 })));
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    expect(await screen.findByText(note)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Extraction notes" })).toHaveTextContent(note);
  });

  it("BUG-234/V-077: explains a same-account exact re-upload without calling it a Library record", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            ...SUMMARY,
            existing_upload: {
              manuscript_id: 17,
              created_at: "2026-09-20T04:30:00Z",
              original_filename: "defense-manuscript.pdf",
              purged_at: null,
            },
          }),
          { status: 200 },
        ),
      ),
    );
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));

    const heading = await screen.findByRole("heading", { name: "You uploaded this file before" });
    expect(heading).toBeInTheDocument();
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(screen.getByText(/exact same file you uploaded on .*That upload was named “defense-manuscript\.pdf”/)).toBeInTheDocument();
    expect(screen.getByText(/new copy was saved and can be checked on its own/)).toBeInTheDocument();
    // V-077 (D-028): a dashboard upload is never a Library record.
    expect(screen.queryByText(/Library record/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Library/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start a check with this manuscript" })).toBeInTheDocument();
  });

  it("BUG-234: honestly distinguishes a purged prior copy while keeping the new upload usable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            ...SUMMARY,
            existing_upload: {
              manuscript_id: 17,
              created_at: "2026-09-20T04:30:00Z",
              original_filename: "defense-manuscript.pdf",
              purged_at: "2026-09-21T01:00:00Z",
            },
          }),
          { status: 200 },
        ),
      ),
    );
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));

    expect(await screen.findByRole("heading", { name: "You uploaded this file before" })).toBeInTheDocument();
    expect(screen.getByText(/earlier copy's stored file was removed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start a check with this manuscript" })).toBeInTheDocument();
  });

  it("BUG-234: offers a visible shortcut past optional group details and focuses Start check", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.endsWith("/programs")) return new Response(JSON.stringify([]), { status: 200 });
        return new Response(
          JSON.stringify({
            ...SUMMARY,
            existing_upload: {
              manuscript_id: 17,
              created_at: "2026-09-20T04:30:00Z",
              original_filename: "defense-manuscript.pdf",
              purged_at: null,
            },
            group_proposal: {
              ...EMPTY_PROPOSAL,
              title: { value: "A Long Title", anchor: "p. 1" },
            },
          }),
          { status: 200 },
        );
      }),
    );
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));

    const continueButton = await screen.findByRole("button", {
      name: "Skip group details and continue",
    });
    fireEvent.click(continueButton);
    const startButton = await screen.findByRole("button", {
      name: "Start a check with this manuscript",
    });
    await waitFor(() => expect(document.activeElement).toBe(startButton));
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
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
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
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The file is larger than the 40 MB upload limit.",
    );
  });

  it("BUG-032-adjacent: a mid-upload connection loss reads as a real, actionable network error, not a silent stall", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "VERIDICAL could not confirm this upload's result. Review your uploads before starting another attempt.",
    );
    // A lost response is not a known rejection. A new request needs explicit confirmation.
    expect(screen.getByRole("button", { name: "Start another upload attempt" })).not.toBeDisabled();
  });

  it("moves focus to the error message on a failed upload, instead of stranding it at <body>", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ error: { code: "file_malformed", message: "Bad file." } }), { status: 422 }),
      ),
    );
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
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
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));

    await screen.findByRole("heading", { name: "Upload complete" });
    expect(screen.getByLabelText("Group name")).toHaveValue("VERIDICAL");
    expect(screen.getByDisplayValue("Juan Dela Cruz")).toBeInTheDocument();
    const footer = document.querySelector(".signal-task-sheet__footer") as HTMLElement;
    const body = document.querySelector(".signal-task-sheet__body") as HTMLElement;
    expect(within(footer).getByRole("button", { name: "Confirm assignment" })).toBeInTheDocument();
    expect(within(body).queryByRole("button", { name: "Confirm assignment" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Start a check with this manuscript" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
    expect(
      await screen.findByRole("button", { name: "Start a check with this manuscript" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload another" })).toHaveFocus();
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
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));

    await screen.findByRole("heading", { name: "Upload complete" });
    expect(await screen.findByText(/Couldn't read the title page/)).toBeInTheDocument();
    expect(screen.getByLabelText("Group name")).toHaveValue("");
  });

  it("V-063: 'Confirm assignment' PATCHes the proposal and shows a resolved banner naming the created group", async () => {
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
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));

    await screen.findByRole("heading", { name: "Upload complete" });
    fireEvent.click(screen.getByRole("button", { name: "Confirm assignment" }));

    expect(await screen.findByText(/Created a new group/)).toBeInTheDocument();
    expect(within(document.querySelector(".signal-task-sheet__footer") as HTMLElement)
      .getByRole("button", { name: "Done" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(
      await screen.findByRole("button", { name: "Start a check with this manuscript" }),
    ).toBeInTheDocument();
    // UX review (V-063 review): focus used to strand at <body> on this
    // transition -- must land somewhere real in the reverted footer.
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Upload another" }));
  });

  it("'Upload another' resets the form back to a fresh idle state", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(SUMMARY), { status: 200 })));
    await renderWithUploadProviders(<UploadManuscriptModal onClose={() => {}} onUploadSuccess={() => {}} />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload manuscript" }));
    await screen.findByRole("heading", { name: "Upload complete" });
    fireEvent.click(screen.getByRole("button", { name: "Upload another" }));
    expect(screen.getByLabelText("Choose file")).toBeInTheDocument();
    expect(screen.getByText("No file chosen yet.")).toBeInTheDocument();
  });
});
