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
};

const UNTITLED: LibraryItemOut = {
  ...SHARED,
  manuscript_id: 3,
  group_label: "Fallback Group",
  title: null,
  original_filename: "fallback-study.docx",
  created_at: "2026-08-12T08:00:00Z",
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
});
