import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ArchiveItemOut, PaginatedArchive } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { ArchivePage } from "./Archive";

// Mobile card + desktop table both render in the DOM simultaneously (jsdom
// applies no media queries) -- same convention as Manage.test.tsx /
// dashboard/ManuscriptsTable.test.tsx: text present in both layouts is
// expected TWICE, not once.

const ARCHIVED: ArchiveItemOut = {
  manuscript_id: 1,
  group_label: "G1",
  original_filename: "g1.pdf",
  created_at: "2026-08-01T00:00:00Z",
  latest_check_run_status: "done",
  has_archive: true,
  purged_at: null,
};

const NOT_YET_ARCHIVED: ArchiveItemOut = {
  manuscript_id: 2,
  group_label: "G2",
  original_filename: "g2.pdf",
  created_at: "2026-08-02T00:00:00Z",
  latest_check_run_status: null,
  has_archive: false,
  purged_at: null,
};

const REMOVED: ArchiveItemOut = {
  manuscript_id: 3,
  group_label: "G3",
  original_filename: "g3.pdf",
  created_at: "2026-08-03T00:00:00Z",
  latest_check_run_status: "done",
  has_archive: false,
  purged_at: "2026-08-10T00:00:00Z",
};

function page(items: ArchiveItemOut[]): PaginatedArchive {
  return { items, total: items.length, page: 1, page_size: 50 };
}

describe("ArchivePage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows a loading state, then the list", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/archive": page([ARCHIVED]) }));
    renderWithProviders(<ArchivePage />);
    expect(screen.getByText("Loading archive…")).toBeInTheDocument();
    expect((await screen.findAllByText("G1")).length).toBe(2);
  });

  it("shows an error state with retry", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/archive": new Response("{}", { status: 500 }) }),
    );
    renderWithProviders(<ArchivePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load your archive.");
  });

  it("shows an empty state with no purge affordance", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/archive": page([]) }));
    renderWithProviders(<ArchivePage />);
    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Purge/ })).not.toBeInTheDocument();
  });

  it("renders the real 3-state archive combination distinctly", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/archive": page([ARCHIVED, NOT_YET_ARCHIVED, REMOVED]) }),
    );
    renderWithProviders(<ArchivePage />);
    await screen.findAllByText("G1");

    expect(screen.getAllByText("Archived").length).toBe(2);
    expect(screen.getAllByText("Not yet archived").length).toBe(2);
    expect(screen.getAllByText("Content removed").length).toBe(2);

    // Only the archived row gets a Purge action -- never-archived and
    // already-purged rows have nothing to purge.
    expect(screen.getAllByRole("button", { name: /^Purge/ }).length).toBe(2);
    expect(screen.getAllByText(/^Removed /).length).toBe(2);
  });

  it("Purge requires confirmation before the endpoint is called", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === `/archive/${ARCHIVED.manuscript_id}` && init?.method === "DELETE") {
        return new Response(
          JSON.stringify({ manuscript_id: ARCHIVED.manuscript_id, purged_at: "2026-08-14T00:00:00Z" }),
          { status: 200 },
        );
      }
      if (path === "/archive") return new Response(JSON.stringify(page([ARCHIVED])), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ArchivePage />);

    await screen.findAllByText("G1");
    fireEvent.click(screen.getAllByRole("button", { name: /^Purge/ })[0]);

    expect(await screen.findByText("Remove G1's stored content?")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining(`/archive/${ARCHIVED.manuscript_id}`),
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("confirming Purge updates the row in place to Content removed, never a silent disappear", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === `/archive/${ARCHIVED.manuscript_id}` && init?.method === "DELETE") {
        return new Response(
          JSON.stringify({ manuscript_id: ARCHIVED.manuscript_id, purged_at: "2026-08-14T00:00:00Z" }),
          { status: 200 },
        );
      }
      if (path === "/archive") return new Response(JSON.stringify(page([ARCHIVED])), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ArchivePage />);

    await screen.findAllByText("G1");
    fireEvent.click(screen.getAllByRole("button", { name: /^Purge/ })[0]);
    await screen.findByText("Remove G1's stored content?");
    fireEvent.click(screen.getByRole("button", { name: "Remove stored content" }));

    await waitFor(() => expect(screen.queryByText("Remove G1's stored content?")).not.toBeInTheDocument());
    expect(screen.getAllByText("Content removed").length).toBe(2);
    expect(screen.queryByRole("button", { name: /^Purge/ })).not.toBeInTheDocument();
  });

  it("ux-critic finding (P1): moves focus to the row's own identity text after purge, not stranded at <body>", async () => {
    // The identity span only mounts once `has_archive` flips to false --
    // focusing it synchronously right after the mutation resolves (before
    // React commits that re-render) would silently no-op on a still-
    // unregistered ref. Regression for the deferred-effect fix.
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === `/archive/${ARCHIVED.manuscript_id}` && init?.method === "DELETE") {
        return new Response(
          JSON.stringify({ manuscript_id: ARCHIVED.manuscript_id, purged_at: "2026-08-14T00:00:00Z" }),
          { status: 200 },
        );
      }
      if (path === "/archive") return new Response(JSON.stringify(page([ARCHIVED])), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ArchivePage />);

    await screen.findAllByText("G1");
    fireEvent.click(screen.getAllByRole("button", { name: /^Purge/ })[0]);
    await screen.findByText("Remove G1's stored content?");
    fireEvent.click(screen.getByRole("button", { name: "Remove stored content" }));

    await waitFor(() => expect(screen.queryByText("Remove G1's stored content?")).not.toBeInTheDocument());
    await waitFor(() => {
      // Mobile card + desktop grid both render one "Removed ..." span for
      // this row (jsdom applies no media queries); the ref map keys by
      // manuscript_id, so whichever layout's span mounts last wins the
      // registration -- either is a real, non-detached, focusable match.
      const removedTexts = screen.getAllByText(/^Removed /);
      expect(removedTexts.length).toBeGreaterThan(0);
      expect(removedTexts).toContain(document.activeElement);
    });
  });

  it("shows the server's error inside the still-open modal on failure", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === `/archive/${ARCHIVED.manuscript_id}` && init?.method === "DELETE") {
        return new Response(
          JSON.stringify({
            error: { code: "conflict", message: "This manuscript's archive has already been purged." },
          }),
          { status: 409 },
        );
      }
      if (path === "/archive") return new Response(JSON.stringify(page([ARCHIVED])), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ArchivePage />);

    await screen.findAllByText("G1");
    fireEvent.click(screen.getAllByRole("button", { name: /^Purge/ })[0]);
    await screen.findByText("Remove G1's stored content?");
    fireEvent.click(screen.getByRole("button", { name: "Remove stored content" }));

    expect(
      await screen.findByText("This manuscript's archive has already been purged."),
    ).toBeInTheDocument();
    // Still open, not silently dismissed on error.
    expect(screen.getByText("Remove G1's stored content?")).toBeInTheDocument();
  });

  it("Cancel closes the modal without calling the endpoint", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/archive") return new Response(JSON.stringify(page([ARCHIVED])), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ArchivePage />);

    await screen.findAllByText("G1");
    fireEvent.click(screen.getAllByRole("button", { name: /^Purge/ })[0]);
    await screen.findByText("Remove G1's stored content?");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(screen.queryByText("Remove G1's stored content?")).not.toBeInTheDocument(),
    );
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining(`/archive/${ARCHIVED.manuscript_id}`),
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});
