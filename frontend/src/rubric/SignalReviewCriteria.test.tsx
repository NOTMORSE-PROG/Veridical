import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useNavigate } from "react-router";
import type { Rubric } from "../api/types";
import { renderWithProviders } from "../test/renderWithProviders";
import { SignalReviewCriteriaPage } from "./SignalReviewCriteria";

const LEVELS = [
  { level: 1, name: "Emerging", descriptor: "The section is incomplete.", points: 1 },
  { level: 2, name: "Established", descriptor: "The section is complete and supported.", points: 2 },
];

const RUBRIC: Rubric = {
  id: 5,
  rubric_family_id: "11111111-1111-1111-1111-111111111111",
  version: 2,
  title: "T.I.P. Capstone Format",
  parse_status: "parsed",
  parse_issues: null,
  is_active: false,
  is_latest_version: true,
  criteria: [
    {
      id: 1,
      type: "structural",
      text: "Includes a complete abstract",
      evidence: "Abstract section",
      weight: 40,
      position: 0,
      levels: null,
    },
    {
      id: 2,
      type: "semantic",
      text: "Findings answer the research questions",
      evidence: "Results and discussion",
      weight: 60,
      position: 1,
      levels: LEVELS,
    },
  ],
};

function renderPage() {
  return renderWithProviders(<SignalReviewCriteriaPage />, {
    route: "/rubric/5/review",
    path: "/rubric/:rubricId/review", authInstructorId: 1,
  });
}

function VersionNavigationControl() {
  const navigate = useNavigate();
  return <button onClick={() => navigate("/rubric/6/review")}>Open next version</button>;
}

