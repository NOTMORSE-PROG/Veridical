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

    expect(await screen.findByDisplayValue("Has an abstract")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Argument is well developed")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Abstract present")).toBeInTheDocument();
    expect(screen.getByText("2 criteria")).toBeInTheDocument();
    expect(screen.getByText("Weights total 100%")).toBeInTheDocument();
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

    const textInput = await screen.findByDisplayValue("Has an abstract");
    fireEvent.change(textInput, { target: { value: "Has an abstract of at most 250 words" } });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));

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

  it("deleting every row disables Save and Confirm, and shows an explanation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(RUBRIC), { status: 200 })),
    );
    renderWithProviders(<ReviewCriteriaPage />, {
      route: "/rubric/5/review",
      path: "/rubric/:rubricId/review",
    });

    await screen.findByDisplayValue("Has an abstract");
    fireEvent.click(screen.getByRole("button", { name: "Delete criterion 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete criterion 1" })); // list shifted up

    expect(await screen.findByText(/Add at least one criterion/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Confirm & activate rubric" })).toBeDisabled();
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

    await screen.findByDisplayValue("Has an abstract");
    expect(screen.getByText("Weights total 20%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Normalize to 100%" }));
    expect(await screen.findByText("Weights total 100%")).toBeInTheDocument();
  });
});
