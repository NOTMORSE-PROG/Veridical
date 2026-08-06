import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Rubric } from "../api/types";
import { renderWithProviders } from "../test/renderWithProviders";
import { ReviewCriteriaPage } from "./ReviewCriteria";

const RUBRIC: Rubric = {
  id: 5,
  rubric_family_id: "11111111-1111-1111-1111-111111111111",
  version: 1,
  title: "CS-Capstone-Format.pdf",
  parse_status: "parsed",
  parse_issues: null,
  is_active: false,
  is_latest_version: true,
  criteria: [
    { id: 1, type: "structural", text: "Has an abstract", evidence: "Abstract present", weight: 40, position: 0 },
    { id: 2, type: "semantic", text: "Argument is well developed", evidence: "Ch. 4", weight: 60, position: 1 },
  ],
};

describe("ReviewCriteriaPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the parsed criteria — text, type, evidence, weight", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 })),
    );
    renderWithProviders(<ReviewCriteriaPage />, {
      route: "/rubric/5/review",
      path: "/rubric/:rubricId/review",
    });

    expect((await screen.findAllByDisplayValue("Has an abstract")).length).toBeGreaterThan(0);
    expect(screen.getAllByDisplayValue("Argument is well developed").length).toBeGreaterThan(0);
    expect(screen.getAllByDisplayValue("Abstract present").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2 criteria")[0]).toBeInTheDocument();
    expect(screen.getAllByText(/Weights total 100%/)[0]).toBeInTheDocument();
  });

  it("shows the needs-review banner with the parse issues when the parse was partial (V-011)", async () => {
    const partial: Rubric = {
      ...RUBRIC,
      parse_status: "needs_review",
      parse_issues: ["only 40% of the source text is reflected in the parsed criteria"],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(partial), { status: 200 })),
    );
    renderWithProviders(<ReviewCriteriaPage />, {
      route: "/rubric/5/review",
      path: "/rubric/:rubricId/review",
    });

    expect(await screen.findByText("Needs manual completion")).toBeInTheDocument();
    expect(
      screen.getByText("only 40% of the source text is reflected in the parsed criteria"),
    ).toBeInTheDocument();
  });

  it("editing a field then saving sends the edit in the PUT body (edit round-trip)", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (init?.method === "PUT") {
        return new Response(JSON.stringify({ ...RUBRIC, is_active: false }), { status: 200 });
      }
      if (url.includes("/rubrics/5")) {
        return new Response(JSON.stringify(RUBRIC), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ReviewCriteriaPage />, {
      route: "/rubric/5/review",
      path: "/rubric/:rubricId/review",
    });

    const textInputs = await screen.findAllByDisplayValue("Has an abstract");
    fireEvent.change(textInputs[0], { target: { value: "Has an abstract of at most 250 words" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save draft" })[0]);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/rubrics/5/criteria"),
      expect.objectContaining({ method: "PUT" }),
    ));
    const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
    const body = JSON.parse((putCall![1] as RequestInit).body as string) as {
      criteria: Array<{ text: string }>;
      confirm: boolean;
    };
    expect(body.criteria[0].text).toBe("Has an abstract of at most 250 words");
    expect(body.confirm).toBe(false);
  });

  it("removing every row shows an accessible, focused error summary instead of disabling Save", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 })),
    );
    renderWithProviders(<ReviewCriteriaPage />, {
      route: "/rubric/5/review",
      path: "/rubric/:rubricId/review",
    });

    await screen.findAllByDisplayValue("Has an abstract");
    fireEvent.click(screen.getAllByRole("button", { name: "Remove criterion 1" })[0]);
    fireEvent.click(screen.getAllByRole("button", { name: "Remove criterion 1" })[0]); // list shifted up

    // Save/Confirm stay enabled — clicking is what surfaces the problem,
    // via a focused error summary (GOV.UK pattern) rather than a
    // silently-disabled button a screen-reader user can't explain.
    fireEvent.click(screen.getAllByRole("button", { name: "Save draft" })[0]);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Add at least one criterion.");
    expect(alert).toHaveFocus();
  });

  it("Normalize rescales weights to sum to 100", async () => {
    const unbalanced: Rubric = {
      ...RUBRIC,
      criteria: [
        { ...RUBRIC.criteria[0], weight: 10 },
        { ...RUBRIC.criteria[1], weight: 10 },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(unbalanced), { status: 200 })),
    );
    renderWithProviders(<ReviewCriteriaPage />, {
      route: "/rubric/5/review",
      path: "/rubric/:rubricId/review",
    });

    await screen.findAllByDisplayValue("Has an abstract");
    expect(screen.getAllByText(/Weights total 20%/)[0]).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Normalize to 100%" })[0]);
    expect(await screen.findAllByText(/Weights total 100%/)).not.toHaveLength(0);
  });

  it("renders read-only when a newer version supersedes this one (V-013 F2.4)", async () => {
    const superseded: Rubric = { ...RUBRIC, is_latest_version: false };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(superseded), { status: 200 })),
    );
    renderWithProviders(<ReviewCriteriaPage />, {
      route: "/rubric/5/review",
      path: "/rubric/:rubricId/review",
    });

    const textInputs = await screen.findAllByDisplayValue("Has an abstract");
    for (const input of textInputs) expect(input).toBeDisabled();
    expect(screen.getByText(/read-only history/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save draft" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm & activate rubric" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove criterion 1" })).not.toBeInTheDocument();
  });

  it("BUG-021 regression guard: the fillable grid columns use minmax(0,1fr), not a bare 1fr, and every text field fills its column", async () => {
    // jsdom doesn't compute real layout, so this can't assert actual
    // pixel widths (that's what the live Playwright pass against the
    // running app is for — see RESEARCH.md §24). This guards the exact
    // literal regression BUG-021 was: a bare `1fr` track whose minimum
    // is its content's intrinsic size, not the flexible width, silently
    // reappearing in a future edit.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 })),
    );
    renderWithProviders(<ReviewCriteriaPage />, {
      route: "/rubric/5/review",
      path: "/rubric/:rubricId/review",
    });

    await screen.findAllByDisplayValue("Has an abstract");
    const table = document.querySelector('[role="table"]');
    expect(table?.innerHTML).toContain("minmax(0,1fr)_192px_minmax(0,1fr)");
    for (const el of screen.getAllByLabelText("Criterion 1 text")) {
      expect(el.className).toContain("w-full");
    }
    for (const el of screen.getAllByLabelText("Criterion 1 evidence")) {
      expect(el.className).toContain("w-full");
    }
  });

  it("every Remove button (desktop and mobile) carries a distinct per-row accessible name", async () => {
    // Regression guard for a ux-critic finding: the mobile Remove
    // button had only visible text ("Remove"), giving every row the
    // same accessible name and breaking the document-order-based focus
    // fallback in removeRow(). Both renders must carry the same
    // "Remove criterion N" label.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 })),
    );
    renderWithProviders(<ReviewCriteriaPage />, {
      route: "/rubric/5/review",
      path: "/rubric/:rubricId/review",
    });

    await screen.findAllByDisplayValue("Has an abstract");
    const row1Removes = screen.getAllByRole("button", { name: "Remove criterion 1" });
    expect(row1Removes.length).toBe(2); // desktop + mobile
  });

  it("the weight field is not a native type=number spinner (platform-inconsistent chrome + scroll-wheel footgun)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 })),
    );
    renderWithProviders(<ReviewCriteriaPage />, {
      route: "/rubric/5/review",
      path: "/rubric/:rubricId/review",
    });

    await screen.findAllByDisplayValue("Has an abstract");
    for (const el of screen.getAllByLabelText(/Criterion 1 weight, percent/)) {
      expect((el as HTMLInputElement).type).toBe("text");
    }
  });

  it("shows the Try again button and calls refetch when the rubric fails to load", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        if (calls === 1) return new Response("Internal error", { status: 500 });
        return new Response(JSON.stringify(RUBRIC), { status: 200 });
      }),
    );
    renderWithProviders(<ReviewCriteriaPage />, {
      route: "/rubric/5/review",
      path: "/rubric/:rubricId/review",
    });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load this rubric.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findAllByDisplayValue("Has an abstract")).not.toHaveLength(0);
  });
});
