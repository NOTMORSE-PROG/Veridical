// V-077 (D-028): the Library as a standalone collection and the only
// originality corpus. Replaces the V-066 suite, whose subject (every
// dashboard upload listed as a Library record, with purge and report links)
// no longer exists.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  LibraryExcerptOut,
  LibraryItemOut,
  LibrarySubmissionOut,
  ManuscriptViewerOut,
  PaginatedLibrary,
} from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { SignalLibraryPage } from "./SignalLibrary";
import { SignalLibraryAddPage } from "./SignalLibraryAdd";
import { SignalLibraryComparePage } from "./SignalLibraryCompare";
import { SignalLibraryDetailPage } from "./SignalLibraryDetail";

vi.mock("./LibraryContentPane", () => ({
  LibraryContentPane: ({ isOwn }: { isOwn: boolean }) => (
    <div>{isOwn ? "Full manuscript source" : "Bounded manuscript excerpt"}</div>
  ),
}));

const OWN: LibraryItemOut = {
  manuscript_id: 1,
  is_own: true,
  label: "Barangay Service Portal",
  title: "Barangay Service Portal",
  team_name: "Team Lakan",
  authors: ["Ana Cruz", "Ben Reyes"],
  program: "IT",
  school_year: "2024-2025",
  original_filename: "portal.pdf",
  status: "active",
  source: "library_submission",
  added_at: "2026-10-01T02:00:00Z",
  created_at: "2026-10-01T02:00:00Z",
  proposed_fields: [],
  possible_duplicate_of: null,
  failure_detail: null,
};

const SHARED: LibraryItemOut = {
  ...OWN,
  manuscript_id: 2,
  is_own: false,
  label: "Library record #2",
  title: null,
  team_name: null,
  authors: [],
  original_filename: null,
  source: null,
  program: "CS",
};

const WAITING: LibraryItemOut = {
  ...OWN,
  manuscript_id: 3,
  label: "portal-copy.docx",
  title: null,
  original_filename: "portal-copy.docx",
  status: "needs_decision",
  added_at: null,
  possible_duplicate_of: {
    manuscript_id: 1,
    label: "Barangay Service Portal",
    original_filename: "portal.pdf",
    added_at: "2026-10-01T02:00:00Z",
  },
};

const VIEWER: ManuscriptViewerOut = {
  manuscript_id: 1,
  original_filename: "portal.pdf",
  source_format: "pdf",
  available: true,
  unavailable_reason: null,
  purged_at: null,
  page_count: 12,
  regions: [],
};

const EXCERPT: LibraryExcerptOut = {
  manuscript_id: 2,
  chapters: [
    { chapter_index: 0, title: "Introduction", excerpt: "A bounded excerpt.", context_before: null, context_after: null },
  ],
  total_chapters: 1,
  limitations: "Bounded excerpt only.",
  purged_at: null,
};

function page(items: LibraryItemOut[]): PaginatedLibrary {
  return { items, total: items.length, page: 1, page_size: 50 };
}

/** `/library` serves both the collection and `scope=pending` from one path,
 * so the shared path-keyed stub is wrapped to tell them apart. */
function libraryFetch(
  handlers: Record<string, unknown>,
  pending: PaginatedLibrary = page([]),
  onRequest?: (url: URL, init?: RequestInit) => Response | undefined,
) {
  const base = stubFetchByPath(handlers);
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input.toString(), "http://localhost");
    const custom = onRequest?.(url, init);
    if (custom) return custom;
    if (url.pathname === "/library" && url.searchParams.get("scope") === "pending") {
      return new Response(JSON.stringify(pending), { status: 200 });
    }
    return base(input);
  });
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

afterEach(() => vi.unstubAllGlobals());

