import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../test/renderWithProviders";
import { UploadRubricModal } from "./UploadRubricModal";

describe("UploadRubricModal", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows an inline validation message when Continue is clicked with no file chosen", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderWithProviders(<UploadRubricModal onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Continue to review" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Choose a format document before continuing.",
    );
  });

  it("the custom file-trigger label is really associated with the input (not just visually near it)", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderWithProviders(<UploadRubricModal onClose={() => {}} />);
    // getByLabelText only succeeds on a real for/id (or wrapping) association.
    expect(screen.getByLabelText("Choose file")).toHaveAttribute("type", "file");
  });

  it("shows a selected file as a removable chip, and remove returns focus to the trigger", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderWithProviders(<UploadRubricModal onClose={() => {}} />);

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
      new Response(JSON.stringify({ id: 7, criteria: [] }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<UploadRubricModal onClose={() => {}} />);

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
    renderWithProviders(<UploadRubricModal onClose={() => {}} />);

    const file = new File(["dummy"], "rubric.pdf", { type: "application/pdf" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Continue to review" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The file could not be read.");
  });
});
