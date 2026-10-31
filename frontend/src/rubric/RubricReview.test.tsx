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

const beginnerRow = () => screen.getByRole("heading", { level: 4, name: "Beginner" }).closest("li")!;
const keepButton = () => screen.getByRole("button", { name: /^Keep this wording/ });

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
    // It cannot be placed, so it points at its criterion's row instead.
    expect(within(proficient).getByText('Page 1 · row "Purpose"')).toBeInTheDocument();
    expect(within(beginnerRow()).getByText('Page 1 · row "Purpose" · column "BEGIN ER 1"')).toBeInTheDocument();
    expect(within(beginnerRow()).getByText("Matches the document")).toBeInTheDocument();
    expect(within(beginnerRow()).queryByRole("button", { name: /Keep this wording/ })).not.toBeInTheDocument();
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

    fireEvent.click(within(summary).getByRole("button", { name: /Purpose · Proficient/ }));
    expect(screen.getByRole("heading", { level: 4, name: "Proficient" })).toHaveFocus();
    expect(screen.getByRole("navigation", { name: "Items to check" })).toHaveTextContent("Item 1 of 1");
    expect(screen.getByText("Item 1 of 1: Purpose · Proficient.")).toBeInTheDocument();
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

    fireEvent.click(await screen.findByRole("button", { name: "Keep this wording, Purpose, Proficient" }));
    expect(screen.getByText("VERIDICAL will grade with this wording exactly as shown.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("radio", { name: "The document says it this way" })).toHaveFocus());
    fireEvent.click(screen.getByRole("button", { name: "Keep wording" }));
    expect(screen.getByText("Choose why you are keeping this wording.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "The document says it this way" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep wording" }));
    // Wording VERIDICAL could not find needs a note saying what was compared.
    expect(screen.getByText("Add a note saying what you compared.")).toBeInTheDocument();
    expect(screen.getByLabelText("Note")).toHaveFocus();
    expect(screen.getByLabelText("Note")).toHaveAccessibleDescription(/Say what you compared in the original/);
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "Same as page 1, Proficient" } });
    fireEvent.click(screen.getByRole("button", { name: "Keep wording" }));

    const proficient = screen.getByRole("heading", { level: 4, name: "Proficient" }).closest("li")!;
    expect(within(proficient).getByText("Kept as written")).toBeInTheDocument();
    expect(screen.getByText("Levelled criterion · 2 levels · 1 kept as written")).toBeInTheDocument();
    expect(within(proficient).getByText("Kept as written: The document says it this way. Not saved yet.", { exact: false })).toBeInTheDocument();
    // What VERIDICAL raised stays on the level after it is kept.
    expect(within(proficient).getByText("VERIDICAL could not find this wording in the document. Compare it with the original.")).toBeInTheDocument();
    expect(screen.getByText("All 1 item kept or settled. You can activate this format.")).toBeInTheDocument();

    fireEvent.click(within(proficient).getByRole("button", { name: "Undo keeping, Purpose, Proficient" }));
    expect(within(proficient).getByText("Check this")).toBeInTheDocument();
    await waitFor(() => expect(keepButton()).toHaveFocus());

    fireEvent.click(keepButton());
    fireEvent.click(screen.getByRole("radio", { name: "Other reason" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep wording" }));
    expect(screen.getByText("Add a note saying what you compared.")).toBeInTheDocument();
    expect(screen.getByLabelText("Note")).toHaveFocus();
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "  Matches the printed copy  " } });
    fireEvent.click(screen.getByRole("button", { name: "Keep wording" }));
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));

    await waitFor(() => expect(puts(fetchMock)).toHaveLength(1));
    expect(puts(fetchMock)[0]).toMatchObject({
      confirm: false, expected_revision: 4,
      acknowledgements: [{ issue_id: 11, reason_code: "other", note: "Matches the printed copy" }],
    });
    expect(await screen.findByText("Kept by Ana Reyes, Oct 30, 2026: The document says it this way", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("Levelled criterion · 2 levels · 1 kept as written")).toBeInTheDocument();

    // The next save starts from the revision the server returned.
    fireEvent.click(screen.getByRole("button", { name: "Confirm and activate format" }));
    await waitFor(() => expect(puts(fetchMock)).toHaveLength(2));
    expect(puts(fetchMock)[1]).toMatchObject({ confirm: true, expected_revision: 5 });
    expect(puts(fetchMock)[1]).not.toHaveProperty("acknowledgements");
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

    fireEvent.click(keepButton());
    fireEvent.click(screen.getByRole("radio", { name: "The document says it this way" }));
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "Checked against page 1" } });
    fireEvent.click(screen.getByRole("button", { name: "Keep wording" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm and activate format" }));
    await waitFor(() => expect(puts(fetchMock)).toHaveLength(2));
    // Revision 5, from the refusal: taking 6 from the later read would
    // overwrite the other tab's save.
    expect(puts(fetchMock)[1]).toMatchObject({ confirm: true, expected_revision: 5, acknowledgements: [{ issue_id: 11, reason_code: "document_wording", note: "Checked against page 1" }] });
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
    expect(screen.queryByRole("button", { name: /Keep this wording/ })).not.toBeInTheDocument();
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
    const show = await screen.findByRole("button", { name: "Show page 1 of the original, Purpose, Proficient" });
    fireEvent.click(show);

    expect(await screen.findByTestId("pdf-pane")).toHaveTextContent("Requested page 1");
    fireEvent.click(screen.getByRole("button", { name: "Back to criteria" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Show page 1 of the original, Purpose, Proficient" })).toHaveFocus());
  });

  it("does not claim it looked for a level it only skipped because another level in its row failed", async () => {
    const value = rubric();
    value.criteria[1].levels!.push({ level: 4, name: "Exemplary", descriptor: "The purpose is clearly and concisely stated.", points: 4, source: UNPLACED });
    serve(() => value);
    renderPage();
    const exemplary = (await screen.findByRole("heading", { level: 4, name: "Exemplary" })).closest("li")!;
    expect(within(exemplary).getByText("Not checked separately")).toBeInTheDocument();
    expect(within(exemplary).getByText("Another level in this row did not match, so this one was not checked on its own.")).toBeInTheDocument();
    expect(within(exemplary).queryByText(/could not find this wording/)).not.toBeInTheDocument();
    expect(within(exemplary).queryByText("Check this")).not.toBeInTheDocument();
    expect(screen.getByText("1 item to check before this format can be activated")).toBeInTheDocument();
  });

  it("lets the instructor keep a note about the whole format, which otherwise blocks activation", async () => {
    const note: RubricIssueDetail = { id: 12, criterion_id: null, level: null, code: "other", message: "Text differs between the copies of the table on pages 2 and 3.", status: "open", resolution: null };
    let current = rubric({ parse_issue_details: [note] });
    const fetchMock = serve(() => current, (_url, init) => {
      if (init?.method !== "PUT") return undefined;
      current = rubric({ revision: 5, is_active: true, parse_issue_details: [{ ...note, status: "acknowledged", resolution: { kind: "acknowledged", reason_code: "outside_table", note: null, by_instructor_id: 1, by_name: "Ana Reyes", at: "2026-10-30T10:00:00Z" } }] });
      return new Response(JSON.stringify(current), { status: 200 });
    });
    renderPage();
    const section = (await screen.findByRole("heading", { name: "Items about the whole format" })).closest("section")!;
    expect(within(section).getByText(note.message)).toBeInTheDocument();
    fireEvent.click(within(section).getByRole("button", { name: "Keep this wording, The whole format" }));
    fireEvent.click(screen.getByRole("radio", { name: "The wording comes from outside the table" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep wording" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm and activate format" }));
    await waitFor(() => expect(puts(fetchMock)).toHaveLength(1));
    expect(puts(fetchMock)[0]).toMatchObject({ confirm: true, acknowledgements: [{ issue_id: 12, reason_code: "outside_table", note: null }] });
  });

  it("does not save changes to an active format while an item is open", async () => {
    const fetchMock = serve(() => rubric({ is_active: true }));
    renderPage();
    expect(await screen.findByText("1 item to check in this active format. Changes can be saved once every item is kept.")).toBeInTheDocument();
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

  it("keeps the edits and the kept wording when an active format's save is refused, and names items this page has not seen", async () => {
    const fetchMock = serve(() => rubric({ is_active: true }), (_url, init) => init?.method === "PUT"
      ? new Response(JSON.stringify({ error: { code: "unresolved_rubric_issues", message: "Open", details: { open_issue_ids: [99], revision: 4 } } }), { status: 409 })
      : undefined);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Keep this wording, Purpose, Proficient" }));
    fireEvent.click(screen.getByRole("radio", { name: "The document says it this way" }));
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "Checked against page 1" } });
    fireEvent.click(screen.getByRole("button", { name: "Keep wording" }));
    fireEvent.change(screen.getByLabelText("Criterion text, criterion 1"), { target: { value: "Introduction, edited" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(puts(fetchMock)).toHaveLength(1));
    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("Saving these changes would also leave one level description that no longer matches the document.");
    expect(screen.getByLabelText("Criterion text, criterion 1")).toHaveValue("Introduction, edited");
    expect(screen.getByText(/Kept as written: The document says it this way\. Not saved yet\./)).toBeInTheDocument();
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

  it("closes the keep form with Escape and returns to its button", async () => {
    serve(() => rubric());
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Keep this wording, Purpose, Proficient" }));
    fireEvent.keyDown(screen.getByRole("radio", { name: "Other reason" }), { key: "Escape" });
    expect(screen.queryByRole("button", { name: "Keep wording" })).not.toBeInTheDocument();
    await waitFor(() => expect(keepButton()).toHaveFocus());
  });

  it("keeps the form open on Escape once something has been entered", async () => {
    serve(() => rubric());
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Keep this wording, Purpose, Proficient" }));
    fireEvent.click(screen.getByRole("radio", { name: "Other reason" }));
    fireEvent.keyDown(screen.getByRole("radio", { name: "Other reason" }), { key: "Escape" });
    expect(screen.getByRole("button", { name: "Keep wording" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Other reason" })).toBeChecked();
  });

  it("keeps a row note without a note unless the reason is Other", async () => {
    const leftOut: RubricIssueDetail = { id: 14, criterion_id: 2, level: null, code: "level_left_out", message: "Row has text under Acceptable.", status: "open", resolution: null };
    const fetchMock = serve(() => rubric({ parse_issue_details: [leftOut] }));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Keep this wording, Purpose" }));
    expect(screen.getByLabelText("Note (optional)")).toBeInTheDocument();
    expect(screen.queryByText(/Say what you compared in the original/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Other reason" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep wording" }));
    expect(screen.getByText("Add a note for this reason.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "The document says it this way" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep wording" }));
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(puts(fetchMock)).toHaveLength(1));
    expect(puts(fetchMock)[0]).toMatchObject({ acknowledgements: [{ issue_id: 14, reason_code: "document_wording", note: null }] });
  });

  it("offers the next item from a level that was just kept", async () => {
    const beginner: RubricIssueDetail = { ...ISSUE, id: 13, level: 1, code: "wrong_column", message: "Under another level." };
    serve(() => rubric({ parse_issue_details: [beginner, ISSUE] }));
    renderPage();
    expect(await screen.findByText("2 items to check before this format can be activated")).toBeInTheDocument();
    expect(screen.getByText(/You cannot correct a level's wording on this page yet\..*leave this format inactive and upload a corrected copy\./)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Keep this wording, Purpose, Beginner" }));
    fireEvent.click(screen.getByRole("radio", { name: "The document says it this way" }));
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "The original has it under Beginner" } });
    fireEvent.click(screen.getByRole("button", { name: "Keep wording" }));

    expect(within(beginnerRow()).getByText("Kept as written")).toBeInTheDocument();
    fireEvent.click(within(beginnerRow()).getByRole("button", { name: "Next item" }));
    expect(screen.getByRole("heading", { level: 4, name: "Proficient" })).toHaveFocus();
    expect(screen.getByText("Item 1 of 1: Purpose · Proficient.")).toBeInTheDocument();
  });

  it("says when a refused save on an active format would reopen wording that was kept", async () => {
    const kept: RubricIssueDetail = { ...ISSUE, status: "acknowledged", resolution: { kind: "acknowledged", reason_code: "document_wording", note: "Checked", by_instructor_id: 1, by_name: "Ana Reyes", at: "2026-10-30T10:00:00Z" } };
    const fetchMock = serve(() => rubric({ is_active: true, parse_issue_details: [kept] }), (_url, init) => init?.method === "PUT"
      ? new Response(JSON.stringify({ error: { code: "unresolved_rubric_issues", message: "Open", details: { open_issue_ids: [11], revision: 4 } } }), { status: 409 })
      : undefined);
    renderPage();
    expect(await screen.findByText(/This format is active\. Saved changes apply to new checks straight away\./)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Criterion text, criterion 1"), { target: { value: "Introduction, edited" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(puts(fetchMock)).toHaveLength(1));
    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("Saving these changes would reopen an item you kept");
    expect(summary).not.toHaveTextContent("no longer matches the document");
    expect(screen.getByLabelText("Criterion text, criterion 1")).toHaveValue("Introduction, edited");
  });
});
