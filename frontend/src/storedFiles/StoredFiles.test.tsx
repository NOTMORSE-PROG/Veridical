// V-078 (BUG-410): the stored files page lists the instructor's own Review
// Desk uploads and is the one place a stored manuscript file is deleted.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PaginatedStoredFiles, StoredFileOut } from "../api/types";
import { SIGNAL_NAVIGATION } from "../config/signalRoutes";
import { renderWithProviders } from "../test/renderWithProviders";
import { StoredFilesPage } from "./StoredFiles";

const KEPT: StoredFileOut = {
  manuscript_id: 11,
  group_label: "G1",
  original_filename: "portal.pdf",
  created_at: "2026-10-01T02:00:00Z",
  ingest_status: "done",
  dismissed_at: null,
  latest_check_run_status: "done",
  latest_check_run_id: 41,
  latest_done_check_run_id: 41,
  has_archive: false,
  has_stored_file: true,
  in_library: false,
  purged_at: null,
};

const LEGACY: StoredFileOut = { ...KEPT, manuscript_id: 12, original_filename: "legacy.pdf", in_library: true };

const DELETED: StoredFileOut = {
  ...KEPT,
  manuscript_id: 13,
  original_filename: "old.pdf",
  has_stored_file: false,
  purged_at: "2026-10-05T02:00:00Z",
};

const DISMISSED: StoredFileOut = {
  ...KEPT,
  manuscript_id: 14,
  original_filename: "broken.docx",
  ingest_status: "failed",
  dismissed_at: "2026-10-03T02:00:00Z",
  latest_check_run_status: null,
  latest_check_run_id: null,
};

const RUNNING: StoredFileOut = {
  ...KEPT,
  manuscript_id: 15,
  original_filename: "busy.pdf",
  latest_check_run_status: "semantic",
  latest_check_run_id: 45,
};

function page(items: StoredFileOut[]): PaginatedStoredFiles {
  return { items, total: items.length, page: 1, page_size: 50 };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

/** Serves `GET /archive` from `items` and answers `DELETE /archive/{id}`
 * with `onDelete`; a successful delete marks the row purged for the refetch. */
function archiveFetch(items: StoredFileOut[], onDelete?: (id: number) => Response) {
  let rows = [...items];
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input.toString(), "http://localhost");
    const match = url.pathname.match(/\/archive\/(\d+)$/);
    if (match && init?.method === "DELETE") {
      const id = Number(match[1]);
      const response = onDelete?.(id) ?? json({ manuscript_id: id, purged_at: "2026-10-13T02:00:00Z" });
      if (response.ok) {
        rows = rows.map((row) =>
          row.manuscript_id === id
            ? { ...row, has_stored_file: false, in_library: false, purged_at: "2026-10-13T02:00:00Z" }
            : row,
        );
      }
      return response;
    }
    if (url.pathname.endsWith("/archive")) return json(page(rows));
    return json({ error: { code: "internal", message: "no handler" } }, 404);
  });
}

function rowFor(name: string): HTMLElement {
  return screen.getByRole("listitem", { name });
}

afterEach(() => vi.unstubAllGlobals());

