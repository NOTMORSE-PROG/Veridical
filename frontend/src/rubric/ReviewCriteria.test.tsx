import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMemoryRouter, RouterProvider } from "react-router";
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
    // D-023: weight is relative, no required total -- never framed as a
    // percentage that must sum to 100.
    expect(screen.getAllByText("Weight is relative, no required total")[0]).toBeInTheDocument();
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

  it("BUG-052 (backend-critic finding): a confirmed-but-flagged rubric gets past-tense wording, never 'before confirming'", async () => {
    const confirmedButFlagged: Rubric = {
      ...RUBRIC,
      is_active: true,
      parse_status: "needs_review",
      parse_issues: ["only 40% of the source text is reflected in the parsed criteria"],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(confirmedButFlagged), { status: 200 })),
    );
    renderWithProviders(<ReviewCriteriaPage />, {
      route: "/rubric/5/review",
      path: "/rubric/:rubricId/review",
    });

    await screen.findByText("Needs manual completion");
    expect(screen.getByText(/this rubric was activated anyway/)).toBeInTheDocument();
    expect(screen.queryByText(/before confirming/)).not.toBeInTheDocument();
    expect(screen.getByText("This rubric is active. Checks use it now.")).toBeInTheDocument();
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

  it("BUG-052 (backend-critic finding): a zeroed-out weight blocks Confirm client-side, with an actionable message", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify(RUBRIC), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ReviewCriteriaPage />, {
      route: "/rubric/5/review",
      path: "/rubric/:rubricId/review",
    });

    await screen.findAllByDisplayValue("Has an abstract");
    const weightInputs = screen.getAllByLabelText("Criterion 1 weight");
    fireEvent.change(weightInputs[0], { target: { value: "" } });

    fireEvent.click(screen.getAllByRole("button", { name: "Confirm & activate rubric" })[0]);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Criterion 1's weight must be greater than zero.");
    expect(alert).toHaveFocus();
    // Blocked client-side -- never even reached the network as a PUT.
    expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "PUT")).toBe(
      false,
    );
  });

  it("D-023: shows a live Low/High importance tag next to an asymmetric weight, never a percentage", async () => {
    const unbalanced: Rubric = {
      ...RUBRIC,
      criteria: [
        { ...RUBRIC.criteria[0], weight: 90 },
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
    // average = 50: 90 is 1.8x -> High, 10 is 0.2x -> Low.
    expect(screen.getAllByText("High").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Low").length).toBeGreaterThan(0);
    expect(screen.queryByText(/90%/)).not.toBeInTheDocument();
    expect(screen.queryByText(/10%/)).not.toBeInTheDocument();
  });

  it("Distribute weights evenly sets every criterion to the same weight", async () => {
    const unbalanced: Rubric = {
      ...RUBRIC,
      criteria: [
        { ...RUBRIC.criteria[0], weight: 90 },
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
    fireEvent.click(screen.getAllByRole("button", { name: "Distribute weights evenly" })[0]);
    const weightInputs = await screen.findAllByLabelText(/Criterion \d weight$/);
    const values = weightInputs.map((el) => (el as HTMLInputElement).value);
    expect(new Set(values).size).toBe(1); // all equal now
    expect(values[0]).toBe("50");
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
    // D-023: no longer labelled "percent" -- weight is a relative value.
    for (const el of screen.getAllByLabelText(/Criterion 1 weight/)) {
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

  // BUG-037: unsaved edits were silently discarded on in-app navigation,
  // directly undermining this screen's own "Nothing runs until you
  // confirm" promise. Renders a real two-route data router (the same
  // kind App.tsx now uses) so `useBlocker` can actually intercept a
  // navigation attempt — renderWithProviders only mounts one route.
  function renderWithTwoRoutes() {
    const router = createMemoryRouter(
      [
        { path: "/rubric/:rubricId/review", element: <ReviewCriteriaPage /> },
        { path: "/dashboard", element: <div>Dashboard page</div> },
      ],
      { initialEntries: ["/rubric/5/review"] },
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    return router;
  }

  it("BUG-037: warns before discarding an unsaved edit on in-app navigation, and cancel keeps the edit intact", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 })),
    );
    const router = renderWithTwoRoutes();

    const textInputs = await screen.findAllByDisplayValue("Has an abstract");
    fireEvent.change(textInputs[0], { target: { value: "Has an abstract of at most 250 words" } });

    router.navigate("/dashboard");
    expect(await screen.findByText("Leave without saving your changes?")).toBeInTheDocument();
    // Blocked: still on the review screen, edit intact, Dashboard not rendered.
    expect(screen.queryByText("Dashboard page")).not.toBeInTheDocument();
    expect(
      screen.getAllByDisplayValue("Has an abstract of at most 250 words").length,
    ).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    await waitFor(() =>
      expect(screen.queryByText("Leave without saving your changes?")).not.toBeInTheDocument(),
    );
    expect(screen.queryByText("Dashboard page")).not.toBeInTheDocument();
    expect(
      screen.getAllByDisplayValue("Has an abstract of at most 250 words").length,
    ).toBeGreaterThan(0);
  });

  it("BUG-037: 'Leave without saving' completes the navigation and discards the edit", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 })),
    );
    const router = renderWithTwoRoutes();

    const textInputs = await screen.findAllByDisplayValue("Has an abstract");
    fireEvent.change(textInputs[0], { target: { value: "An edit that never gets saved" } });

    router.navigate("/dashboard");
    await screen.findByText("Leave without saving your changes?");
    fireEvent.click(screen.getByRole("button", { name: "Leave without saving" }));

    await waitFor(() => expect(screen.getByText("Dashboard page")).toBeInTheDocument());
  });

  it("BUG-037/ux-critic finding: a rapid double-click on 'Leave without saving' never throws (react-router rejects a second proceed() on an already-unblocked blocker)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 })),
    );
    const router = renderWithTwoRoutes();

    const textInputs = await screen.findAllByDisplayValue("Has an abstract");
    fireEvent.change(textInputs[0], { target: { value: "An edit that never gets saved" } });

    router.navigate("/dashboard");
    await screen.findByText("Leave without saving your changes?");
    const leaveButton = screen.getByRole("button", { name: "Leave without saving" });
    expect(() => {
      fireEvent.click(leaveButton);
      fireEvent.click(leaveButton);
    }).not.toThrow();

    await waitFor(() => expect(screen.getByText("Dashboard page")).toBeInTheDocument());
  });

  it("BUG-037: does not warn when navigating away with no unsaved edits", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 })),
    );
    const router = renderWithTwoRoutes();

    await screen.findAllByDisplayValue("Has an abstract");
    router.navigate("/dashboard");

    await waitFor(() => expect(screen.getByText("Dashboard page")).toBeInTheDocument());
    expect(screen.queryByText("Leave without saving your changes?")).not.toBeInTheDocument();
  });

  it("BUG-037: a saved edit no longer counts as unsaved (no false-positive warning)", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const router = renderWithTwoRoutes();

    const textInputs = await screen.findAllByDisplayValue("Has an abstract");
    fireEvent.change(textInputs[0], { target: { value: "Has an abstract, revised" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save draft" })[0]);
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/rubrics/5/criteria"),
        expect.objectContaining({ method: "PUT" }),
      ),
    );

    router.navigate("/dashboard");
    await waitFor(() => expect(screen.getByText("Dashboard page")).toBeInTheDocument());
    expect(screen.queryByText("Leave without saving your changes?")).not.toBeInTheDocument();
  });
});
