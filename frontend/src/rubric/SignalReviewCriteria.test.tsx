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

  it("presents the human review gate as editable, numbered criteria", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 })));
    renderPage();

    expect(await screen.findByRole("heading", { name: "Review prepared criteria" })).toBeInTheDocument();
    expect(await screen.findByLabelText("Criterion 1 text")).toHaveValue("Includes a complete abstract");
    expect(screen.getByLabelText("Criterion 2 type")).toHaveValue("semantic");
    expect(screen.getByLabelText("Criterion 2 evidence")).toHaveValue("Results and discussion");
    expect(screen.getByText("Levelled criterion · 2 levels")).toBeInTheDocument();
    expect(screen.getAllByText(/importance$/)).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Rubric Studio" })).toHaveAttribute("href", `/rubric?family=${RUBRIC.rubric_family_id}`);
    expect(screen.getByLabelText("Criterion 2 type")).toHaveAccessibleDescription(/Semantic for checks that require interpreting the content/);
  });

  it("saves the complete edited set as a draft and preserves level data", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") return new Response(JSON.stringify(RUBRIC), { status: 200 });
      return new Response(JSON.stringify(RUBRIC), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    const text = await screen.findByLabelText("Criterion 1 text");
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

    const text = await screen.findByLabelText("Criterion 1 text");
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

    const text = await screen.findByLabelText("Criterion 1 text");
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
    await screen.findByLabelText("Criterion 1 text");
    fireEvent.click(screen.getByRole("button", { name: "Add criterion" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Criterion 3 text"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: action }));
    const summary = await screen.findByRole("alert");
    await waitFor(() => expect(summary).toHaveFocus());
    expect(screen.getByLabelText("Criterion 3 text")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Criterion 3 weight")).toHaveAttribute("aria-invalid", "true");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(0);
    fireEvent.click(within(summary).getByRole("button", { name: "Criterion 3's weight must be greater than zero." }));
    expect(screen.getByLabelText("Criterion 3 weight")).toHaveFocus();
    fireEvent.change(screen.getByLabelText("Criterion 3 text"), { target: { value: "New supported requirement" } });
    fireEvent.click(screen.getByRole("button", { name: action }));
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Criterion 3 weight"), { target: { value: "10" } });
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
    await screen.findByLabelText("Criterion 1 text");
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
    fireEvent.change(await screen.findByLabelText("Criterion 1 text"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Open next version" }));
    fireEvent.click(await screen.findByRole("button", { name: "Save draft and leave" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("alert")).toHaveFocus());
    expect(screen.getByLabelText("Criterion 1 text")).toHaveValue("");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Open next version" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it.each([200, 503, 422])("valid save-and-leave handles HTTP %s without losing edits", async (status) => {
    const next = { ...RUBRIC, id: 6, criteria: [{ ...RUBRIC.criteria[0], text: "New version criterion" }] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") return new Response(JSON.stringify(status === 200 ? RUBRIC : {}), { status });
      return new Response(JSON.stringify(String(input).endsWith("/6") ? next : RUBRIC), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<><SignalReviewCriteriaPage /><VersionNavigationControl /></>, { route: "/rubric/5/review", path: "/rubric/:rubricId/review", authInstructorId: 1 });
    fireEvent.change(await screen.findByLabelText("Criterion 1 text"), { target: { value: "Retain this corrected criterion" } });
    fireEvent.click(screen.getByRole("button", { name: "Open next version" }));
    fireEvent.click(await screen.findByRole("button", { name: "Save draft and leave" }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(1));
    const body = JSON.parse(fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")![1]!.body as string);
    expect(body.confirm).toBe(false);
    if (status === 200) {
      await waitFor(() => expect(screen.getByLabelText("Criterion 1 text")).toHaveValue("New version criterion"));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    } else {
      const dialog = await screen.findByRole("dialog");
      const expected = status === 422 ? /One or more criterion values were rejected/ : /Request failed \(503\)/;
      expect(await within(dialog).findByText(expected)).toBeInTheDocument();
      expect(screen.getByLabelText("Criterion 1 text")).toHaveValue("Retain this corrected criterion");
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
    expect(screen.queryByLabelText("Criterion 1 text")).not.toBeInTheDocument();
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
    expect(await screen.findByLabelText("Criterion 1 text")).toHaveValue("Replacement criterion from the new source");
    expect(screen.getByText("Levelled criterion · 1 levels")).toBeInTheDocument();
    expect(screen.queryByText("Includes a complete abstract")).not.toBeInTheDocument();
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
  });
});