describe("Library collection (4z)", () => {
  it("is a plain collection: an add action, a privacy note, and records with no verdict or report link", async () => {
    vi.stubGlobal("fetch", libraryFetch({ "/library": page([OWN, SHARED]), "/programs": [] }));
    renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });

    expect(await screen.findByRole("heading", { name: "Manuscript Library" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Add to Library/ })).toHaveAttribute("href", "/library/add");
    expect(screen.getByText("Who can see Library records")).toBeInTheDocument();
    expect(screen.getByText(/not added here automatically/)).toBeInTheDocument();

    const own = await screen.findByRole("link", { name: "Barangay Service Portal" });
    expect(own).toHaveAttribute("href", "/library/1");
    expect(screen.getByText("Ana Cruz, Ben Reyes")).toBeInTheDocument();
    // Own records show their number, so "Library record #N" in a finding is findable.
    expect(screen.getByText("#1")).toBeInTheDocument();
    const foreign = screen.getByRole("link", { name: "Library record #2" });
    expect(foreign).toHaveAttribute("href", "/library/2");
    expect(screen.getByText("Title, team, authors, and file name are withheld.")).toBeInTheDocument();

    expect(screen.queryByText(/report/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Ready|Not Ready|%/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Remove/ })).not.toBeInTheDocument();
    expect(screen.getByText("records in the Library")).toBeInTheDocument();
  });

  it("explains an empty Library honestly and offers only the add path", async () => {
    vi.stubGlobal("fetch", libraryFetch({ "/library": page([]), "/programs": [] }));
    renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });

    expect(await screen.findByRole("heading", { name: "The Library has no manuscripts yet" })).toBeInTheDocument();
    expect(screen.getByText(/right now they have nothing to compare against/)).toBeInTheDocument();
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Compare/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/selected/)).not.toBeInTheDocument();
  });

  it("keeps own uploads that need attention apart from the collection count", async () => {
    vi.stubGlobal(
      "fetch",
      libraryFetch({ "/library": page([OWN]), "/programs": [] }, page([WAITING])),
    );
    renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });

    const pending = await screen.findByRole("region", { name: "Your uploads not in the Library" });
    expect(within(pending).getByText("Possible duplicate")).toBeInTheDocument();
    expect(within(pending).getByText(/is very similar to your record “Barangay Service Portal”/)).toBeInTheDocument();
    expect(
      within(pending).getByRole("link", { name: "Review possible duplicate for portal-copy.docx" }),
    ).toHaveAttribute("href", "/library/3");
    expect(screen.getByText("record in the Library")).toBeInTheDocument();
    expect(screen.getByText("Comparison needs at least two Library records.")).toBeInTheDocument();
  });

  it("enables comparison only after two selected records have viewable content", async () => {
    vi.stubGlobal(
      "fetch",
      libraryFetch({
        "/library": page([OWN, SHARED]),
        "/programs": [],
        "/library/1/document": VIEWER,
        "/library/2/excerpt": EXCERPT,
      }),
    );
    renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });
    await screen.findByRole("link", { name: "Barangay Service Portal" });

    fireEvent.click(screen.getByRole("button", { name: "Compare two records" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select “Barangay Service Portal” for comparison" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select “Library record #2” for comparison" }));
    expect(screen.getByRole("button", { name: "Compare records" })).toBeDisabled();
    await waitFor(() => expect(screen.getByRole("button", { name: "Compare records" })).toBeEnabled());
    expect(screen.getByText("Comparison is a reading aid and does not assign a new similarity score.")).toBeInTheDocument();
  });

  it("says a search covers own records only when nothing matches", async () => {
    vi.stubGlobal("fetch", libraryFetch({ "/library": page([]), "/programs": [] }));
    renderWithProviders(<SignalLibraryPage />, { route: "/library?q=Maria", path: "/library" });

    expect(await screen.findByRole("heading", { name: "No records match your search" })).toBeInTheDocument();
    expect(screen.getAllByText("Search covers your own records only.").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Clear search and filter" })).toBeInTheDocument();
  });
});

function chooseFiles(...files: File[]) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files } });
}

function pdf(name: string): File {
  return new File(["%PDF-1.4"], name, { type: "application/pdf", lastModified: 1 });
}

function submission(outcome: LibrarySubmissionOut["outcome"], item: LibraryItemOut | null, extra: Partial<LibrarySubmissionOut> = {}): LibrarySubmissionOut {
  return { outcome, item, duplicate_of: null, message: "", ...extra };
}

