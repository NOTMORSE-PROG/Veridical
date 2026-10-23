import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithUploadProviders } from "../test/renderWithUploadProviders";
import { UploadRubricModal } from "./UploadRubricModal";

describe("UploadRubricModal", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("retains a new-version file on exact preparation busy without blaming the file", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ error: {
      code: "document_preparation_busy",
      message: "Document preparation is unavailable. This attempt did not add a document. Try again later.",
    } }), { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
    await renderWithUploadProviders(<UploadRubricModal onClose={() => {}} familyId="existing-family" />);
    const file = new File(["dummy"], "format.docx");
    const input = screen.getByLabelText("Choose file") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Continue to review" }));
    const alert = await screen.findByRole("alert");
    await waitFor(() => expect(alert).toHaveFocus());
    expect(alert).toHaveTextContent("preparation is unavailable");
    expect(input).not.toHaveAttribute("aria-invalid");
    expect(input.getAttribute("aria-describedby")).not.toContain(alert.id);
    expect(input.files?.[0]).toBe(file);
    expect(screen.getByRole("heading", { name: "Upload new format version" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Continue to review" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [url, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(new URL(url, "http://localhost").searchParams.get("family_id")).toBe("existing-family");
    expect(init.body).toBeInstanceOf(FormData);
  });

  it("shows an inline validation message when Continue is clicked with no file chosen", async () => {
    vi.stubGlobal("fetch", vi.fn());
    await renderWithUploadProviders(<UploadRubricModal onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Continue to review" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Choose a format document before continuing.",
    );
  });

  it("the custom file-trigger label is really associated with the input (not just visually near it)", async () => {
    vi.stubGlobal("fetch", vi.fn());
    await renderWithUploadProviders(<UploadRubricModal onClose={() => {}} />);
    // getByLabelText only succeeds on a real for/id (or wrapping) association.
    expect(screen.getByLabelText("Choose file")).toHaveAttribute("type", "file");
  });

  it("shows a selected file as a removable chip, and remove returns focus to the trigger", async () => {
    vi.stubGlobal("fetch", vi.fn());
    await renderWithUploadProviders(<UploadRubricModal onClose={() => {}} />);

    const file = new File(["dummy"], "rubric.pdf", { type: "application/pdf" });
    const input = screen.getByLabelText("Choose file") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    expect(screen.getByText("rubric.pdf")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove rubric.pdf" }));
    expect(screen.queryByText("rubric.pdf")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(input);
  });

  it("uploads the chosen file as multipart form data (not JSON)", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify({ id: 7, rubric_family_id: "00000000-0000-4000-8000-000000000007",
        version: 1, title: "Required format", parse_status: "needs_review", parse_issues: [],
        is_active: false, is_latest_version: true, criteria: [] }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await renderWithUploadProviders(<UploadRubricModal onClose={() => {}} />);

    const file = new File(["dummy"], "rubric.pdf", { type: "application/pdf" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Continue to review" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.headers as Record<string, string> | undefined)?.["Content-Type"]).toBeUndefined();
  });

  it("shows the server error message on a failed upload", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ error: { code: "file_malformed", message: "The file could not be read." } }),
          { status: 422 },
        ),
      ),
    );
    await renderWithUploadProviders(<UploadRubricModal onClose={() => {}} />);

    const file = new File(["dummy"], "rubric.pdf", { type: "application/pdf" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Continue to review" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The file could not be read.");
  });

  it("BUG-028: moves focus to the error message on a failed upload, instead of stranding it at <body>", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ error: { code: "file_malformed", message: "The file could not be read." } }),
          { status: 422 },
        ),
      ),
    );
    await renderWithUploadProviders(<UploadRubricModal onClose={() => {}} />);

    const file = new File(["dummy"], "rubric.pdf", { type: "application/pdf" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Continue to review" }));

    const alert = await screen.findByRole("alert");
    await waitFor(() => expect(document.activeElement).toBe(alert));
    expect(document.activeElement).not.toBe(document.body);
  });
});