describe("SignalReviewCriteriaPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("presents the human review gate as editable, numbered criteria with WCAG 2.5.3 label-in-name accessible names (BUG-338)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 })));
    renderPage();

    expect(await screen.findByRole("heading", { name: "Review criteria" })).toBeInTheDocument();
    expect(await screen.findByLabelText("Criterion text, criterion 1")).toHaveValue("Includes a complete abstract");
    expect(screen.getByLabelText("Type, criterion 2")).toHaveValue("semantic");
    expect(screen.getByLabelText("Evidence expected, criterion 2")).toHaveValue("Results and discussion");
    expect(screen.getByLabelText("Relative weight, criterion 1")).toHaveValue("40");
    expect(screen.getByText("Levelled criterion · 2 levels")).toBeInTheDocument();
    expect(screen.getAllByText(/importance$/)).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Required format" })).toHaveAttribute("href", `/rubric?family=${RUBRIC.rubric_family_id}`);
    expect(screen.getByLabelText("Type, criterion 2")).toHaveAccessibleDescription(/Semantic for checks that require interpreting the content/);
  });

  it("saves the complete edited set as a draft and preserves level data", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") return new Response(JSON.stringify(RUBRIC), { status: 200 });
      return new Response(JSON.stringify(RUBRIC), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    const text = await screen.findByLabelText("Criterion text, criterion 1");
    fireEvent.change(text, { target: { value: "Includes an abstract within the required length" } });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/rubrics/5/criteria"),
      expect.objectContaining({ method: "PUT" }),
    ));
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    const body = JSON.parse(call?.[1]?.body as string) as {
      confirm: boolean;
      criteria: Array<{ text: string; levels: unknown }>;
    };
    expect(body.confirm).toBe(false);
    expect(body.criteria[0].text).toBe("Includes an abstract within the required length");
    expect(body.criteria[1].levels).toEqual(LEVELS);
  });

  // BUG-056: in-app navigation was correctly blocked by useBlocker (BUG-037),
  // but no `beforeunload` handler existed at all -- reload silently
  // discarded hand-corrected criteria with no warning, "asymmetric
  // protection worse than none" per the ticket's own framing. Asserts the
  // handler is registered once the form is dirty and removed once it's
  // clean again (matching the ticket's own literal Regression-test ask),
  // via the real browser event rather than inspecting React internals.
  it("BUG-056: warns before an unload once the form is dirty, and stops once clean again", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 })));
    renderPage();

    const text = await screen.findByLabelText("Criterion text, criterion 1");
    const original = text.getAttribute("value") ?? "Includes a complete abstract";

    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);

    fireEvent.change(text, { target: { value: "Includes an abstract within the required length" } });

    const dirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);

    fireEvent.change(text, { target: { value: original } });

    const cleanAgain = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(cleanAgain);
    expect(cleanAgain.defaultPrevented).toBe(false);
  });

  it("blocks confirmation and focuses a linked summary when required fields are invalid", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    const text = await screen.findByLabelText("Criterion text, criterion 1");
    fireEvent.change(text, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm and activate format" }));

    const summary = await screen.findByRole("alert");
    expect(summary).toHaveFocus();
    expect(screen.getByRole("button", { name: "Criterion 1 has no text." })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(["Save draft", "Confirm and activate format"])("%s rejects incomplete added criteria before sending a request", async (action) => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(RUBRIC), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await screen.findByLabelText("Criterion text, criterion 1");
    fireEvent.click(screen.getByRole("button", { name: "Add criterion" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Criterion text, criterion 3"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: action }));
    const summary = await screen.findByRole("alert");
    await waitFor(() => expect(summary).toHaveFocus());
    expect(screen.getByLabelText("Criterion text, criterion 3")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Relative weight, criterion 3")).toHaveAttribute("aria-invalid", "true");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(0);
    fireEvent.click(within(summary).getByRole("button", { name: "Criterion 3's weight must be greater than zero." }));
    expect(screen.getByLabelText("Relative weight, criterion 3")).toHaveFocus();
    fireEvent.change(screen.getByLabelText("Criterion text, criterion 3"), { target: { value: "New supported requirement" } });
    fireEvent.click(screen.getByRole("button", { name: action }));
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Relative weight, criterion 3"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: action }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(1));
    const body = JSON.parse(fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")![1]!.body as string);
    expect(body.confirm).toBe(action !== "Save draft");
    expect(body.criteria[2]).toMatchObject({ text: "New supported requirement", weight: 10, evidence: null });
    expect(body.criteria[1].levels).toEqual(LEVELS);
  });

  it.each(["Save draft", "Confirm and activate format"])("%s rejects an empty criterion set", async (action) => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await screen.findByLabelText("Criterion text, criterion 1");
    fireEvent.click(screen.getByRole("button", { name: "Remove criterion 2" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove criterion 1" }));
    fireEvent.click(screen.getByRole("button", { name: action }));
    const summary = await screen.findByRole("alert");
    await waitFor(() => expect(summary).toHaveFocus());
    expect(within(summary).getByRole("button", { name: "Add at least one criterion." })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("invalid save-and-leave closes the dialog, focuses errors, and keeps dirty edits", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(RUBRIC), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<><SignalReviewCriteriaPage /><VersionNavigationControl /></>, { route: "/rubric/5/review", path: "/rubric/:rubricId/review", authInstructorId: 1 });
    fireEvent.change(await screen.findByLabelText("Criterion text, criterion 1"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Open next version" }));
    fireEvent.click(await screen.findByRole("button", { name: "Save draft and leave" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("alert")).toHaveFocus());
    expect(screen.getByLabelText("Criterion text, criterion 1")).toHaveValue("");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Open next version" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("shows the server's refusal under the criterion it names, and drops it once that criterion is edited", async () => {
    const sentence = "This text contains an invisible character that can't be saved. Retype it or paste it as plain text.";
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => init?.method === "PUT"
      ? new Response(JSON.stringify({ detail: [{ type: "text_not_storable", loc: ["body", "criteria", 0, "text"], msg: sentence, input: "x" }] }), { status: 422 })
      : new Response(JSON.stringify(RUBRIC), { status: 200 })));
    renderWithProviders(<SignalReviewCriteriaPage />, { route: "/rubric/5/review", path: "/rubric/:rubricId/review", authInstructorId: 1 });
    const field = await screen.findByLabelText("Criterion text, criterion 1");
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(await screen.findByText("Nothing was saved. One value needs changing. Your edits are still on this page.", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Criterion 1, Criterion text" })).toBeInTheDocument();
    await waitFor(() => expect(field).toHaveAccessibleDescription(sentence));
    expect(field).toHaveAttribute("aria-invalid", "true");
    fireEvent.change(field, { target: { value: "Retyped criterion text" } });
    expect(field).not.toHaveAttribute("aria-invalid");
  });

  it("counts every refusal, keeps each level's reason, and names the criterion of one it cannot place", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => init?.method === "PUT"
      ? new Response(JSON.stringify({ detail: [
        { type: "string_too_long", loc: ["body", "criteria", 0, "levels", 0, "descriptor"], msg: "String should have at most 2000 characters", input: "x" },
        { type: "value_error", loc: ["body", "criteria", 0, "levels", 1, "name"], msg: "Value error, level names must not be blank", input: "" },
        { type: "greater_than", loc: ["body", "criteria", 0, "weight"], msg: "Input should be greater than 0", input: 0 },
      ] }), { status: 422 })
      : new Response(JSON.stringify(RUBRIC), { status: 200 })));
    renderWithProviders(<SignalReviewCriteriaPage />, { route: "/rubric/5/review", path: "/rubric/:rubricId/review", authInstructorId: 1 });
    await screen.findByLabelText("Criterion text, criterion 1");
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(await screen.findByText("Nothing was saved. 3 values need changing. Your edits are still on this page.", { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/Criterion 1, Relative weight: Some of the information sent was not accepted/)).toBeInTheDocument();
  });

  it("gives each refused field its own reason when one save is refused for several", async () => {
    const sentence = "This text contains an invisible character that can't be saved. Retype it or paste it as plain text.";
    const two = { ...RUBRIC, criteria: [RUBRIC.criteria[0], { ...RUBRIC.criteria[0], id: 99, text: "Second criterion" }] };
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => init?.method === "PUT"
      ? new Response(JSON.stringify({ detail: [
        { type: "text_not_storable", loc: ["body", "criteria", 0, "evidence"], msg: sentence, input: "x" },
        { type: "string_too_short", loc: ["body", "criteria", 1, "text"], msg: "String should have at least 1 character", input: "" },
      ] }), { status: 422 })
      : new Response(JSON.stringify(two), { status: 200 })));
    renderWithProviders(<SignalReviewCriteriaPage />, { route: "/rubric/5/review", path: "/rubric/:rubricId/review", authInstructorId: 1 });
    await screen.findByLabelText("Criterion text, criterion 2");
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(await screen.findByText("Nothing was saved. 2 values need changing. Your edits are still on this page.", { exact: false })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Evidence expected, criterion 1")).toHaveAccessibleDescription(sentence));
    expect(screen.getByLabelText("Criterion text, criterion 2")).toHaveAccessibleDescription("Some of the information sent was not accepted. Check what you entered and try again.");
    expect(screen.getByLabelText("Criterion text, criterion 2")).not.toHaveAccessibleDescription(sentence);
    fireEvent.click(screen.getByRole("button", { name: "Criterion 2, Criterion text" }));
    expect(screen.getByLabelText("Criterion text, criterion 2")).toHaveFocus();
    // Changing a different field of criterion 1 leaves its evidence refusal in place.
    fireEvent.change(screen.getByLabelText("Criterion text, criterion 1"), { target: { value: "Edited text" } });
    expect(screen.getByLabelText("Evidence expected, criterion 1")).toHaveAccessibleDescription(sentence);
    expect(screen.getByRole("button", { name: "Criterion 1, Evidence expected" })).toBeInTheDocument();
  });

  it.each([200, 503, 422])("valid save-and-leave handles HTTP %s without losing edits", async (status) => {
    const next = { ...RUBRIC, id: 6, criteria: [{ ...RUBRIC.criteria[0], text: "New version criterion" }] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") return new Response(JSON.stringify(status === 200 ? RUBRIC : {}), { status });
      return new Response(JSON.stringify(String(input).endsWith("/6") ? next : RUBRIC), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<><SignalReviewCriteriaPage /><VersionNavigationControl /></>, { route: "/rubric/5/review", path: "/rubric/:rubricId/review", authInstructorId: 1 });
    fireEvent.change(await screen.findByLabelText("Criterion text, criterion 1"), { target: { value: "Retain this corrected criterion" } });
    fireEvent.click(screen.getByRole("button", { name: "Open next version" }));
    fireEvent.click(await screen.findByRole("button", { name: "Save draft and leave" }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(1));
    const body = JSON.parse(fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")![1]!.body as string);
    expect(body.confirm).toBe(false);
    if (status === 200) {
      await waitFor(() => expect(screen.getByLabelText("Criterion text, criterion 1")).toHaveValue("New version criterion"));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    } else if (status === 422) {
      // A refusal names fields on the page, so the dialog closes and the page shows it.
      expect(await screen.findByText(/One or more criterion values were rejected/)).toBeInTheDocument();
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(screen.getByLabelText("Criterion text, criterion 1")).toHaveValue("Retain this corrected criterion");
    } else {
      const dialog = await screen.findByRole("dialog");
      expect(await within(dialog).findByText(/Request failed \(503\)/)).toBeInTheDocument();
      expect(screen.getByLabelText("Criterion text, criterion 1")).toHaveValue("Retain this corrected criterion");
    }
  });

  it("renders a history version read-only and explains traceability", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      ...RUBRIC,
      is_latest_version: false,
      is_active: false,
    }), { status: 200 })));
    renderPage();

    expect(await screen.findByText("Read-only version")).toBeInTheDocument();
    expect(screen.getByText("Includes a complete abstract")).toBeInTheDocument();
    expect(screen.queryByLabelText("Criterion text, criterion 1")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save draft" })).not.toBeInTheDocument();
    expect(screen.getByText(/Reports remain pinned to this history version/)).toBeInTheDocument();
  });

  it("locks the latest used version and offers a family-bound replacement", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      ...RUBRIC, is_active: true, can_edit_criteria: false,
      parse_status: "needs_review", parse_issues: ["Source wording needs review."],
    }), { status: 200 })));
    renderPage();
    expect(await screen.findByText(/A check already uses this version/)).toBeInTheDocument();
    expect(screen.getByText("Findings answer the research questions")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add criterion" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm and activate format" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Review and save any corrections/)).not.toBeInTheDocument();
    expect(screen.getByText(/upload a new version if corrections are needed/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Upload new version" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("loads fresh rows and scale when navigation changes the rubric identity", async () => {
    const replacement = { ...RUBRIC, id: 6, version: 3, criteria: [{
      ...RUBRIC.criteria[1], id: 22, text: "Replacement criterion from the new source", levels: LEVELS.slice(0, 1),
    }] };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(
      String(input).endsWith("/6") ? replacement : { ...RUBRIC, can_edit_criteria: false },
    ), { status: 200 })));
    renderWithProviders(<><SignalReviewCriteriaPage /><VersionNavigationControl /></>, {
      route: "/rubric/5/review", path: "/rubric/:rubricId/review", authInstructorId: 1,
    });
    expect(await screen.findByText("Includes a complete abstract")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open next version" }));
    expect(await screen.findByLabelText("Criterion text, criterion 1")).toHaveValue("Replacement criterion from the new source");
    expect(screen.getByText("Levelled criterion · 1 levels")).toBeInTheDocument();
    expect(screen.queryByText("Includes a complete abstract")).not.toBeInTheDocument();
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
  });

  it("BUG-316 & BUG-337: keeps focus on Save draft across saves, renders visible confirmation for all actions, and re-announces repeated saves", async () => {
    let resolveSave!: () => void;
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") {
        await new Promise<void>((resolve) => { resolveSave = resolve; });
        return new Response(JSON.stringify(RUBRIC), { status: 200 });
      }
      return new Response(JSON.stringify(RUBRIC), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    await screen.findByLabelText("Criterion text, criterion 1");

    // 1. Add criterion visible confirmation
    fireEvent.click(screen.getByRole("button", { name: "Add criterion" }));
    expect(screen.getByText("Criterion added. 3 criteria total.")).toBeInTheDocument();

    // 2. Remove criterion visible confirmation
    fireEvent.click(screen.getByRole("button", { name: "Remove criterion 3" }));
    expect(screen.getByText("Criterion removed.")).toBeInTheDocument();

    // 3. Set equal relative weights visible confirmation
    fireEvent.click(screen.getByRole("button", { name: "Set equal relative weights" }));
    expect(screen.getByText("Relative weights set equally across all criteria.")).toBeInTheDocument();

    // 4. Save draft keeps keyboard focus while pending and after completion, and re-announces on second save
    const saveDraftBtn = screen.getByRole("button", { name: "Save draft" });
    saveDraftBtn.focus();
    fireEvent.click(saveDraftBtn);
    await waitFor(() => expect(saveDraftBtn).toHaveAttribute("aria-busy", "true"));
    expect(saveDraftBtn).toHaveAttribute("aria-disabled", "true");
    expect(saveDraftBtn).not.toBeDisabled();
    expect(saveDraftBtn).toHaveFocus();

    await waitFor(() => expect(resolveSave).toBeTypeOf("function"));
    resolveSave();
    const firstSavedNode = await screen.findByText("Draft saved.");
    expect(firstSavedNode.closest(".signal-live-region")).toBeNull();
    expect(saveDraftBtn).toHaveFocus();

    // Second save re-mounts a fresh node in the live region so assistive technology re-announces
    resolveSave = undefined as unknown as () => void;
    fireEvent.click(saveDraftBtn);
    await waitFor(() => expect(resolveSave).toBeTypeOf("function"));
    resolveSave();
    await waitFor(() => {
      const secondSavedNode = screen.getByText("Draft saved.");
      expect(secondSavedNode).not.toBe(firstSavedNode);
    });
    expect(saveDraftBtn).toHaveFocus();
  });

  it("BUG-317: removing a criterion offers an Undo remove action that restores all fields at its original position with predictable focus", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 })));
    renderPage();

    await screen.findByLabelText("Criterion text, criterion 1");
    fireEvent.click(screen.getByRole("button", { name: "Remove criterion 1" }));

    const undoBtn = await screen.findByRole("button", { name: "Undo remove" });
    await waitFor(() => expect(undoBtn).toHaveFocus());
    expect(screen.queryByDisplayValue("Includes a complete abstract")).not.toBeInTheDocument();

    fireEvent.click(undoBtn);
    const restoredText = await screen.findByLabelText("Criterion text, criterion 1");
    expect(restoredText).toHaveValue("Includes a complete abstract");
    expect(screen.getByLabelText("Evidence expected, criterion 1")).toHaveValue("Abstract section");
    expect(screen.getByLabelText("Relative weight, criterion 1")).toHaveValue("40");
    await waitFor(() => expect(restoredText).toHaveFocus());
    expect(screen.queryByRole("button", { name: "Undo remove" })).not.toBeInTheDocument();
  });

  it("BUG-339: a 404 response from GET /rubrics/:id shows an honest unavailable state with a Required format link and no Try again button", async () => {
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ error: { code: "not_found", message: "Rubric not found" } }),
      { status: 404, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Format not available");
    expect(alert).toHaveTextContent("This format version does not exist or is not available to your account.");
    expect(screen.getByRole("link", { name: "Back to Required format" })).toHaveAttribute("href", "/rubric");
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
