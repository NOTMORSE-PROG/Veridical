import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Rubric, RubricIssueDetail, RubricLevelSource } from "../api/types";
import { renderWithProviders } from "../test/renderWithProviders";
import { SignalReviewCriteriaPage } from "./SignalReviewCriteria";

// The PDF reader is replaced by a stub that shows which page was asked for.
vi.mock("../document/PdfPane", () => ({
  PdfPane: ({ requestedPage }: { requestedPage: number | null }) => <p data-testid="pdf-pane">Requested page {String(requestedPage)}</p>,
}));

function cell(row: number, col: number, rowLabel: string, heading: string, match: RubricLevelSource["match"] = "exact"): RubricLevelSource {
  return { match, page: 1, table: 1, row, col, cell_text: "cell", row_label: rowLabel, column_heading: heading };
}

const ISSUE: RubricIssueDetail = {
  id: 11, criterion_id: 2, level: 3, code: "not_in_source",
  message: "'Purpose' level 'Proficient': the descriptor is not found in the document", status: "open", resolution: null,
};

function rubric(overrides: Partial<Rubric> = {}): Rubric {
  return {
    id: 5,
    rubric_family_id: "11111111-1111-1111-1111-111111111111",
    version: 1,
    title: "Rubric for Oral Presentation",
    parse_status: "needs_review",
    parse_issues: [ISSUE.message],
    is_active: false,
    is_latest_version: true,
    can_edit_criteria: true,
    revision: 4,
    source_summary: { copy_pages: [1, 2, 3], withheld_count: 2 },
    parse_issue_details: [ISSUE],
    criteria: [
      { id: 1, type: "structural", text: "Introduction", evidence: null, weight: 1, position: 0, levels: null },
      {
        id: 2, type: "semantic", text: "Purpose", evidence: null, weight: 1, position: 1,
        levels: [
          { level: 1, name: "Beginner", descriptor: "The purpose is unclear.", points: 1, source: cell(2, 2, "Purpose", "BEGIN ER 1") },
          { level: 3, name: "Proficient", descriptor: "The paper's purpose is clearly stated.", points: 3, source: cell(2, 4, "Purpose", "PROFICIEN T 3", "unmatched") },
        ],
      },
    ],
    ...overrides,
  };
}

type Handler = (url: string, init?: RequestInit) => Response | undefined;

function serve(get: () => Rubric, handler: Handler = () => undefined) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const custom = handler(url, init);
    if (custom) return custom;
    if (url.includes("/document")) return new Response(JSON.stringify({ error: { code: "not_found", message: "none" } }), { status: 404 });
    return new Response(JSON.stringify(get()), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function puts(fetchMock: ReturnType<typeof serve>) {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === "PUT")
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);
}

function renderPage() {
  return renderWithProviders(<SignalReviewCriteriaPage />, {
    route: "/rubric/5/review", path: "/rubric/:rubricId/review", authInstructorId: 1,
  });
}

