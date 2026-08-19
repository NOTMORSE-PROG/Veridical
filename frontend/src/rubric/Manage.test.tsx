import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RubricListItem } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { ManageRubricPage } from "./Manage";

// Mobile card + desktop table both render in the DOM simultaneously (CSS
// `lg:hidden`/`hidden lg:block` — jsdom applies no media queries), so every
// query here expects TWO matches, not one (same convention as
// dashboard/ManuscriptsTable.test.tsx).

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
  program: null,
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
  program: null,
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
    expect((await screen.findAllByText("V2.pdf")).length).toBe(2);
    // The summary panel (unlike the version-history table below it) has
    // one shared responsive layout, not a separate mobile/desktop split.
    expect(screen.getByText("v1 · Active")).toBeInTheDocument();
    const versionCells = screen.getAllByText(/^v[12]$/);
    expect(versionCells.map((el) => el.textContent)).toEqual(["v2", "v1", "v2", "v1"]);
    expect(screen.getAllByText("3 pinned").length).toBe(2); // v1's report count
  });

  it("shows an empty state when no format has been uploaded yet", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/rubric-families": [] }));
    renderWithProviders(<ManageRubricPage />);
    expect(await screen.findByText("No required format yet")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Upload required format" }),
    ).toBeInTheDocument();
  });

  it("honestly says a shown format is not active yet, instead of asserting a false 'Active' status (P1 found live)", async () => {
    const draftFamily: RubricListItem = { ...V1, is_active: false };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/rubric-families": [draftFamily],
        [`/rubric-families/${FAMILY_ID}/versions`]: [draftFamily],
      }),
    );
    renderWithProviders(<ManageRubricPage />);
    expect(await screen.findByText("v1 · Not confirmed")).toBeInTheDocument();
    expect(screen.queryByText(/v1 · Active/)).not.toBeInTheDocument();
    expect(screen.getByText(/No version of this format is active yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review and activate" })).toBeInTheDocument();
  });

  it("discloses when more than one required format exists (no switcher is built yet)", async () => {
    const secondFamily: RubricListItem = {
      ...V1,
      id: 99,
      rubric_family_id: "33333333-3333-3333-3333-333333333333",
      is_active: false,
    };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/rubric-families": [V1, secondFamily],
        [`/rubric-families/${FAMILY_ID}/versions`]: [V1],
      }),
    );
    renderWithProviders(<ManageRubricPage />);
    expect(
      await screen.findByText(/VERIDICAL found 2 required formats on your account/),
    ).toBeInTheDocument();
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

    await screen.findAllByText("V2.pdf");
    fireEvent.click(screen.getAllByRole("button", { name: "Activate" })[0]);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining(`/rubrics/${V2.id}/activate`),
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(await screen.findByText("Version 2 is now active.")).toBeInTheDocument();
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
    await screen.findAllByText("V2.pdf");
    // Only v2 (not active) should offer Delete — doubled for the dual layout.
    expect(screen.getAllByRole("button", { name: "Delete" })).toHaveLength(2);
  });

  it("Delete requires confirmation before the endpoint is called (a misclick no longer destroys a version)", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === `/rubrics/${V2.id}` && init?.method === "DELETE") {
        return new Response(null, { status: 204 });
      }
      if (path === "/rubric-families") return new Response(JSON.stringify([V1]), { status: 200 });
      if (path === `/rubric-families/${FAMILY_ID}/versions`) {
        return new Response(JSON.stringify([V2, V1]), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ManageRubricPage />);

    await screen.findAllByText("V2.pdf");
    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]);

    expect(await screen.findByText("Delete version 2?")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining(`/rubrics/${V2.id}`),
      expect.objectContaining({ method: "DELETE" }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Delete version" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining(`/rubrics/${V2.id}`),
        expect.objectContaining({ method: "DELETE" }),
      ),
    );
    expect(screen.queryByText("Delete version 2?")).not.toBeInTheDocument();
  });

  it("announces the in-flight delete, not just the visual button label (ux-critic finding)", async () => {
    let resolveDelete: (() => void) | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === `/rubrics/${V2.id}` && init?.method === "DELETE") {
        await new Promise<void>((resolve) => {
          resolveDelete = resolve;
        });
        return new Response(null, { status: 204 });
      }
      if (path === "/rubric-families") return new Response(JSON.stringify([V1]), { status: 200 });
      if (path === `/rubric-families/${FAMILY_ID}/versions`) {
        return new Response(JSON.stringify([V2, V1]), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ManageRubricPage />);

    await screen.findAllByText("V2.pdf");
    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]);
    await screen.findByText("Delete version 2?");
    fireEvent.click(screen.getByRole("button", { name: "Delete version" }));

    const pending = await screen.findByText("Deleting version 2.");
    expect(pending).toHaveAttribute("role", "status");

    resolveDelete?.();
    await waitFor(() => expect(screen.queryByText("Delete version 2?")).not.toBeInTheDocument());
  });

  it("Cancel on the delete confirmation closes it without calling the endpoint", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/rubric-families") return new Response(JSON.stringify([V1]), { status: 200 });
      if (path === `/rubric-families/${FAMILY_ID}/versions`) {
        return new Response(JSON.stringify([V2, V1]), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ManageRubricPage />);

    await screen.findAllByText("V2.pdf");
    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]);
    expect(await screen.findByText("Delete version 2?")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("Delete version 2?")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining(`/rubrics/${V2.id}`),
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("shows an always-visible reason (not a hover-only tooltip) when Delete is blocked by pinned reports", async () => {
    const pinnedDraft: RubricListItem = { ...V2, id: 21, version: 3, report_count: 2, title: "V3.pdf" };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/rubric-families": [V1],
        [`/rubric-families/${FAMILY_ID}/versions`]: [pinnedDraft, V1],
      }),
    );
    renderWithProviders(<ManageRubricPage />);
    await screen.findAllByText("V3.pdf");
    expect(screen.getAllByText("Reports pinned").length).toBe(2);
  });

  it("V-064 AC1: shows 'Not set' when the family has no program, and offers real seeded programs to pick from", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/rubric-families": [V1],
        [`/rubric-families/${FAMILY_ID}/versions`]: [V1],
        "/programs": [
          { id: 1, name: "CS" },
          { id: 2, name: "IT" },
        ],
      }),
    );
    renderWithProviders(<ManageRubricPage />);
    const select = await screen.findByLabelText("Program");
    expect(select).toHaveValue("");
    expect(screen.getByRole("option", { name: "CS" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "IT" })).toBeInTheDocument();
  });

  it("V-064 AC1: shows the family's already-set program as actually selected, not blank", async () => {
    const csFamily: RubricListItem = { ...V1, program: "CS" };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/rubric-families": [csFamily],
        [`/rubric-families/${FAMILY_ID}/versions`]: [csFamily],
        "/programs": [
          { id: 1, name: "CS" },
          { id: 2, name: "IT" },
        ],
      }),
    );
    renderWithProviders(<ManageRubricPage />);
    const select = await screen.findByLabelText("Program");
    await waitFor(() => expect(select).toHaveValue("1"));
  });

  it("V-064 AC1: changing the program PUTs the family's new program_id", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(typeof input === "string" ? input : input.toString(), "http://localhost").pathname;
      if (path === "/rubric-families") return new Response(JSON.stringify([V1]), { status: 200 });
      if (path === `/rubric-families/${FAMILY_ID}/versions`) {
        return new Response(JSON.stringify([V1]), { status: 200 });
      }
      if (path === "/programs") {
        return new Response(JSON.stringify([{ id: 1, name: "CS" }, { id: 2, name: "IT" }]), { status: 200 });
      }
      if (path === `/rubric-families/${FAMILY_ID}/program` && init?.method === "PUT") {
        return new Response(JSON.stringify([{ ...V1, program: "IT" }]), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ManageRubricPage />);
    const select = await screen.findByLabelText("Program");
    fireEvent.change(select, { target: { value: "2" } });

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining(`/rubric-families/${FAMILY_ID}/program`),
        expect.objectContaining({ method: "PUT" }),
      ),
    );
    const call = fetchMock.mock.calls.find(
      ([, init]) => init?.method === "PUT",
    ) as [string, RequestInit] | undefined;
    expect(JSON.parse((call?.[1]?.body as string) ?? "{}")).toEqual({ program_id: 2 });
  });

  it("V-064: renders no program control when no programs are configured on the account", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/rubric-families": [V1],
        [`/rubric-families/${FAMILY_ID}/versions`]: [V1],
        "/programs": [],
      }),
    );
    renderWithProviders(<ManageRubricPage />);
    await screen.findByText("v1 · Active");
    expect(screen.queryByLabelText("Program")).not.toBeInTheDocument();
  });
});
