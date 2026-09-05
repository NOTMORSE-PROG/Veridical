import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  DocumentParagraphsOut,
  LibraryExcerptOut,
  LibraryItemOut,
  ManuscriptViewerOut,
  PaginatedLibrary,
} from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { SignalLibraryPage } from "./SignalLibrary";
import { SignalLibraryComparePage } from "./SignalLibraryCompare";
import { SignalLibraryDetailPage } from "./SignalLibraryDetail";

vi.mock("./LibraryContentPane", () => ({
  LibraryContentPane: ({
    isOwn,
    ownDocument,
  }: {
    isOwn: boolean;
    ownDocument?: { removedAt: string | null };
  }) => (
    <div>
      {isOwn
        ? ownDocument?.removedAt
          ? "Stored content removed from source pane"
          : "Full manuscript source"
        : "Bounded manuscript excerpt"}
    </div>
  ),
}));

const OWN: LibraryItemOut = {
  manuscript_id: 1,
  group_label: "Byte Builders",
  title: "Barangay Service Portal",
  authors: ["Ana Cruz", "Ben Lim"],
  program: "BSIT",
  original_filename: "portal.pdf",
  created_at: "2026-08-10T08:00:00Z",
  purged_at: null,
  is_own: true,
  duplicate_uploads: null,
  latest_done_check_run_id: null,
};

const SHARED: LibraryItemOut = {
  manuscript_id: 2,
  group_label: "Circuit Team",
  title: "Campus Energy Monitor",
  authors: ["Cara Reyes"],
  program: "BSCS",
  original_filename: null,
  created_at: "2026-08-11T08:00:00Z",
  purged_at: null,
  is_own: false,
  duplicate_uploads: null,
  latest_done_check_run_id: null,
};

const UNTITLED: LibraryItemOut = {
  ...SHARED,
  manuscript_id: 3,
  group_label: "Fallback Group",
  title: null,
  original_filename: "fallback-study.docx",
  created_at: "2026-08-12T08:00:00Z",
};