describe("Add manuscripts (4aa)", () => {
  it("requires at least one file and explains what adding does before anything is sent", async () => {
    const fetchMock = libraryFetch({ "/programs": [] });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<SignalLibraryAddPage />, { route: "/library/add", path: "/library/add" });

    expect(await screen.findByRole("heading", { name: "Add manuscripts to the Library" })).toBeInTheDocument();
    expect(screen.getByText(/does not check it and produces no grade or report/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Who can see what you add" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add files to the Library" }));
    // Announced, not just associated (UX review P2-7).
    expect(screen.getByRole("alert")).toHaveTextContent("Choose at least one PDF or DOCX file.");
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/library/entries"))).toBe(false);
  });

  it("rejects a file that is not a PDF or DOCX", async () => {
    vi.stubGlobal("fetch", libraryFetch({ "/programs": [] }));
    renderWithProviders(<SignalLibraryAddPage />, { route: "/library/add", path: "/library/add" });
    await screen.findByRole("heading", { name: "Add manuscripts to the Library" });

    chooseFiles(new File(["x"], "notes.txt", { type: "text/plain" }));
    expect(screen.getByText("“notes.txt” is not a PDF or DOCX file, so it was not added to the list.")).toBeInTheDocument();
  });

  it("sends files one at a time and reports each outcome, including an exact copy", async () => {
    const order: string[] = [];
    let release: (() => void) | undefined;
    const firstGate = new Promise<void>((resolve) => { release = resolve; });
    const fetchMock = libraryFetch({ "/programs": [] }, page([]), (url, init) => {
      if (url.pathname !== "/library/entries" || init?.method !== "POST") return undefined;
      const name = ((init.body as FormData).get("file") as File).name;
      order.push(name);
      if (name === "first.pdf") {
        return undefined;
      }
      return json(submission("exact_duplicate", null, {
        duplicate_of: { manuscript_id: 1, label: "Barangay Service Portal", original_filename: "portal.pdf", added_at: "2026-10-01T02:00:00Z" },
        message: "This exact file is already in your Library, so nothing new was added.",
      }));
    });
    // The first file resolves only when released, proving the second waits.
    const wrapped = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input.toString(), "http://localhost");
      if (url.pathname === "/library/entries" && init && ((init.body as FormData).get("file") as File).name === "first.pdf") {
        order.push("first.pdf");
        await firstGate;
        return json(submission("added", { ...OWN, manuscript_id: 9, label: "First Capstone" }));
      }
      return fetchMock(input, init);
    });
    vi.stubGlobal("fetch", wrapped);
    renderWithProviders(<SignalLibraryAddPage />, { route: "/library/add", path: "/library/add" });
    await screen.findByRole("heading", { name: "Add manuscripts to the Library" });

    chooseFiles(pdf("first.pdf"), pdf("second.pdf"));
    fireEvent.click(screen.getByRole("button", { name: "Add 2 files to the Library" }));

    expect(await screen.findByRole("heading", { name: "Adding 2 files" })).toBeInTheDocument();
    await waitFor(() => expect(order).toEqual(["first.pdf"]));
    release?.();
    expect(await screen.findByText("Not added: you already uploaded this file")).toBeInTheDocument();
    // The server's status-aware wording is shown, never a client guess.
    expect(screen.getByText(/This exact file is already in your Library, so nothing new was added\./)).toBeInTheDocument();
    expect(order).toEqual(["first.pdf", "second.pdf"]);
    expect(screen.getByText(/Added as “First Capstone”/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open record for first.pdf" })).toHaveAttribute("href", "/library/9");
    expect(screen.getByRole("link", { name: "Open existing record for second.pdf" })).toHaveAttribute("href", "/library/1");
    // Counts what was added, not what was processed (first-time-user review pass).
    expect(await screen.findByRole("heading", { name: "Finished: 1 file added to the Library" })).toBeInTheDocument();
    expect(screen.getByText("Added: 1. already uploaded: 1.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Go to Library/ })).toBeInTheDocument();
  });

  it("leaves a possible duplicate to the instructor and records Add anyway", async () => {
    const decisions: string[] = [];
    vi.stubGlobal(
      "fetch",
      libraryFetch({ "/programs": [] }, page([]), (url, init) => {
        if (url.pathname === "/library/entries") {
          return json(submission("possible_duplicate", WAITING, { duplicate_of: WAITING.possible_duplicate_of }));
        }
        if (url.pathname === "/library/3/decision") {
          decisions.push(JSON.parse(String(init?.body)).action);
          return json({ manuscript_id: 3, status: "active", added_at: "2026-10-12T00:00:00Z", removed_at: null, file_deleted: false });
        }
        return undefined;
      }),
    );
    renderWithProviders(<SignalLibraryAddPage />, { route: "/library/add", path: "/library/add" });
    await screen.findByRole("heading", { name: "Add manuscripts to the Library" });

    chooseFiles(pdf("portal-copy.pdf"));
    fireEvent.click(screen.getByRole("button", { name: "Add 1 file to the Library" }));

    expect(await screen.findByText(/It has not been added/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add anyway: portal-copy.pdf" }));
    expect(await screen.findByText("Added anyway. It is now a comparison source.")).toBeInTheDocument();
    expect(decisions).toEqual(["add"]);
  });

  it("validates the school year and normalizes an en dash separator", async () => {
    const sent: (string | null)[] = [];
    vi.stubGlobal(
      "fetch",
      libraryFetch({ "/programs": [] }, page([]), (url, init) => {
        if (url.pathname !== "/library/entries") return undefined;
        if (!init) return undefined;
        sent.push((init.body as FormData).get("school_year") as string | null);
        return json(submission("added", OWN));
      }),
    );
    renderWithProviders(<SignalLibraryAddPage />, { route: "/library/add", path: "/library/add" });
    await screen.findByRole("heading", { name: "Add manuscripts to the Library" });

    chooseFiles(pdf("a.pdf"));
    fireEvent.change(screen.getByLabelText("School year (optional)"), { target: { value: "last year" } });
    fireEvent.click(screen.getByRole("button", { name: "Add 1 file to the Library" }));
    expect(screen.getByText("Enter the school year as two consecutive years, for example 2025-2026.")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("School year (optional)"), { target: { value: "2026-2025" } });
    fireEvent.click(screen.getByRole("button", { name: "Add 1 file to the Library" }));
    expect(screen.getByText("Enter the school year as two consecutive years, for example 2025-2026.")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("School year (optional)"), { target: { value: "2025 – 2026" } });
    fireEvent.click(screen.getByRole("button", { name: "Add 1 file to the Library" }));
    await waitFor(() => expect(sent).toEqual(["2025-2026"]));
  });
});

describe("Library record (4x)", () => {
  it("shows an own record's details, saves edits, and returns focus to Edit details", async () => {
    const patches: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      libraryFetch({ "/library/1": OWN, "/library/1/document": VIEWER, "/library": page([OWN, SHARED]), "/programs": [] }, page([]), (url, init) => {
        if (url.pathname === "/library/1" && init?.method === "PATCH") {
          const body = JSON.parse(String(init.body));
          patches.push(body);
          return json({ ...OWN, title: body.title, label: body.title });
        }
        return undefined;
      }),
    );
    renderWithProviders(<SignalLibraryDetailPage />, { route: "/library/1", path: "/library/:manuscriptId" });

    expect(await screen.findByRole("heading", { level: 1, name: "Barangay Service Portal" })).toBeInTheDocument();
    expect(screen.getByText("Uploaded on the Library page")).toBeInTheDocument();
    expect(screen.queryByText(/report/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Edit details" }));
    const titleInput = screen.getByLabelText("Title");
    await waitFor(() => expect(document.activeElement).toBe(titleInput));
    fireEvent.change(titleInput, { target: { value: "Renamed Capstone" } });
    fireEvent.click(screen.getByRole("button", { name: "Save details" }));

    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]).toMatchObject({ title: "Renamed Capstone", authors: ["Ana Cruz", "Ben Reyes"], program: "IT" });
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Edit details" })));
  });

  it("words removal by where the record came from", async () => {
    vi.stubGlobal(
      "fetch",
      libraryFetch({
        "/library/1": { ...OWN, source: "legacy_check_upload" },
        "/library/1/document": VIEWER,
        "/library": page([OWN]),
        "/programs": [],
      }),
    );
    renderWithProviders(<SignalLibraryDetailPage />, { route: "/library/1", path: "/library/:manuscriptId" });
    await screen.findByRole("heading", { level: 1, name: "Barangay Service Portal" });
    expect(screen.getByText("Carried over from an earlier check")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remove from Library" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/check report, instructor decision, and audit history are not affected/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Remove from Library" })).toBeInTheDocument();
  });

  it("warns that a Library submission's file is deleted on removal", async () => {
    vi.stubGlobal(
      "fetch",
      libraryFetch({ "/library/1": OWN, "/library/1/document": VIEWER, "/library": page([OWN]), "/programs": [] }),
    );
    renderWithProviders(<SignalLibraryDetailPage />, { route: "/library/1", path: "/library/:manuscriptId" });
    await screen.findByRole("heading", { level: 1, name: "Barangay Service Portal" });

    fireEvent.click(screen.getByRole("button", { name: "Remove from Library" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/stored file and its comparison data will be deleted/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Delete file and remove" })).toBeInTheDocument();
  });

  it("asks the instructor to decide a possible duplicate", async () => {
    vi.stubGlobal(
      "fetch",
      libraryFetch({ "/library/3": WAITING, "/library/3/document": VIEWER, "/library": page([OWN]), "/programs": [] }),
    );
    renderWithProviders(<SignalLibraryDetailPage />, { route: "/library/3", path: "/library/:manuscriptId" });

    expect(await screen.findByText("Possible duplicate: your decision is needed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add anyway" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Skip this copy" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open that record/ })).toHaveAttribute("href", "/library/1");
    expect(screen.getByRole("link", { name: /Compare the two/ })).toHaveAttribute("href", "/library/compare?a=3&b=1");
    // Resolved by Add anyway or Skip this copy, never by a red "Remove from Library"
    // for a record that is not in the Library (UX review P2-5).
    expect(screen.queryByRole("button", { name: /Remove from Library|Delete upload/ })).not.toBeInTheDocument();
    expect(screen.getByText("Your upload, not in the Library")).toBeInTheDocument();

    // Skip deletes the uploaded copy, so it asks first and names the file.
    fireEvent.click(screen.getByRole("button", { name: "Skip this copy" }));
    const dialog = await screen.findByRole("dialog", { name: "Skip this copy?" });
    expect(within(dialog).getByText(/“portal-copy\.docx” will not be added and will not be kept/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Your existing record “Barangay Service Portal” is not affected/)).toBeInTheDocument();
  });

  it("shows another instructor's record without edit or remove controls", async () => {
    vi.stubGlobal(
      "fetch",
      libraryFetch({ "/library/2": SHARED, "/library/2/excerpt": EXCERPT, "/library": page([OWN, SHARED]), "/programs": [] }),
    );
    renderWithProviders(<SignalLibraryDetailPage />, { route: "/library/2", path: "/library/:manuscriptId" });

    expect(await screen.findByRole("heading", { level: 1, name: "Library record #2" })).toBeInTheDocument();
    expect(screen.getByText("Bounded manuscript excerpt")).toBeInTheDocument();
    expect(screen.getByText(/which instructor added it are withheld for privacy/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit details|Remove/ })).not.toBeInTheDocument();
  });

  it("says a removed or unknown record is not available", async () => {
    vi.stubGlobal("fetch", libraryFetch({ "/library": page([]), "/programs": [] }));
    renderWithProviders(<SignalLibraryDetailPage />, { route: "/library/77", path: "/library/:manuscriptId" });

    expect(await screen.findByRole("heading", { name: "This Library record is not available" })).toBeInTheDocument();
  });

  it("uses real tabs with arrow-key movement", async () => {
    vi.stubGlobal(
      "fetch",
      libraryFetch({ "/library/1": OWN, "/library/1/document": VIEWER, "/library": page([OWN]), "/programs": [] }),
    );
    renderWithProviders(<SignalLibraryDetailPage />, { route: "/library/1", path: "/library/:manuscriptId" });
    await screen.findByRole("heading", { level: 1, name: "Barangay Service Portal" });

    const source = screen.getByRole("tab", { name: "Source" });
    const details = screen.getByRole("tab", { name: "Record details" });
    expect(source).toHaveAttribute("aria-selected", "true");
    expect(document.getElementById(source.getAttribute("aria-controls") ?? "")).toHaveAttribute("role", "tabpanel");
    expect(details).toHaveAttribute("tabindex", "-1");
    source.focus();
    fireEvent.keyDown(source, { key: "ArrowRight" });
    expect(details).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(details);
  });
});

describe("V-077 review fixes", () => {
  it("keeps the removal notice after the page settles its search parameters", async () => {
    vi.stubGlobal("fetch", libraryFetch({ "/library": page([OWN]), "/programs": [] }));
    renderWithProviders(<SignalLibraryPage />, {
      route: "/library",
      path: "/library",
      state: { removedTitle: "Barangay Service Portal" },
    });

    expect(await screen.findByText("“Barangay Service Portal” was removed from the Library.")).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect(screen.getByText("“Barangay Service Portal” was removed from the Library.")).toBeInTheDocument();
  });

  it("lets the owner delete a stalled upload, but not one still being read", async () => {
    const reading: LibraryItemOut = { ...OWN, manuscript_id: 5, status: "processing", added_at: null };
    vi.stubGlobal(
      "fetch",
      libraryFetch({ "/library/5": reading, "/library/5/document": VIEWER, "/library": page([]), "/programs": [] }),
    );
    const { unmount } = renderWithProviders(<SignalLibraryDetailPage />, { route: "/library/5", path: "/library/:manuscriptId" });
    expect(await screen.findByText("Reading this file")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete upload|Remove/ })).not.toBeInTheDocument();
    unmount();

    vi.stubGlobal(
      "fetch",
      libraryFetch({ "/library/5": { ...reading, stalled: true }, "/library/5/document": VIEWER, "/library": page([]), "/programs": [] }),
    );
    renderWithProviders(<SignalLibraryDetailPage />, { route: "/library/5", path: "/library/:manuscriptId" });
    expect(await screen.findByText("Reading this file stopped")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete upload" })).toBeInTheDocument();
  });
});

describe("V-077 first-time-user review fixes", () => {
  it("says plainly when a batch added nothing", async () => {
    vi.stubGlobal(
      "fetch",
      libraryFetch({ "/programs": [] }, page([]), (url) => {
        if (url.pathname !== "/library/entries") return undefined;
        return json(submission("failed", null, { message: "VERIDICAL could not find readable text in this file." }));
      }),
    );
    renderWithProviders(<SignalLibraryAddPage />, { route: "/library/add", path: "/library/add" });
    await screen.findByRole("heading", { name: "Add manuscripts to the Library" });

    chooseFiles(pdf("scan.pdf"));
    fireEvent.click(screen.getByRole("button", { name: "Add 1 file to the Library" }));
    expect(await screen.findByRole("heading", { name: "Finished: nothing was added to the Library" })).toBeInTheDocument();
    expect(screen.getByText("Added: 0. could not be read: 1.")).toBeInTheDocument();
  });

  it("lets the instructor decide a possible duplicate beside the comparison", async () => {
    vi.stubGlobal(
      "fetch",
      libraryFetch({
        "/library/3": WAITING,
        "/library/1": OWN,
        "/library/3/document": VIEWER,
        "/library/1/document": VIEWER,
        "/programs": [],
      }),
    );
    renderWithProviders(<SignalLibraryComparePage />, { route: "/library/compare?a=3&b=1", path: "/library/compare" });

    expect(await screen.findByText("Decide about your upload “portal-copy.docx”")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add anyway" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Skip this copy" }));
    expect(await screen.findByRole("dialog", { name: "Skip this copy?" })).toBeInTheDocument();
  });
});