describe("Stored files (V-078)", () => {
  it("is in the workspace navigation, named as stored files and not as the Library", () => {
    const item = SIGNAL_NAVIGATION.find((route) => route.to === "/stored-files");
    expect(item?.label).toBe("Stored files");
    expect(item?.matches("/stored-files")).toBe(true);
    expect(item?.matches("/library")).toBe(false);
  });

  it("lists each upload with its stored file state and no report content", async () => {
    vi.stubGlobal("fetch", archiveFetch([KEPT, DELETED, DISMISSED, LEGACY]));
    renderWithProviders(<StoredFilesPage />, { route: "/stored-files" });

    await screen.findByRole("heading", { name: "portal.pdf" });
    expect(screen.getByText("4 uploads")).toBeInTheDocument();

    const kept = rowFor("portal.pdf");
    expect(within(kept).getByText("File recorded")).toBeInTheDocument();
    expect(within(kept).getByRole("link", { name: "Open manuscript review" })).toHaveAttribute("href", "/report/41/document");
    expect(within(kept).getByRole("button", { name: "Delete stored file" })).toBeInTheDocument();
    expect(within(kept).queryByText(/Ready|Not Ready|%/)).not.toBeInTheDocument();

    const deleted = rowFor("old.pdf");
    expect(within(deleted).getByText(/^Deleted /)).toBeInTheDocument();
    expect(within(deleted).queryByRole("button", { name: "Delete stored file" })).not.toBeInTheDocument();

    const dismissed = rowFor("broken.docx");
    expect(within(dismissed).getByText(/Removed from the Review Desk on .*VERIDICAL kept the record\./)).toBeInTheDocument();
    expect(within(dismissed).getByText("Upload preparation failed")).toBeInTheDocument();

    expect(within(rowFor("legacy.pdf")).getByText(/Also in the Library/)).toBeInTheDocument();
  });

  it("offers no delete while a check is running, and links to its progress", async () => {
    vi.stubGlobal("fetch", archiveFetch([RUNNING]));
    renderWithProviders(<StoredFilesPage />, { route: "/stored-files" });

    const row = await screen.findByRole("listitem", { name: "busy.pdf" });
    expect(within(row).queryByRole("button", { name: "Delete stored file" })).not.toBeInTheDocument();
    expect(within(row).getByText(/A preparation or check is running/)).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Open check progress" })).toHaveAttribute("href", "/checks/45");
  });

  it("explains an empty page and points back to the Review Desk", async () => {
    vi.stubGlobal("fetch", archiveFetch([]));
    renderWithProviders(<StoredFilesPage />, { route: "/stored-files" });

    expect(await screen.findByRole("heading", { name: "No stored files" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the Review Desk" })).toHaveAttribute("href", "/dashboard");
  });

  it("confirms what is deleted and kept, then announces the result and focuses the row", async () => {
    const fetchMock = archiveFetch([LEGACY]);
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<StoredFilesPage />, { route: "/stored-files" });

    fireEvent.click(await screen.findByRole("button", { name: "Delete stored file" }));
    const dialog = screen.getByRole("dialog", { name: "Delete the stored file for “legacy.pdf”?" });
    expect(within(dialog).getByText(/deletes the uploaded file and the text VERIDICAL extracted/)).toBeInTheDocument();
    expect(within(dialog).getByText(/leaves the Library/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Kept: its reports and their findings, your decisions, and the audit history\./)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Delete stored file" }));
    expect(await screen.findByText("The stored file for “legacy.pdf” was deleted.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("heading", { name: "legacy.pdf" })).toHaveFocus());
    expect(within(rowFor("legacy.pdf")).getByText(/^Deleted /)).toBeInTheDocument();
    expect(within(rowFor("legacy.pdf")).queryByText(/Also in the Library/)).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(true);
  });

  it("keeps the dialog open with the server's reason when the delete is refused", async () => {
    vi.stubGlobal(
      "fetch",
      archiveFetch([KEPT], () =>
        json({ error: { code: "conflict", message: "A check is still running on this manuscript." } }, 409),
      ),
    );
    renderWithProviders(<StoredFilesPage />, { route: "/stored-files" });

    fireEvent.click(await screen.findByRole("button", { name: "Delete stored file" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete stored file" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("Nothing was deleted");
    expect(alert).toHaveTextContent("A check is still running on this manuscript.");
  });

  it("keeps a completed report reachable after a newer cancelled attempt", async () => {
    vi.stubGlobal("fetch", archiveFetch([{ ...KEPT, latest_check_run_id: 42, latest_check_run_status: "cancelled" }]));
    renderWithProviders(<StoredFilesPage />);
    const row = await screen.findByRole("listitem", { name: "portal.pdf" });
    expect(within(row).getByText("Check cancelled")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Open manuscript review" })).toHaveAttribute("href", "/report/41/document");
    expect(within(row).queryByRole("link", { name: "Open check progress" })).not.toBeInTheDocument();
  });

  it("does not offer deletion while a source is being prepared", async () => {
    vi.stubGlobal("fetch", archiveFetch([{ ...KEPT, ingest_status: "processing", latest_check_run_id: null, latest_check_run_status: null, latest_done_check_run_id: null }]));
    renderWithProviders(<StoredFilesPage />);
    const row = await screen.findByRole("listitem", { name: "portal.pdf" });
    expect(within(row).getByText("Preparing upload")).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Delete stored file" })).not.toBeInTheDocument();
    expect(within(row).queryByRole("link", { name: "Open check progress" })).not.toBeInTheDocument();
  });

  it("describes retained metadata without claiming that a physical file exists", async () => {
    vi.stubGlobal("fetch", archiveFetch([{ ...KEPT, has_stored_file: false }]));
    renderWithProviders(<StoredFilesPage />);
    const row = await screen.findByRole("listitem", { name: "portal.pdf" });
    expect(within(row).getByText("No stored file recorded")).toBeInTheDocument();
    expect(screen.getByText(/File availability is checked when you open its source/)).toBeInTheDocument();
    expect(screen.queryByText("No file was kept")).not.toBeInTheDocument();
  });

  it("requires a status refresh after an uncertain deletion outcome", async () => {
    vi.stubGlobal("fetch", archiveFetch([KEPT], () => json({ error: { code: "internal", message: "Unavailable" } }, 500)));
    renderWithProviders(<StoredFilesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete stored file" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete stored file" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("Could not confirm deletion");
    expect(alert).not.toHaveTextContent("Nothing was deleted");
    expect(within(dialog).getByRole("button", { name: "Delete stored file" })).toBeDisabled();
    fireEvent.click(within(alert).getByRole("button", { name: "Refresh file status" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
