import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
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
    path: "/rubric/:rubricId/review",
  });
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

  it("renders a history version read-only and explains traceability", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      ...RUBRIC,
      is_latest_version: false,
      is_active: false,
    }), { status: 200 })));
    renderPage();

    expect(await screen.findByText("Read-only version")).toBeInTheDocument();
    expect(screen.getByLabelText("Criterion 1 text")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save draft" })).not.toBeInTheDocument();
    expect(screen.getByText(/Reports remain pinned to this history version/)).toBeInTheDocument();
  });
});
