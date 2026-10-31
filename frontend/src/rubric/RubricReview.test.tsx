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

// What the server sends for a level it could not place in the table.
const UNPLACED: RubricLevelSource = { match: "unmatched", page: null, table: null, row: null, col: null, cell_text: null, row_label: null, column_heading: null };

const ISSUE: RubricIssueDetail = {
  id: 11, criterion_id: 2, level: 3, code: "not_in_source",
  message: "'Purpose' level 'Proficient': the descriptor is not found in the document", status: "open", resolution: null,
};

const KEPT_BY = { kind: "acknowledged" as const, by_instructor_id: 1, by_name: "Ana Reyes", at: "2026-10-30T10:00:00Z" };
const NOT_IN_SOURCE_REASON = "It is in the document outside the table, for example in a note under it";
const OTHER_EXPLAINED = "Another reason, explained in the note";
const NOTE = "Checked against page 1, row Purpose";

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
          { level: 3, name: "Proficient", descriptor: "The paper's purpose is clearly stated.", points: 3, source: UNPLACED },
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

const level = (name: string) => screen.getByRole("heading", { level: 4, name: `Purpose, ${name}` });
const levelRow = (name: string) => level(name).closest("li")!;
const keepButton = () => screen.getByRole("button", { name: /^Keep this wording/ });
const status = () => document.querySelector(".signal-review-status")!;
const itemBar = () => screen.getByRole("navigation", { name: "Items to check" });

function renderPage() {
  return renderWithProviders(<SignalReviewCriteriaPage />, {
    route: "/rubric/5/review", path: "/rubric/:rubricId/review", authInstructorId: 1,
  });
}

// Keeps Purpose · Proficient (not_in_source) with a reason and a real note.
async function keepProficient(note = NOTE) {
  fireEvent.click(await screen.findByRole("button", { name: "Keep this wording, Purpose, Proficient" }));
  fireEvent.click(screen.getByRole("radio", { name: NOT_IN_SOURCE_REASON }));
  fireEvent.change(screen.getByLabelText("Note (required)"), { target: { value: note } });
  fireEvent.click(screen.getByRole("button", { name: /^Keep wording,/ }));
}