describe("rubric review before activation (BUG-566)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows each level against the document and asks only about the one that did not match", async () => {
    serve(() => rubric());
    renderPage();

    expect(await screen.findByText("1 item to check before this format can be activated")).toBeInTheDocument();
    const proficient = screen.getByRole("heading", { level: 4, name: "Proficient" }).closest("li")!;
    expect(within(proficient).getByText("Check this")).toBeInTheDocument();
    expect(within(proficient).getByText("VERIDICAL could not find this wording in the document. Compare it with the original.")).toBeInTheDocument();
    expect(within(proficient).getByText('Page 1 · row "Purpose" · column "PROFICIEN T 3"')).toBeInTheDocument();
    const beginner = screen.getByRole("heading", { level: 4, name: "Beginner" }).closest("li")!;
    expect(within(beginner).getByText("Matches the document")).toBeInTheDocument();
    expect(within(beginner).queryByRole("button", { name: "Keep this wording" })).not.toBeInTheDocument();
    // The issue is shown once, on its level, not again as a raw parser list.
    expect(screen.queryByText(ISSUE.message)).not.toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Items to check" })).toHaveTextContent("1 item to check");
  });

  it("refuses activation while an item is open, without asking the server, and links to the item", async () => {
    const fetchMock = serve(() => rubric());
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Confirm and activate format" }));

    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("Check these items before activating");
    await waitFor(() => expect(summary).toHaveFocus());
    expect(puts(fetchMock)).toHaveLength(0);

    fireEvent.click(within(summary).getByRole("button", { name: /Criterion 2 · Proficient/ }));
    expect(screen.getByRole("heading", { level: 4, name: "Proficient" })).toHaveFocus();
    expect(screen.getByRole("navigation", { name: "Items to check" })).toHaveTextContent("Item 1 of 1");
  });

  it("keeps wording with a reason, sends it with the revision on save, and can undo before saving", async () => {
    let current = rubric();
    const fetchMock = serve(() => current, (_url, init) => {
      if (init?.method !== "PUT") return undefined;
      current = rubric({
        revision: 5,
        parse_issue_details: [{ ...ISSUE, status: "acknowledged", resolution: { kind: "acknowledged", reason_code: "document_wording", note: null, by_instructor_id: 1, by_name: "Ana Reyes", at: "2026-10-30T10:00:00Z" } }],
      });
      return new Response(JSON.stringify(current), { status: 200 });
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Keep this wording" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep wording" }));
    expect(screen.getByText("Choose why you are keeping this wording.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "The document says it this way" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep wording" }));

    const proficient = screen.getByRole("heading", { level: 4, name: "Proficient" }).closest("li")!;
    expect(within(proficient).getByText("Kept as written")).toBeInTheDocument();
    expect(within(proficient).getByText(/Not saved yet/)).toBeInTheDocument();
    expect(screen.getByText("All 1 item is checked. You can activate this format.")).toBeInTheDocument();

    fireEvent.click(within(proficient).getByRole("button", { name: "Undo" }));
    expect(within(proficient).getByText("Check this")).toBeInTheDocument();
    await waitFor(() => expect(within(proficient).getByRole("button", { name: "Keep this wording" })).toHaveFocus());

    fireEvent.click(within(proficient).getByRole("button", { name: "Keep this wording" }));
    fireEvent.click(screen.getByRole("radio", { name: "Other reason" }));
    fireEvent.change(screen.getByLabelText("Note (optional)"), { target: { value: "  Matches the printed copy  " } });
    fireEvent.click(screen.getByRole("button", { name: "Keep wording" }));
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));

    await waitFor(() => expect(puts(fetchMock)).toHaveLength(1));
    expect(puts(fetchMock)[0]).toMatchObject({
      confirm: false, expected_revision: 4,
      acknowledgements: [{ issue_id: 11, reason_code: "other", note: "Matches the printed copy" }],
    });
    expect(await screen.findByText("Checked by Ana Reyes, Oct 30, 2026", { exact: false })).toBeInTheDocument();

    // The next save starts from the revision the server returned.
    fireEvent.click(screen.getByRole("button", { name: "Confirm and activate format" }));
    await waitFor(() => expect(puts(fetchMock)).toHaveLength(2));
    expect(puts(fetchMock)[1]).toMatchObject({ confirm: true, expected_revision: 5 });
    expect(puts(fetchMock)[1]).not.toHaveProperty("acknowledgements");
  });

  it("shows the server's refusal to activate as the same list, after reading the format again", async () => {
    let current = rubric({ parse_issue_details: [{ ...ISSUE, status: "resolved", resolution: null }] });
    serve(() => current, (_url, init) => {
      if (init?.method !== "PUT") return undefined;
      current = rubric();
      return new Response(JSON.stringify({ error: { code: "unresolved_rubric_issues", message: "Open items", details: { open_issue_ids: [11] } } }), { status: 409 });
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Confirm and activate format" }));

    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("Check these items before activating");
    expect(summary).toHaveTextContent("Criterion 2 · Proficient");
    expect(screen.queryByText("Could not save these criteria")).not.toBeInTheDocument();
  });

  it("keeps the instructor's edits when the format changed elsewhere, and reloads only after they agree", async () => {
    let current = rubric();
    let refused = false;
    serve(() => current, (_url, init) => {
      if (init?.method !== "PUT") return undefined;
      refused = true;
      current = rubric({ revision: 9, title: "Changed elsewhere" });
      return new Response(JSON.stringify({ error: { code: "rubric_changed", message: "Changed" } }), { status: 409 });
    });
    renderPage();
    const text = await screen.findByLabelText("Criterion text, criterion 1");
    fireEvent.change(text, { target: { value: "Introduction, edited" } });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));

    expect(await screen.findByText("This format changed in another tab or window. Your edits are still on this page but were not saved.")).toBeInTheDocument();
    expect(refused).toBe(true);
    expect(screen.getByLabelText("Criterion text, criterion 1")).toHaveValue("Introduction, edited");

    fireEvent.click(screen.getByRole("button", { name: "Load the latest version" }));
    const dialog = await screen.findByRole("dialog", { name: "Discard your edits and load the latest version?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Discard and load" }));
    await waitFor(() => expect(screen.getByLabelText("Criterion text, criterion 1")).toHaveValue("Introduction"));
    expect(screen.getByText(/Changed elsewhere/)).toBeInTheDocument();
  });

  it("says plainly when a version was locked by a check while it was being edited", async () => {
    serve(() => rubric(), (_url, init) => init?.method === "PUT"
      ? new Response(JSON.stringify({ error: { code: "rubric_locked", message: "Locked" } }), { status: 409 }) : undefined);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Save draft" }));
    expect(await screen.findByText("A check started using this version, so it is now locked. Your edits were not saved.")).toBeInTheDocument();
  });

  it("shows a locked version's items read-only, with no way to keep wording on it", async () => {
    serve(() => rubric({ can_edit_criteria: false, is_active: true }));
    renderPage();
    expect(await screen.findByText(/1 item in this version could not be matched with the document\. This version is locked/)).toBeInTheDocument();
    expect(screen.getByText("Check this")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Keep this wording" })).not.toBeInTheDocument();
  });

  it("does not present unchecked levels from an older version as matches", async () => {
    const legacy = rubric({ parse_issue_details: null, source_summary: null, revision: undefined, parse_status: "parsed", parse_issues: null });
    legacy.criteria[1].levels = legacy.criteria[1].levels!.map(({ source: _source, ...level }) => level);
    serve(() => legacy);
    renderPage();
    expect(await screen.findByText(/prepared before VERIDICAL compared level descriptions with the document/)).toBeInTheDocument();
    expect(screen.getAllByText("Not checked against the document")).toHaveLength(2);
    expect(screen.queryByText("Matches the document")).not.toBeInTheDocument();
  });

  it("says repeated copies were read once and counts the entries left out without showing them", async () => {
    serve(() => rubric());
    renderPage();
    const summary = await screen.findByText("Read once from 3 copies of the same table · 2 entries left out");
    fireEvent.click(summary);
    expect(screen.getByText(/The same table appears on pages 1, 2, 3\. VERIDICAL read it once, from page 1\./)).toBeInTheDocument();
  });

  it("opens the level's page of the original and returns to the button that opened it", async () => {
    serve(() => rubric(), (url) => url.endsWith("/rubrics/5/document")
      ? new Response(JSON.stringify({ rubric_id: 5, rubric_family_id: "11111111-1111-1111-1111-111111111111", version: 1, available: true,
        source_format: "pdf", source_sha256: "a".repeat(64), unavailable_reason: null, reconstruction: "original_pdf" }), { status: 200 })
      : undefined);
    renderPage();
    const show = await screen.findByRole("button", { name: "Show page 1 of the original, Criterion 2, Proficient" });
    fireEvent.click(show);

    expect(await screen.findByTestId("pdf-pane")).toHaveTextContent("Requested page 1");
    fireEvent.click(screen.getByRole("button", { name: "Back to criteria" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Show page 1 of the original, Criterion 2, Proficient" })).toHaveFocus());
  });
});