// BUG-147: the REAL shape the backend now returns for another
// instructor's manuscript -- matches `_item_out`'s redacted branch
// exactly (`group_label` is the non-identifying placeholder, `title`/
// `original_filename` are null, `authors` is empty), unlike `SHARED`
// above, which still carries fixture identity data for unrelated tests
// that predate this fix and aren't about redaction.
const REDACTED: LibraryItemOut = {
  manuscript_id: 99,
  group_label: "Archived manuscript #99",
  title: null,
  authors: [],
  program: "IT",
  original_filename: null,
  created_at: "2026-08-13T08:00:00Z",
  purged_at: null,
  is_own: false,
  duplicate_uploads: null,
  latest_done_check_run_id: null,
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

const OWN_DOCX: LibraryItemOut = {
  ...OWN,
  manuscript_id: 4,
  title: "Student Advising Assistant",
  original_filename: "advising.docx",
};

const DOCX_VIEWER: ManuscriptViewerOut = {
  ...VIEWER,
  manuscript_id: 4,
  original_filename: "advising.docx",
  source_format: "docx",
  page_count: null,
};

const DOCX_PARAGRAPHS: DocumentParagraphsOut = {
  paragraphs: [
    { paragraph: 1, text: "The system supports scheduled academic advising.", heading_level: null },
  ],
};

const EXCERPT: LibraryExcerptOut = {
  manuscript_id: 2,
  chapters: [
    {
      chapter_index: 0,
      title: "Introduction",
      excerpt: "A bounded chapter excerpt for comparison.",
      context_before: null,
      context_after: null,
    },
  ],
  total_chapters: 1,
  limitations: "Bounded excerpt only.",
  purged_at: null,
};

const PAGE: PaginatedLibrary = {
  items: [OWN, SHARED],
  total: 2,
  page: 1,
  page_size: 50,
};

describe("Signal manuscript library", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("opens in browse mode, explains the privacy boundary, and makes comparison an intentional mode", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/library": PAGE, "/programs": [] }));
    renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });

    expect(await screen.findByRole("heading", { name: "Manuscript Library" })).toBeInTheDocument();
    expect(screen.getByText("Privacy boundary")).toBeInTheDocument();
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Compare manuscripts" }));
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    expect(screen.getByText("Choose manuscripts to compare")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("No manuscripts selected. Select two manuscripts.");
    expect(screen.getByRole("button", { name: "Compare manuscripts" })).toBeDisabled();
  });

  it("BUG-191: enables comparison only after two selected records have viewable content", async () => {
    const fetchMock = stubFetchByPath({
      "/library": PAGE,
      "/programs": [],
      "/library/1/document": VIEWER,
      "/library/2/excerpt": EXCERPT,
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });
    await screen.findByText("Barangay Service Portal");

    fireEvent.click(screen.getByRole("button", { name: "Compare manuscripts" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select “Barangay Service Portal” for comparison" }));
    expect(screen.getByRole("status")).toHaveTextContent("One manuscript selected. Checking comparison content.");
    expect(screen.getByRole("button", { name: "Compare manuscripts" })).toBeDisabled();
    expect(await screen.findByText("Available to compare")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("One manuscript selected and available. Select one more manuscript.");

    fireEvent.click(screen.getByRole("checkbox", { name: "Select “Campus Energy Monitor” for comparison" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Compare manuscripts" })).toBeEnabled());
    expect(screen.getByText("Ready to compare")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Two manuscripts selected and available. You can compare them now.");
    expect(screen.getByRole("checkbox", { name: "Remove “Barangay Service Portal” from comparison" })).toBeChecked();

    const contentRequests = fetchMock.mock.calls
      .map(([input]) => new URL(String(input), "http://localhost").pathname)
      .filter((path) => path.endsWith("/document") || path.endsWith("/excerpt"));
    expect(contentRequests).toEqual(["/library/1/document", "/library/2/excerpt"]);
  });

  it("BUG-191: identifies which selected record has no excerpt and keeps Compare disabled", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/library": PAGE,
        "/programs": [],
        "/library/1/document": VIEWER,
        "/library/2/excerpt": { ...EXCERPT, chapters: [], total_chapters: 0 },
      }),
    );
    renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });
    await screen.findByText("Barangay Service Portal");

    fireEvent.click(screen.getByRole("button", { name: "Compare manuscripts" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select “Barangay Service Portal” for comparison" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select “Campus Energy Monitor” for comparison" }));

    const selectedRecords = screen.getByRole("list", { name: "Selected manuscripts" });
    const sharedRecord = (await within(selectedRecords).findByText(/Campus Energy Monitor/)).closest("li");
    expect(await within(sharedRecord as HTMLElement).findByText("No viewable comparison content")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "At least one selected manuscript has no viewable comparison content. Clear it and choose another.",
    );
    expect(screen.getByText("Choose another manuscript")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Compare manuscripts" })).toBeDisabled();
  });

  it("BUG-191: treats a retained but unopenable own source as unavailable before comparison", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/library": PAGE,
        "/programs": [],
        "/library/1/document": {
          ...VIEWER,
          available: false,
          unavailable_reason: "The retained source could not be opened.",
        },
        "/library/2/excerpt": EXCERPT,
      }),
    );
    renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });
    await screen.findByText("Barangay Service Portal");

    fireEvent.click(screen.getByRole("button", { name: "Compare manuscripts" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select “Barangay Service Portal” for comparison" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select “Campus Energy Monitor” for comparison" }));

    const selectedRecords = screen.getByRole("list", { name: "Selected manuscripts" });
    const ownRecord = (await within(selectedRecords).findByText(/Barangay Service Portal/)).closest("li");
    expect(await within(ownRecord as HTMLElement).findByText("No viewable comparison content")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Compare manuscripts" })).toBeDisabled();
  });

  it("BUG-191: requires usable paragraphs before an owned DOCX is available to compare", async () => {
    const docxPage = { ...PAGE, items: [OWN_DOCX, SHARED] };
    const fetchMock = stubFetchByPath({
      "/library": docxPage,
      "/programs": [],
      "/library/4/document": DOCX_VIEWER,
      "/library/4/document/paragraphs": new Response(
        JSON.stringify({ error: { code: "gone", message: "The extracted document is unavailable." } }),
        { status: 410 },
      ),
      "/library/2/excerpt": EXCERPT,
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });
    await screen.findByText("Student Advising Assistant");

    fireEvent.click(screen.getByRole("button", { name: "Compare manuscripts" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select “Student Advising Assistant” for comparison" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select “Campus Energy Monitor” for comparison" }));

    const selectedRecords = screen.getByRole("list", { name: "Selected manuscripts" });
    const docxRecord = (await within(selectedRecords).findByText(/Student Advising Assistant/)).closest("li");
    expect(await within(docxRecord as HTMLElement).findByText("No viewable comparison content")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Compare manuscripts" })).toBeDisabled();
    const paths = fetchMock.mock.calls.map(([input]) => new URL(String(input), "http://localhost").pathname);
    expect(paths).toContain("/library/4/document/paragraphs");
  });

  it("BUG-191: enables an owned DOCX only after its selected-only paragraph check succeeds", async () => {
    const docxPage = { ...PAGE, items: [OWN_DOCX, SHARED] };
    const fetchMock = stubFetchByPath({
      "/library": docxPage,
      "/programs": [],
      "/library/4/document": DOCX_VIEWER,
      "/library/4/document/paragraphs": DOCX_PARAGRAPHS,
      "/library/2/excerpt": EXCERPT,
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });
    await screen.findByText("Student Advising Assistant");

    fireEvent.click(screen.getByRole("button", { name: "Compare manuscripts" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select “Student Advising Assistant” for comparison" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select “Campus Energy Monitor” for comparison" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Compare manuscripts" })).toBeEnabled());
    expect(screen.getByRole("status")).toHaveTextContent(
      "Two manuscripts selected and available. You can compare them now.",
    );
    const contentPaths = fetchMock.mock.calls
      .map(([input]) => new URL(String(input), "http://localhost").pathname)
      .filter((path) => path.includes("/document") || path.endsWith("/excerpt"));
    expect(contentPaths).toEqual([
      "/library/4/document",
      "/library/2/excerpt",
      "/library/4/document/paragraphs",
    ]);
  });

  it("BUG-191: offers a selected-record retry after an availability request fails", async () => {
    let excerptAttempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = new URL(String(input), "http://localhost").pathname;
        if (path === "/library") return new Response(JSON.stringify(PAGE), { status: 200 });
        if (path === "/programs") return new Response(JSON.stringify([]), { status: 200 });
        if (path === "/library/1/document") return new Response(JSON.stringify(VIEWER), { status: 200 });
        if (path === "/library/2/excerpt") {
          excerptAttempts += 1;
          return excerptAttempts === 1
            ? new Response(JSON.stringify({ error: { code: "internal", message: "Unavailable" } }), { status: 503 })
            : new Response(JSON.stringify(EXCERPT), { status: 200 });
        }
        throw new Error(`Unexpected request: ${path}`);
      }),
    );
    renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });
    await screen.findByText("Barangay Service Portal");

    fireEvent.click(screen.getByRole("button", { name: "Compare manuscripts" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select “Barangay Service Portal” for comparison" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select “Campus Energy Monitor” for comparison" }));

    const retry = await screen.findByRole("button", { name: /Try availability check again for Campus Energy Monitor/ });
    expect(screen.getByRole("button", { name: "Compare manuscripts" })).toBeDisabled();
    fireEvent.click(retry);

    await waitFor(() => expect(screen.getByRole("button", { name: "Compare manuscripts" })).toBeEnabled());
    expect(excerptAttempts).toBe(2);
  });

  it("BUG-187: keeps a full selection understandable and identifies an untitled record by its visible fallback", async () => {
    const page = { ...PAGE, items: [OWN, SHARED, UNTITLED], total: 3 };
    vi.stubGlobal("fetch", stubFetchByPath({
      "/library": page,
      "/programs": [],
      "/library/1/document": VIEWER,
      "/library/2/excerpt": EXCERPT,
    }));
    renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });
    await screen.findByText("Barangay Service Portal");

    fireEvent.click(screen.getByRole("button", { name: "Compare manuscripts" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select “Barangay Service Portal” for comparison" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select “Campus Energy Monitor” for comparison" }));

    const blocked = screen.getByRole("checkbox", { name: "Select “Fallback Group” for comparison" });
    expect(blocked).toHaveAttribute("aria-disabled", "true");
    expect(blocked).toHaveAttribute("aria-describedby", "signal-compare-limit");
    expect(screen.getByText(/clear one selection to choose this manuscript/i)).toBeInTheDocument();

    fireEvent.click(blocked);
    expect(blocked).not.toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: "Remove “Campus Energy Monitor” from comparison" }));
    expect(screen.getByRole("status")).toHaveTextContent(/One manuscript selected/);
    expect(blocked).not.toHaveAttribute("aria-disabled");
    expect(blocked).not.toHaveAttribute("aria-describedby");
  });

  it("BUG-187: gives visually identical titles distinct comparison names using visible record details", async () => {
    const secondVersion: LibraryItemOut = {
      ...OWN,
      manuscript_id: 4,
      authors: ["Diego Ramos"],
      original_filename: "portal-revision.pdf",
    };
    const page = { ...PAGE, items: [OWN, secondVersion], total: 2 };
    vi.stubGlobal("fetch", stubFetchByPath({ "/library": page, "/programs": [] }));
    renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });
    await screen.findAllByText("Barangay Service Portal");

    fireEvent.click(screen.getByRole("button", { name: "Compare manuscripts" }));

    expect(
      screen.getByRole("checkbox", {
        name: /Select “Barangay Service Portal”, Byte Builders, Ana Cruz, Ben Lim, uploaded .* for comparison/,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", {
        name: /Select “Barangay Service Portal”, Byte Builders, Diego Ramos, uploaded .* for comparison/,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Ana Cruz, Ben Lim")).toBeVisible();
    expect(screen.getByText("Diego Ramos")).toBeVisible();
  });

  it("uses visible processed seconds when shared records otherwise have identical metadata", async () => {
    const first: LibraryItemOut = {
      ...UNTITLED,
      manuscript_id: 27,
      group_label: "G",
      title: null,
      authors: [],
      program: null,
      original_filename: null,
      created_at: "2026-08-18T09:53:09.555236Z",
    };
    const second: LibraryItemOut = {
      ...first,
      manuscript_id: 28,
      created_at: "2026-08-18T09:53:22.131200Z",
    };
    const page = { ...PAGE, items: [first, second], total: 2 };
    vi.stubGlobal("fetch", stubFetchByPath({ "/library": page, "/programs": [] }));
    renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });
    await screen.findAllByText("G");

    fireEvent.click(screen.getByRole("button", { name: "Compare manuscripts" }));

    const choices = screen.getAllByRole("checkbox");
    const names = choices.map((choice) => choice.getAttribute("aria-label"));
    const processedValues = screen
      .getAllByText(/Aug 18, 2026/)
      .map((value) => value.textContent);
    expect(new Set(names).size).toBe(2);
    expect(names.every((name) => name?.includes("processed"))).toBe(true);
    expect(new Set(processedValues).size).toBe(2);
    expect(processedValues.every((value) => /:\d{2}/.test(value ?? ""))).toBe(true);
  });

  it("does not issue a detail request for an invalid manuscript address", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<SignalLibraryDetailPage />, { route: "/library/not-a-number", path: "/library/:manuscriptId" });

    expect(screen.getByRole("heading", { name: "This manuscript address is not valid" })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("states the ownership boundary on a record before exposing its source", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/library/1": OWN,
        "/library/1/document": VIEWER,
      }),
    );
    renderWithProviders(<SignalLibraryDetailPage />, { route: "/library/1", path: "/library/:manuscriptId" });

    expect(await screen.findByRole("heading", { name: "Barangay Service Portal" })).toBeInTheDocument();
    expect(screen.getByText("Your manuscript")).toBeInTheDocument();
    expect(await screen.findByText("Full source viewable")).toBeInTheDocument();
    expect(screen.getByText("Stored in your Library")).toBeInTheDocument();
    expect(screen.getByText("Available to view")).toBeInTheDocument();
    expect(screen.getByText("Full manuscript source")).toBeInTheDocument();
  });

  it("separates ownership and storage from an unavailable own-source view", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/library/1": OWN,
        "/library/1/document": {
          ...VIEWER,
          available: false,
          unavailable_reason: "This manuscript's source file could not be opened.",
        },
      }),
    );
    renderWithProviders(<SignalLibraryDetailPage />, { route: "/library/1", path: "/library/:manuscriptId" });

    expect(await screen.findByText("Stored source not viewable")).toBeInTheDocument();
    expect(screen.getByText("Your manuscript")).toBeInTheDocument();
    expect(screen.getByText("Stored in your Library")).toBeInTheDocument();
    expect(screen.getByText("View unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Full source viewable")).not.toBeInTheDocument();
  });

  it("does not request a source document after stored content was removed", async () => {
    const purged = { ...OWN, purged_at: "2026-08-20T08:00:00Z" };
    const requestedPaths: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = new URL(String(input), "http://localhost").pathname;
        requestedPaths.push(path);
        if (path === "/library/1") {
          return new Response(JSON.stringify(purged), { status: 200 });
        }
        throw new Error(`Unexpected request: ${path}`);
      }),
    );
    renderWithProviders(<SignalLibraryDetailPage />, { route: "/library/1", path: "/library/:manuscriptId" });

    expect(await screen.findByText("Stored content removed")).toBeInTheDocument();
    expect(screen.getByText("Stored content removed from source pane")).toBeInTheDocument();
    expect(requestedPaths).toEqual(["/library/1"]);
  });

  it("keeps a removed own source honest in comparison without requesting its document", async () => {
    const purged = { ...OWN, purged_at: "2026-08-20T08:00:00Z" };
    const requestedPaths: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = new URL(String(input), "http://localhost").pathname;
        requestedPaths.push(path);
        if (path === "/library/1") return new Response(JSON.stringify(purged), { status: 200 });
        if (path === "/library/2") return new Response(JSON.stringify(SHARED), { status: 200 });
        if (path === "/library/2/excerpt") {
          return new Response(
            JSON.stringify({ chapters: [], purged_at: null, limitations: "Bounded excerpt." }),
            { status: 200 },
          );
        }
        throw new Error(`Unexpected request: ${path}`);
      }),
    );
    renderWithProviders(<SignalLibraryComparePage />, { route: "/library/compare?a=1&b=2", path: "/library/compare" });

    expect(await screen.findByText("Stored content removed from source pane")).toBeInTheDocument();
    expect(requestedPaths).not.toContain("/library/1/document");
  });

  it("compares full and bounded records without inventing a new similarity judgment", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/library/1": OWN,
        "/library/2": SHARED,
        "/library/1/document": VIEWER,
      }),
    );
    renderWithProviders(<SignalLibraryComparePage />, { route: "/library/compare?a=1&b=2", path: "/library/compare" });

    expect(await screen.findByRole("heading", { name: "Compare manuscripts" })).toBeInTheDocument();
    expect(screen.getByText(/does not produce a new similarity judgment/)).toBeInTheDocument();
    expect(await screen.findByText(/may show its stored source/)).toBeInTheDocument();
    expect(screen.getByText("Your manuscript")).toBeInTheDocument();
    expect(screen.getByText("Bounded shared excerpt")).toBeInTheDocument();
  });

  // BUG-147 (`ux-critic` finding): the backend redaction had no frontend
  // test guarding the copy an instructor actually reads -- a future
  // refactor could silently regress "Withheld..." back to "Not listed"
  // (which misleadingly implies no authors were ever recorded) and
  // nothing would catch it. These two close that gap on the list card
  // and the detail screen.
  it("BUG-147: names the redaction reason on the list card, distinct from a genuinely empty own record", async () => {
    const page = { ...PAGE, items: [REDACTED], total: 1 };
    vi.stubGlobal("fetch", stubFetchByPath({ "/library": page, "/programs": [] }));
    renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });

    expect(await screen.findByText("Withheld for another instructor's manuscript")).toBeInTheDocument();
    expect(screen.queryByText("Not listed")).not.toBeInTheDocument();
    // The anonymized placeholder is what's shown, never the real identity.
    expect(screen.getByText("Archived manuscript #99")).toBeInTheDocument();
  });

  it("BUG-147: names the redaction reason on the detail screen too", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/library/99": REDACTED }));
    renderWithProviders(<SignalLibraryDetailPage />, { route: "/library/99", path: "/library/:manuscriptId" });

    expect(await screen.findByText("Withheld for another instructor's manuscript")).toBeInTheDocument();
  });

  // BUG-148: the library used to list one card per raw upload, so five
  // copies of one document read as five unrelated manuscripts. These
  // tests cover the collapsed-card rendering the frontend owns; the
  // collapsing/representative-selection LOGIC itself is backend, covered
  // in test_library_router_live.py.
  describe("BUG-148: byte-identical duplicate uploads collapse onto one card", () => {
    const WITH_DUPLICATES: LibraryItemOut = {
      ...OWN,
      duplicate_uploads: [
        {
          manuscript_id: 10,
          created_at: "2026-08-09T08:00:00Z",
          purged_at: null,
          original_filename: "portal-draft.pdf",
          latest_done_check_run_id: 55,
        },
        {
          manuscript_id: 11,
          created_at: "2026-08-08T08:00:00Z",
          purged_at: "2026-08-09T12:00:00Z",
          original_filename: null,
          latest_done_check_run_id: null,
        },
      ],
    };

    it("shows an upload-count badge and keeps the disclosure closed by default", async () => {
      const page = { ...PAGE, items: [WITH_DUPLICATES, SHARED] };
      vi.stubGlobal("fetch", stubFetchByPath({ "/library": page, "/programs": [] }));
      renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });

      expect(await screen.findByText("Uploaded 3 times")).toBeInTheDocument();
      const toggle = screen.getByRole("button", { name: "Show 2 more uploads" });
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      expect(screen.queryByText("portal-draft.pdf")).not.toBeInTheDocument();
      // No such disclosure/badge at all for the record with no duplicates.
      expect(screen.queryByText("Campus Energy Monitor")).toBeInTheDocument();
      expect(screen.getAllByRole("button", { name: /more upload/i })).toHaveLength(1);
    });

    it("expands to show each individual upload with its own date, filename, and storage state", async () => {
      const page = { ...PAGE, items: [WITH_DUPLICATES] };
      vi.stubGlobal("fetch", stubFetchByPath({ "/library": page, "/programs": [] }));
      renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });

      const toggle = await screen.findByRole("button", { name: "Show 2 more uploads" });
      fireEvent.click(toggle);
      expect(screen.getByRole("button", { name: "Hide 2 more uploads" })).toHaveAttribute(
        "aria-expanded",
        "true",
      );

      expect(screen.getByText("portal-draft.pdf")).toBeInTheDocument();
      // The still-stored sibling links to its own latest DONE report.
      expect(screen.getByRole("link", { name: "Open report" })).toHaveAttribute("href", "/report/55");
      // The purged sibling (no latest_done_check_run_id) shows the
      // removed-content badge and offers no purge action of its own.
      expect(screen.getAllByText("Content removed")).not.toHaveLength(0);
      expect(screen.getAllByText("Content stored").length).toBeGreaterThan(0);
    });

    it("ux-critic finding: the representative row itself gets an Open report link when one exists, not just its hidden siblings", async () => {
      const withReport = { ...OWN, latest_done_check_run_id: 42 };
      const page = { ...PAGE, items: [withReport] };
      vi.stubGlobal("fetch", stubFetchByPath({ "/library": page, "/programs": [] }));
      renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });

      await screen.findByText("Barangay Service Portal");
      expect(screen.getByRole("link", { name: "Open report" })).toHaveAttribute("href", "/report/42");
      expect(screen.queryByRole("link", { name: "Open record" })).not.toBeInTheDocument();
    });

    it("falls back to Open record when the representative has no completed check run", async () => {
      const page = { ...PAGE, items: [OWN, SHARED] }; // OWN.latest_done_check_run_id is null
      vi.stubGlobal("fetch", stubFetchByPath({ "/library": page, "/programs": [] }));
      renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });

      await screen.findByText("Barangay Service Portal");
      expect(screen.getAllByRole("link", { name: "Open record" }).length).toBeGreaterThan(0);
      expect(screen.queryByRole("link", { name: "Open report" })).not.toBeInTheDocument();
    });

    it("shows the group's storage state as stored while ANY copy is still retrievable", async () => {
      const representativePurged = {
        ...WITH_DUPLICATES,
        purged_at: "2026-08-12T08:00:00Z", // the representative itself is purged...
        duplicate_uploads: [
          { ...WITH_DUPLICATES.duplicate_uploads![0], purged_at: null }, // ...but a sibling isn't
        ],
      };
      const page = { ...PAGE, items: [representativePurged] };
      vi.stubGlobal("fetch", stubFetchByPath({ "/library": page, "/programs": [] }));
      renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });

      await screen.findByText("Barangay Service Portal");
      expect(screen.getByText("Content stored")).toBeInTheDocument();
      expect(screen.queryByText("Content removed")).not.toBeInTheDocument();
    });

    it("discloses the group size in the purge confirmation, counting only what's ACTUALLY still stored", async () => {
      // WITH_DUPLICATES has 2 recorded siblings, but one of them
      // (manuscript 11) is already purged in the fixture -- only 1 of the
      // representative's 2 siblings is genuinely still stored.
      const page = { ...PAGE, items: [WITH_DUPLICATES] };
      vi.stubGlobal("fetch", stubFetchByPath({ "/library": page, "/programs": [] }));
      renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });
      await screen.findByText("Barangay Service Portal");

      fireEvent.click(screen.getByRole("button", { name: "Remove stored content" }));
      expect(
        screen.getByText("This document was uploaded 3 times. Removing this copy leaves 1 other stored copy untouched."),
      ).toBeInTheDocument();
    });

    it("ux-critic finding: the dialog never claims a stored copy remains once every other copy is actually gone", async () => {
      // Reproduces the exact live-found P1: purge the one still-stored
      // sibling first, THEN reopen the dialog on the representative --
      // the group's historical count is still 3, but zero OTHER copies
      // are left, so the sentence must say so honestly instead of
      // repeating a stale "leaves N other stored copies" claim.
      const purgedSibling = {
        ...WITH_DUPLICATES.duplicate_uploads![0],
        purged_at: "2026-08-09T09:00:00Z",
      };
      const afterFirstPurge = {
        ...WITH_DUPLICATES,
        duplicate_uploads: [purgedSibling, WITH_DUPLICATES.duplicate_uploads![1]],
      };
      const page = { ...PAGE, items: [afterFirstPurge] };
      vi.stubGlobal("fetch", stubFetchByPath({ "/library": page, "/programs": [] }));
      renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });
      await screen.findByText("Barangay Service Portal");

      fireEvent.click(screen.getByRole("button", { name: "Remove stored content" }));
      expect(
        screen.getByText(
          "This document was uploaded 3 times, but every other copy has already been removed. This is the last one still stored.",
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText(/leaves \d+ other stored/)).not.toBeInTheDocument();
    });

    it("discloses the group size correctly when purging from the sibling disclosure, not just the card's own action", async () => {
      const page = { ...PAGE, items: [WITH_DUPLICATES] };
      vi.stubGlobal("fetch", stubFetchByPath({ "/library": page, "/programs": [] }));
      renderWithProviders(<SignalLibraryPage />, { route: "/library", path: "/library" });
      await screen.findByText("Barangay Service Portal");

      fireEvent.click(await screen.findByRole("button", { name: "Show 2 more uploads" }));
      // manuscript 10 (not purged) is the only removable sibling; removing
      // IT leaves the representative itself still stored (1), so "1 other
      // stored copy" -- manuscript 11 is already purged and doesn't count.
      const siblingRemoveButtons = screen.getAllByRole("button", { name: "Remove stored content" });
      expect(siblingRemoveButtons).toHaveLength(2); // the card's own + manuscript 10's
      fireEvent.click(siblingRemoveButtons[1]);
      expect(
        screen.getByText("This document was uploaded 3 times. Removing this copy leaves 1 other stored copy untouched."),
      ).toBeInTheDocument();
    });
  });
});