describe("rubric review before activation (BUG-566)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows each level against the document and asks only about the one that did not match", async () => {
    serve(() => rubric());
    renderPage();

    expect(await screen.findByText("1 item to check before this format can be activated.")).toBeInTheDocument();
    const proficient = levelRow("Proficient");
    expect(within(proficient).getByText("Check this")).toBeInTheDocument();
    expect(within(proficient).getByText("VERIDICAL could not find this wording in the document. Compare it with the original.")).toBeInTheDocument();
    // It cannot be placed, so it points at its criterion's row instead.
    expect(within(proficient).getByText('Page 1 · row "Purpose"')).toBeInTheDocument();
    expect(within(levelRow("Beginner")).getByText('Page 1 · row "Purpose" · column "BEGIN ER 1"')).toBeInTheDocument();
    expect(within(levelRow("Beginner")).getByText("Matches the document")).toBeInTheDocument();
    expect(within(levelRow("Beginner")).queryByRole("button", { name: /Keep this wording/ })).not.toBeInTheDocument();
    // The issue is shown once, on its level, not again as a raw parser list.
    expect(screen.queryByText(ISSUE.message)).not.toBeInTheDocument();
    expect(itemBar()).toHaveTextContent("1 item to check");
  });

  it("refuses activation while an item is open, without asking the server, and links to the item", async () => {
    const fetchMock = serve(() => rubric());
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Confirm and activate format" }));

    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("Check these items before activating");
    await waitFor(() => expect(summary).toHaveFocus());
    expect(puts(fetchMock)).toHaveLength(0);

    fireEvent.click(within(summary).getByRole("button", { name: /Purpose · Proficient/ }));
    // The focused heading names the criterion as well as the level.
    expect(level("Proficient")).toHaveFocus();
    expect(itemBar()).toHaveTextContent("Item 1 of 1 to check");
    expect(screen.getByText("Item 1 of 1 to check: Purpose · Proficient.")).toBeInTheDocument();
  });

  it("offers only reasons that fit the problem, and never says the document has wording it could not find", async () => {
    const misplaced: RubricIssueDetail = { ...ISSUE, id: 13, level: 1, code: "wrong_column", message: "Under another level." };
    serve(() => rubric({ parse_issue_details: [misplaced, ISSUE] }));
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Keep this wording, Purpose, Proficient" }));
    const form = screen.getByRole("form");
    expect(within(form).queryByRole("radio", { name: "The document says it this way" })).not.toBeInTheDocument();
    expect(within(form).getAllByRole("radio").map((radio) => radio.closest("label")!.textContent)).toEqual([NOT_IN_SOURCE_REASON, OTHER_EXPLAINED]);
    fireEvent.click(within(form).getByRole("button", { name: /^Cancel,/ }));

    fireEvent.click(screen.getByRole("button", { name: "Keep this wording, Purpose, Beginner" }));
    expect(screen.getByRole("radio", { name: "This level should be graded with that wording" })).toBeInTheDocument();
  });

  it("asks for a note with real content where the wording was not traced, and marks the field required", async () => {
    serve(() => rubric());
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Keep this wording, Purpose, Proficient" }));
    await waitFor(() => expect(screen.getByRole("radio", { name: NOT_IN_SOURCE_REASON })).toHaveFocus());
    fireEvent.click(screen.getByRole("button", { name: /^Keep wording,/ }));
    expect(screen.getByText("Choose why you are keeping this wording.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: NOT_IN_SOURCE_REASON }));
    fireEvent.click(screen.getByRole("button", { name: /^Keep wording,/ }));
    const note = screen.getByLabelText("Note (required)");
    expect(screen.getByText("Add a note saying what you compared.")).toBeInTheDocument();
    expect(note).toHaveFocus();
    expect(note).toHaveAccessibleDescription(/Say what you compared in the original/);

    fireEvent.change(note, { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: /^Keep wording,/ }));
    expect(screen.getByText("Write at least 12 letters or numbers saying what you compared.")).toBeInTheDocument();
    fireEvent.change(note, { target: { value: "... ok ... !!!" } });
    fireEvent.click(screen.getByRole("button", { name: /^Keep wording,/ }));
    expect(screen.getByText("Write at least 12 letters or numbers saying what you compared.")).toBeInTheDocument();

    fireEvent.change(note, { target: { value: NOTE } });
    fireEvent.click(screen.getByRole("button", { name: /^Keep wording,/ }));
    expect(within(levelRow("Proficient")).getByText("Kept as written")).toBeInTheDocument();
  });

  it("keeps wording, counts it as not saved yet, sends it with the revision, and can undo before saving", async () => {
    let current = rubric();
    const fetchMock = serve(() => current, (_url, init) => {
      if (init?.method !== "PUT") return undefined;
      current = rubric({
        revision: 5,
        parse_issue_details: [{ ...ISSUE, status: "acknowledged", resolution: { ...KEPT_BY, reason_code: "outside_table", note: NOTE } }],
      });
      return new Response(JSON.stringify(current), { status: 200 });
    });
    renderPage();
    await keepProficient();

    const proficient = levelRow("Proficient");
    expect(within(proficient).getByText("Kept as written")).toBeInTheDocument();
    expect(within(proficient).getByText(`Kept as written: ${NOT_IN_SOURCE_REASON} · “${NOTE}”. Not saved yet.`, { exact: false })).toBeInTheDocument();
    // What VERIDICAL raised stays on the level after it is kept.
    expect(within(proficient).getByText("VERIDICAL could not find this wording in the document. Compare it with the original.")).toBeInTheDocument();
    expect(screen.getByText("Levelled criterion · 2 levels · 1 kept as written, not saved yet")).toBeInTheDocument();
    expect(status()).toHaveTextContent("No items to check. 1 kept item is not saved yet: save a draft, or confirm and activate to save and activate together.");
    expect(itemBar()).toHaveTextContent("No items to check · 1 kept, not saved yet");

    fireEvent.click(within(proficient).getByRole("button", { name: "Undo keeping, Purpose, Proficient" }));
    expect(within(proficient).getByText("Check this")).toBeInTheDocument();
    await waitFor(() => expect(keepButton()).toHaveFocus());

    fireEvent.click(keepButton());
    fireEvent.click(screen.getByRole("radio", { name: OTHER_EXPLAINED }));
    fireEvent.change(screen.getByLabelText("Note (required)"), { target: { value: "  Matches the printed copy  " } });
    fireEvent.click(screen.getByRole("button", { name: /^Keep wording,/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));

    await waitFor(() => expect(puts(fetchMock)).toHaveLength(1));
    expect(puts(fetchMock)[0]).toMatchObject({
      confirm: false, expected_revision: 4,
      acknowledgements: [{ issue_id: 11, reason_code: "other", note: "Matches the printed copy" }],
    });
    expect(await screen.findByText(`Kept by Ana Reyes, Oct 30, 2026: ${NOT_IN_SOURCE_REASON}`, { exact: false })).toBeInTheDocument();
    expect(screen.getByText("Levelled criterion · 2 levels · 1 kept as written")).toBeInTheDocument();
    // parse_status still says how preparation went; the label reads the open items.
    expect(status()).toHaveTextContent("Flagged when prepared · all items checked. You can activate this format.");
    expect(status()).not.toHaveTextContent(/needs review/i);

    // The next save starts from the revision the server returned.
    fireEvent.click(screen.getByRole("button", { name: "Confirm and activate format" }));
    await waitFor(() => expect(puts(fetchMock)).toHaveLength(2));
    expect(puts(fetchMock)[1]).toMatchObject({ confirm: true, expected_revision: 5 });
    expect(puts(fetchMock)[1]).not.toHaveProperty("acknowledgements");
  });

  it("moves focus to the active-format status once Confirm and activate succeeds", async () => {
    let current = rubric({ parse_issue_details: [] , parse_status: "parsed" });
    serve(() => current, (_url, init) => {
      if (init?.method !== "PUT") return undefined;
      current = rubric({ revision: 5, is_active: true, parse_issue_details: [], parse_status: "parsed" });
      return new Response(JSON.stringify(current), { status: 200 });
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Confirm and activate format" }));
    const active = await screen.findByText("This format is active");
    await waitFor(() => expect(active.closest("[tabindex='-1']")).toHaveFocus());
    expect(screen.queryByRole("button", { name: "Confirm and activate format" })).not.toBeInTheDocument();
  });

  it("points to Save from the last item instead of a dead Next", async () => {
    serve(() => rubric());
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Go to first item" }));
    expect(itemBar()).toHaveTextContent("Item 1 of 1 to check");
    expect(within(itemBar()).queryByRole("button", { name: "Next item" })).not.toBeInTheDocument();
    fireEvent.click(within(itemBar()).getByRole("button", { name: "Last item · go to save" }));
    expect(screen.getByRole("button", { name: "Save draft" })).toHaveFocus();

    await keepProficient();
    fireEvent.click(within(levelRow("Proficient")).getByRole("button", { name: "Go to save, after Purpose, Proficient" }));
    expect(screen.getByRole("button", { name: "Save draft" })).toHaveFocus();
    fireEvent.click(within(status() as HTMLElement).getByRole("button", { name: "Go to save" }));
    expect(screen.getByRole("button", { name: "Save draft" })).toHaveFocus();
  });

  it("offers the next item from a level, with a name that says which level it moves on from", async () => {
    const beginner: RubricIssueDetail = { ...ISSUE, id: 13, level: 1, code: "wrong_column", message: "Under another level." };
    serve(() => rubric({ parse_issue_details: [beginner, ISSUE] }));
    renderPage();
    expect(await screen.findByText("2 items to check before this format can be activated.")).toBeInTheDocument();
    expect(screen.getByText(/You cannot correct a level's wording on this page yet\..*leave this format inactive and upload a corrected copy\./)).toBeInTheDocument();
    expect(within(levelRow("Beginner")).getByRole("button", { name: "Next item after Purpose, Beginner" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Keep this wording, Purpose, Beginner" }));
    fireEvent.click(screen.getByRole("radio", { name: "This level should be graded with that wording" }));
    fireEvent.change(screen.getByLabelText("Note (required)"), { target: { value: "The original has it under Beginner" } });
    fireEvent.click(screen.getByRole("button", { name: /^Keep wording,/ }));

    expect(within(levelRow("Beginner")).getByText("Kept as written")).toBeInTheDocument();
    fireEvent.click(within(levelRow("Beginner")).getByRole("button", { name: "Next item after Purpose, Beginner" }));
    expect(level("Proficient")).toHaveFocus();
    expect(screen.getByText("Item 1 of 1 to check: Purpose · Proficient.")).toBeInTheDocument();
  });

  it("says in plain words which level's wording a misplaced level would be graded with", async () => {
    const value = rubric();
    value.criteria[1].levels = [
      { level: 2, name: "Acceptable", descriptor: "The purpose is stated in a single sentence.", points: 2, source: cell(3, 4, "Purpose", "PROFICIEN T 3") },
      { level: 3, name: "Proficient", descriptor: "The paper's purpose is clearly stated.", points: 3, source: UNPLACED },
    ];
    const misplaced: RubricIssueDetail = { ...ISSUE, id: 13, level: 2, code: "wrong_column", message: "Under another level." };
    serve(() => ({ ...value, parse_issue_details: [misplaced] }));
    renderPage();
    const acceptable = (await screen.findByRole("heading", { level: 4, name: "Purpose, Acceptable" })).closest("li")!;
    expect(within(acceptable).getByText("This wording is in the document, but under Proficient, not Acceptable.")).toBeInTheDocument();
    fireEvent.click(within(acceptable).getByRole("button", { name: "Keep this wording, Purpose, Acceptable" }));
    expect(within(acceptable).getByText("Keeping it means Acceptable is graded with the wording the document gives Proficient. VERIDICAL will grade with this wording exactly as shown.")).toBeInTheDocument();
  });

  it("shows the server's refusal to activate as the same list, and retries from the revision the refusal saved", async () => {
    let current = rubric({ parse_issue_details: [{ ...ISSUE, status: "resolved", resolution: null }] });
    const fetchMock = serve(() => current, (_url, init) => {
      if (init?.method !== "PUT") return undefined;
      if (puts(fetchMock).length > 1) return new Response(JSON.stringify(current), { status: 200 });
      // The server keeps the edits at revision 5, then refuses to activate.
      // Another tab saves revision 6 before this page reads the format again.
      current = rubric({ revision: 6 });
      return new Response(JSON.stringify({ error: { code: "unresolved_rubric_issues", message: "Open items", details: { open_issue_ids: [11], revision: 5 } } }), { status: 409 });
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Confirm and activate format" }));

    const summary = await screen.findByRole("alert");
    await waitFor(() => expect(summary).toHaveTextContent("Purpose · Proficient"));
    expect(summary).toHaveTextContent("Check these items before activating");
    expect(screen.queryByText("Could not save these criteria")).not.toBeInTheDocument();
    expect(screen.getByText("Your changes were saved. The format was not activated.")).toBeInTheDocument();

    await keepProficient();
    fireEvent.click(screen.getByRole("button", { name: "Confirm and activate format" }));
    await waitFor(() => expect(puts(fetchMock)).toHaveLength(2));
    // Revision 5, from the refusal: taking 6 from the later read would
    // overwrite the other tab's save.
    expect(puts(fetchMock)[1]).toMatchObject({ confirm: true, expected_revision: 5, acknowledgements: [{ issue_id: 11, reason_code: "outside_table", note: NOTE }] });
  });

  it("asks at each note when the server refuses to keep wording without one, and saves nothing", async () => {
    const shape: RubricIssueDetail = { id: 14, criterion_id: 2, level: null, code: "not_one_row", message: "Levels from more than one row.", status: "open", resolution: null };
    let refuse = true;
    const fetchMock = serve(() => rubric({ parse_issue_details: [shape] }), (_url, init) => {
      if (init?.method !== "PUT") return undefined;
      if (refuse) {
        refuse = false;
        return new Response(JSON.stringify({ error: { code: "acknowledgement_note_required", message: "Add a note", details: { issue_ids: [14] } } }), { status: 422 });
      }
      return new Response(JSON.stringify(rubric({ revision: 5, parse_issue_details: [{ ...shape, status: "acknowledged", resolution: { ...KEPT_BY, reason_code: "document_wording", note: NOTE } }] })), { status: 200 });
    });
    renderPage();
    // This code keeps without a note on the page; the server asks for one.
    fireEvent.click(await screen.findByRole("button", { name: "Keep this wording, Purpose" }));
    expect(screen.getByLabelText("Note (optional)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "The document says it this way" }));
    fireEvent.click(screen.getByRole("button", { name: /^Keep wording,/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));

    await waitFor(() => expect(puts(fetchMock)).toHaveLength(1));
    const note = await screen.findByLabelText("Note (required)");
    expect(screen.getByText("Nothing was saved. Add a note saying what you compared before keeping this wording.")).toBeInTheDocument();
    await waitFor(() => expect(note).toHaveFocus());
    expect(screen.getByRole("radio", { name: "The document says it this way" })).toBeChecked();
    expect(screen.getByRole("heading", { name: "Add a note to this kept item" })).toBeInTheDocument();
    expect(screen.queryByText("Could not save these criteria")).not.toBeInTheDocument();

    fireEvent.change(note, { target: { value: NOTE } });
    fireEvent.click(screen.getByRole("button", { name: /^Keep wording,/ }));
    expect(screen.queryByRole("heading", { name: "Add a note to this kept item" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(puts(fetchMock)).toHaveLength(2));
    expect(puts(fetchMock)[1]).toMatchObject({ acknowledgements: [{ issue_id: 14, reason_code: "document_wording", note: NOTE }] });
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

  it("labels a locked active version with open items as the exception to correct in a new version, read-only", async () => {
    serve(() => rubric({ can_edit_criteria: false, is_active: true }));
    renderPage();
    expect(await screen.findByText(/^Active with items to check · correct it in a new version\. 1 item to check in this version\. This version is locked/)).toBeInTheDocument();
    expect(screen.getByText("Check this")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Keep this wording/ })).not.toBeInTheDocument();
  });

  it("falls back to the parser's notes for a format prepared before items were recorded", async () => {
    const legacy = rubric({ parse_issue_details: null, source_summary: null, revision: undefined, parse_status: "needs_review", parse_issues: ["Purpose levels may be shifted."] });
    legacy.criteria[1].levels = legacy.criteria[1].levels!.map(({ source: _source, ...item }) => item);
    serve(() => legacy);
    renderPage();
    expect(await screen.findByText(/prepared before VERIDICAL compared level descriptions with the document/)).toBeInTheDocument();
    expect(screen.getByText("Purpose levels may be shifted.")).toBeInTheDocument();
    expect(screen.getAllByText("Not checked against the document")).toHaveLength(2);
    expect(screen.queryByText("Matches the document")).not.toBeInTheDocument();
    expect(screen.queryByText(/Flagged when prepared|items to check/)).not.toBeInTheDocument();
  });

  it("says repeated copies were read once and counts the entries left out without showing them", async () => {
    serve(() => rubric());
    renderPage();
    const summary = await screen.findByText("Read once from 3 copies of the same table · 2 entries left out");
    fireEvent.click(summary);
    expect(screen.getByText(/The same table appears on pages 1, 2, 3\. VERIDICAL read it once, from page 1\./)).toBeInTheDocument();
  });

  it("opens the level's page with what to look for, and returns to the button that opened it", async () => {
    serve(() => rubric(), (url) => url.endsWith("/rubrics/5/document")
      ? new Response(JSON.stringify({ rubric_id: 5, rubric_family_id: "11111111-1111-1111-1111-111111111111", version: 1, available: true,
        source_format: "pdf", source_sha256: "a".repeat(64), unavailable_reason: null, reconstruction: "original_pdf" }), { status: 200 })
      : undefined);
    renderPage();
    const show = await screen.findByRole("button", { name: "Show page 1 of the original, Purpose, Proficient" });
    fireEvent.click(show);

    expect(await screen.findByTestId("pdf-pane")).toHaveTextContent("Requested page 1");
    const target = document.querySelector(".signal-source-target")!;
    expect(target).toHaveTextContent('Compare this wording · Page 1 · row "Purpose"');
    expect(target).toHaveTextContent("The paper's purpose is clearly stated.");
    fireEvent.click(screen.getByRole("button", { name: "Back to criteria" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Show page 1 of the original, Purpose, Proficient" })).toHaveFocus());

    fireEvent.click(screen.getByRole("button", { name: "Show page 1 of the original, Purpose, Beginner" }));
    await waitFor(() => expect(document.querySelector(".signal-source-target")).toHaveTextContent('Find this cell · Page 1 · row "Purpose" · column "BEGIN ER 1"'));
    expect(document.querySelector(".signal-source-target")).toHaveTextContent("cell");
  });

  it("does not claim it looked for a level it only skipped because another level in its row failed", async () => {
    const value = rubric();
    value.criteria[1].levels!.push({ level: 4, name: "Exemplary", descriptor: "The purpose is clearly and concisely stated.", points: 4, source: UNPLACED });
    serve(() => value);
    renderPage();
    const exemplary = (await screen.findByRole("heading", { level: 4, name: "Purpose, Exemplary" })).closest("li")!;
    expect(within(exemplary).getByText("Not checked separately")).toBeInTheDocument();
    expect(within(exemplary).getByText("Another level in this row did not match, so this one was not checked on its own.")).toBeInTheDocument();
    expect(within(exemplary).queryByText(/could not find this wording/)).not.toBeInTheDocument();
    expect(within(exemplary).queryByText("Check this")).not.toBeInTheDocument();
    expect(screen.getByText("1 item to check before this format can be activated.")).toBeInTheDocument();
  });

  it("asks the instructor to check a warning about the whole format, with a note, before activation", async () => {
    const note: RubricIssueDetail = {
      id: 12, criterion_id: null, level: null, code: "other", status: "open", resolution: null,
      message: "the parsed criteria reflect too little of the required format's substantive source text; compare them with the original document",
    };
    const checked = "Read pages 1 to 3 against all nine criteria";
    let current = rubric({ parse_issue_details: [note] });
    const fetchMock = serve(() => current, (_url, init) => {
      if (init?.method !== "PUT") return undefined;
      current = rubric({ revision: 5, is_active: true, parse_issue_details: [{ ...note, status: "acknowledged", resolution: { ...KEPT_BY, reason_code: "compared_whole_format", note: checked } }] });
      return new Response(JSON.stringify(current), { status: 200 });
    });
    renderPage();
    const section = (await screen.findByRole("heading", { name: "Items about the whole format" })).closest("section")!;
    // The server's clause reads as a sentence.
    expect(within(section).getByText("The parsed criteria reflect too little of the required format's substantive source text; compare them with the original document.")).toBeInTheDocument();
    expect(within(section).getByText("Page 1, the table these criteria were read from")).toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: /Keep this wording/ })).not.toBeInTheDocument();
    fireEvent.click(within(section).getByRole("button", { name: "Mark as checked, The whole format" }));
    // There is no wording to keep, so the form does not offer the wording reasons.
    expect(within(section).getByRole("group", { name: "What did you check?" })).toBeInTheDocument();
    expect(within(section).queryByRole("radio", { name: "The document says it this way" })).not.toBeInTheDocument();
    expect(within(section).queryByText(/grade with this wording/)).not.toBeInTheDocument();
    fireEvent.click(within(section).getByRole("radio", { name: "I compared the criteria with the original, and nothing in it is missing" }));
    // A reason alone is not enough: the note is the record of what was compared.
    fireEvent.click(within(section).getByRole("button", { name: /^Mark as checked,/ }));
    expect(within(section).getByText("Add a note saying what you compared.")).toBeInTheDocument();
    fireEvent.change(within(section).getByLabelText("Note (required)"), { target: { value: checked } });
    fireEvent.click(within(section).getByRole("button", { name: /^Mark as checked,/ }));
    expect(within(section).getAllByText("Marked as checked").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Confirm and activate format" }));
    await waitFor(() => expect(puts(fetchMock)).toHaveLength(1));
    expect(puts(fetchMock)[0]).toMatchObject({ confirm: true, acknowledgements: [{ issue_id: 12, reason_code: "compared_whole_format", note: checked }] });
    expect(await within(section).findByText(/Checked by Ana Reyes/)).toBeInTheDocument();
  });

  it("lists the whole-format item first, as the page shows it, and words a misplaced level the same in the summary", async () => {
    const formatNote: RubricIssueDetail = { id: 12, criterion_id: null, level: null, code: "other", message: "check the whole format", status: "open", resolution: null };
    const misplaced: RubricIssueDetail = { id: 13, criterion_id: 2, level: 1, code: "wrong_column", message: "m", status: "open", resolution: null };
    serve(() => rubric({
      parse_issue_details: [misplaced, formatNote],
      criteria: [
        { id: 1, type: "structural", text: "Introduction", evidence: null, weight: 1, position: 0, levels: null },
        {
          id: 2, type: "semantic", text: "Purpose", evidence: null, weight: 1, position: 1,
          levels: [
            { level: 1, name: "Beginner", descriptor: "The purpose is unclear.", points: 1, source: cell(2, 3, "Purpose", "ACCEPTABL E 2") },
            { level: 2, name: "Acceptable", descriptor: "Clear enough.", points: 2, source: cell(2, 4, "Purpose", "PROFICIEN T 3") },
          ],
        },
      ],
    }));
    renderPage();
    fireEvent.click(within(await screen.findByRole("navigation", { name: "Items to check" })).getByRole("button", { name: "Next item" }));
    await waitFor(() => expect(itemBar()).toHaveTextContent("Item 1 of 2 to check"));
    expect(document.activeElement).toBe(document.getElementById("criterion-row-issues-format"));
    fireEvent.click(screen.getByRole("button", { name: "Confirm and activate format" }));
    const items = within(await screen.findByRole("alert")).getAllByRole("button");
    expect(items[0]).toHaveTextContent("The whole format: Check the whole format.");
    expect(items[1]).toHaveTextContent("Purpose · Beginner: This wording is in the document, but under Acceptable, not Beginner.");
  });

  it("keeps a half-filled keep form open when another one is opened", async () => {
    const second: RubricIssueDetail = { ...ISSUE, id: 14, level: 1, code: "joins_cells" };
    serve(() => rubric({ parse_issue_details: [ISSUE, second] }));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Keep this wording, Purpose, Proficient" }));
    fireEvent.change(within(levelRow("Proficient")).getByLabelText("Note (required)"), { target: { value: NOTE } });
    fireEvent.click(screen.getByRole("button", { name: "Keep this wording, Purpose, Beginner" }));
    expect(within(levelRow("Proficient")).getByLabelText("Note (required)")).toHaveValue(NOTE);
    expect(within(levelRow("Beginner")).getByLabelText("Note (required)")).toHaveValue("");
    // With two forms open, each one's buttons still say which item they act on.
    expect(screen.getByRole("button", { name: "Keep wording, Purpose, Proficient" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel, Purpose, Beginner" })).toBeInTheDocument();
  });

  it("does not save changes to an active format while an item is open", async () => {
    const fetchMock = serve(() => rubric({ is_active: true }));
    renderPage();
    expect(await screen.findByText("Active with items to check · correct it in a new version. 1 item to check; changes can be saved once every item is kept.")).toBeInTheDocument();
    expect(screen.getByText(/upload a corrected copy of the format as a new version\./)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm and activate format" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save draft" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Criterion text, criterion 1"), { target: { value: "Introduction, edited" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("Check these items before saving changes to this active format");
    expect(summary).toHaveTextContent("Nothing was saved. Your edits are still on this page.");
    expect(puts(fetchMock)).toHaveLength(0);
  });

  it("keeps the edits and the kept wording when an active format's save is refused, and counts items this page has not seen", async () => {
    const fetchMock = serve(() => rubric({ is_active: true }), (_url, init) => init?.method === "PUT"
      ? new Response(JSON.stringify({ error: { code: "unresolved_rubric_issues", message: "Open", details: { open_issue_ids: [99], revision: 4 } } }), { status: 409 })
      : undefined);
    renderPage();
    await keepProficient();
    fireEvent.change(screen.getByLabelText("Criterion text, criterion 1"), { target: { value: "Introduction, edited" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(puts(fetchMock)).toHaveLength(1));
    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("Saving would also leave one level description that no longer matches the document.");
    expect(summary).toHaveTextContent("Introduction: criterion text changed");
    expect(screen.getByLabelText("Criterion text, criterion 1")).toHaveValue("Introduction, edited");
    expect(screen.getByText(new RegExp(`Kept as written: ${NOT_IN_SOURCE_REASON}`))).toBeInTheDocument();
    // The counters agree with the refusal instead of saying nothing is left.
    expect(status()).toHaveTextContent("Nothing was saved. These changes would leave 1 item to check; the list below names them.");
    expect(itemBar()).toHaveTextContent("Nothing was saved · 1 item to check");
    expect(itemBar()).not.toHaveTextContent("No items to check");
  });

  it("names the kept items a refused active-format save would reopen, links to each, and can discard the changes", async () => {
    const kept: RubricIssueDetail = { ...ISSUE, status: "acknowledged", resolution: { ...KEPT_BY, reason_code: "outside_table", note: NOTE } };
    const fetchMock = serve(() => rubric({ is_active: true, parse_status: "needs_review", parse_issue_details: [kept] }), (_url, init) => init?.method === "PUT"
      ? new Response(JSON.stringify({ error: { code: "unresolved_rubric_issues", message: "Open", details: { open_issue_ids: [11], revision: 4 } } }), { status: 409 })
      : undefined);
    renderPage();
    // Active with nothing open: flagged when prepared, never "needs review".
    expect(await screen.findByText("Flagged when prepared · all items checked.")).toBeInTheDocument();
    expect(screen.getByText(/This format is active\. Saved changes apply to new checks straight away\./)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Criterion text, criterion 1"), { target: { value: "Introduction, edited" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(puts(fetchMock)).toHaveLength(1));
    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("Your change would open an item you checked before again");
    expect(summary).toHaveTextContent("Introduction: criterion text changed");
    expect(summary).not.toHaveTextContent("no longer matches the document");
    expect(status()).not.toHaveTextContent("all items checked");
    // The kept item is open on the page again, to be decided in the same save.
    expect(within(levelRow("Proficient")).getByText("Check this")).toBeInTheDocument();
    expect(within(levelRow("Proficient")).getByText(/You kept this before\. The change you are saving would open it again/)).toBeInTheDocument();

    fireEvent.click(within(summary).getByRole("button", { name: /Purpose · Proficient/ }));
    expect(level("Proficient")).toHaveFocus();

    fireEvent.click(within(summary).getByRole("button", { name: "Discard my changes" }));
    const dialog = await screen.findByRole("dialog", { name: "Discard your changes?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Discard changes" }));
    await waitFor(() => expect(screen.getByLabelText("Criterion text, criterion 1")).toHaveValue("Introduction"));
    expect(screen.queryByText("Check these items before saving changes to this active format")).not.toBeInTheDocument();
  });

  it("lets the instructor check a reopened item again and save the change with it", async () => {
    const formatNote: RubricIssueDetail = {
      id: 12, criterion_id: null, level: null, code: "other", message: "the criteria reflect too little of the format",
      status: "acknowledged", resolution: { ...KEPT_BY, reason_code: "compared_whole_format", note: NOTE },
    };
    let current = rubric({ is_active: true, parse_issue_details: [formatNote] });
    const fetchMock = serve(() => current, (_url, init) => {
      if (init?.method !== "PUT") return undefined;
      const body = JSON.parse(String(init.body)) as { acknowledgements?: unknown[] };
      if (!body.acknowledgements) {
        return new Response(JSON.stringify({ error: { code: "unresolved_rubric_issues", message: "Open", details: { open_issue_ids: [12], revision: 4 } } }), { status: 409 });
      }
      current = rubric({ revision: 5, is_active: true, criteria: current.criteria.slice(1), parse_issue_details: [{ ...formatNote, resolution: { ...KEPT_BY, reason_code: "compared_whole_format", note: "Compared again without Introduction" } }] });
      return new Response(JSON.stringify(current), { status: 200 });
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Remove criterion 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(puts(fetchMock)).toHaveLength(1));
    expect(await screen.findByRole("alert")).toHaveTextContent("Your change would open an item you checked before again");
    const section = screen.getByRole("heading", { name: "Items about the whole format" }).closest("section")!;
    expect(within(section).getByText(/You checked this before\. The change you are saving would open it again/)).toBeInTheDocument();
    fireEvent.click(within(section).getByRole("button", { name: "Mark as checked, The whole format" }));
    fireEvent.click(within(section).getByRole("radio", { name: "I compared the criteria with the original, and nothing in it is missing" }));
    fireEvent.change(within(section).getByLabelText("Note (required)"), { target: { value: "Compared again without Introduction" } });
    fireEvent.click(within(section).getByRole("button", { name: /^Mark as checked,/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(puts(fetchMock)).toHaveLength(2));
    expect(puts(fetchMock)[1]).toMatchObject({
      criteria: [expect.objectContaining({ id: 2 })],
      acknowledgements: [{ issue_id: 12, reason_code: "compared_whole_format", note: "Compared again without Introduction" }],
    });
    expect(await within(section).findByText(/Checked by Ana Reyes/)).toBeInTheDocument();
    expect(within(section).queryByText(/would open it again/)).not.toBeInTheDocument();
  });

  it("brings the instructor back from the leave dialog to say the format changed elsewhere", async () => {
    serve(() => rubric(), (_url, init) => init?.method === "PUT"
      ? new Response(JSON.stringify({ error: { code: "rubric_changed", message: "Changed", details: { revision: 7 } } }), { status: 409 }) : undefined);
    renderPage();
    fireEvent.change(await screen.findByLabelText("Criterion text, criterion 1"), { target: { value: "Introduction, edited" } });
    fireEvent.click(screen.getByRole("link", { name: "Required format" }));
    const dialog = await screen.findByRole("dialog", { name: "Leave without saving your changes?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save draft and leave" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const alert = screen.getByText("This format changed in another tab or window. Your edits are still on this page but were not saved.");
    await waitFor(() => expect(alert.closest("[tabindex='-1']")).toHaveFocus());
    expect(screen.getByLabelText("Criterion text, criterion 1")).toHaveValue("Introduction, edited");
  });

  it("closes an untouched keep form with Escape and returns to its button", async () => {
    serve(() => rubric());
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Keep this wording, Purpose, Proficient" }));
    fireEvent.keyDown(screen.getByRole("radio", { name: OTHER_EXPLAINED }), { key: "Escape" });
    expect(screen.queryByRole("button", { name: /^Keep wording,/ })).not.toBeInTheDocument();
    await waitFor(() => expect(keepButton()).toHaveFocus());
  });

  it("asks before discarding a typed note, from Escape and from Cancel alike", async () => {
    serve(() => rubric());
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Keep this wording, Purpose, Proficient" }));
    fireEvent.change(screen.getByLabelText("Note (required)"), { target: { value: "Half a thought" } });

    fireEvent.keyDown(screen.getByLabelText("Note (required)"), { key: "Escape" });
    expect(screen.getByText("Discard what you entered?")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /^Keep editing,/ })).toHaveFocus());
    fireEvent.click(screen.getByRole("button", { name: /^Keep editing,/ }));
    expect(screen.getByLabelText("Note (required)")).toHaveValue("Half a thought");
    expect(screen.getByLabelText("Note (required)")).toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: /^Cancel,/ }));
    expect(screen.getByText("Discard what you entered?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Discard,/ }));
    expect(screen.queryByLabelText("Note (required)")).not.toBeInTheDocument();
    await waitFor(() => expect(keepButton()).toHaveFocus());
  });

  it("keeps a row note without a note unless the reason is Other", async () => {
    const leftOut: RubricIssueDetail = { id: 14, criterion_id: 2, level: null, code: "level_left_out", message: "Row has text under Acceptable.", status: "open", resolution: null };
    const fetchMock = serve(() => rubric({ parse_issue_details: [leftOut] }));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Keep this wording, Purpose" }));
    expect(screen.getByLabelText("Note (optional)")).toBeInTheDocument();
    expect(screen.queryByText(/Say what you compared in the original/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Other reason" }));
    expect(screen.getByLabelText("Note (required)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Keep wording,/ }));
    expect(screen.getByText("Add a note for this reason.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "The document says it this way" }));
    fireEvent.click(screen.getByRole("button", { name: /^Keep wording,/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(puts(fetchMock)).toHaveLength(1));
    expect(puts(fetchMock)[0]).toMatchObject({ acknowledgements: [{ issue_id: 14, reason_code: "document_wording", note: null }] });
  });
});
