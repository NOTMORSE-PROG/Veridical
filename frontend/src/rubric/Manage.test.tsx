import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RubricListItem } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { ManageRubricPage } from "./Manage";

const FAMILY_ID = "22222222-2222-2222-2222-222222222222";

const V2: RubricListItem = {
  id: 20,
  rubric_family_id: FAMILY_ID,
  version: 2,
  title: "V2.pdf",
  is_active: false,
  created_at: "2026-07-25T00:00:00Z",
  criteria_count: 5,
  report_count: 0,
};

const V1: RubricListItem = {
  id: 10,
  rubric_family_id: FAMILY_ID,
  version: 1,
  title: "V1.pdf",
  is_active: true,
  created_at: "2026-06-01T00:00:00Z",
  criteria_count: 4,
  report_count: 3,
};

describe("ManageRubricPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the active format and every version, newest first", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/rubric-families": [V1],
        [`/rubric-families/${FAMILY_ID}/versions`]: [V2, V1],
      }),
    );
    renderWithProviders(<ManageRubricPage />);

    // V2 only appears in the version list (the header shows the ACTIVE
    // one, V1) — waiting on it proves the versions query actually
    // resolved, not just the family-list fallback used for the header.
    expect(await screen.findByText("V2.pdf")).toBeInTheDocument();
    expect(screen.getByText("v1 · Active")).toBeInTheDocument();
    const versionCells = screen.getAllByText(/^v[12]$/);
    expect(versionCells.map((el) => el.textContent)).toEqual(["v2", "v1"]);
    expect(screen.getByText("3")).toBeInTheDocument(); // v1's report count
    expect(screen.getByText("pinned")).toBeInTheDocument();
  });

  it("shows an empty state when no format has been uploaded yet", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/rubric-families": [] }));
    renderWithProviders(<ManageRubricPage />);
    expect(await screen.findByText(/No required format uploaded yet/)).toBeInTheDocument();
  });

  it("Activate calls the activate endpoint for that version", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === `/rubrics/${V2.id}/activate`) {
        expect(init?.method).toBe("POST");
        return new Response(JSON.stringify({ ...V2, is_active: true }), { status: 200 });
      }
      if (path === "/rubric-families") return new Response(JSON.stringify([V1]), { status: 200 });
      if (path === `/rubric-families/${FAMILY_ID}/versions`) {
        return new Response(JSON.stringify([V2, V1]), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ManageRubricPage />);

    await screen.findByText("V2.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining(`/rubrics/${V2.id}/activate`),
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("does not offer Delete for the active version", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/rubric-families": [V1],
        [`/rubric-families/${FAMILY_ID}/versions`]: [V2, V1],
      }),
    );
    renderWithProviders(<ManageRubricPage />);
    await screen.findByText("V2.pdf");
    // Only v2 (not active) should offer Delete.
    expect(screen.getAllByRole("button", { name: "Delete" })).toHaveLength(1);
  });
});
