import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { NewCheckModal } from "./NewCheck";

const MANUSCRIPTS = [
  { id: 1, group_label: "G-11", ingest_status: "done", created_at: "2026-01-01T00:00:00Z" },
  { id: 2, group_label: "G-12 (still processing)", ingest_status: "processing", created_at: "2026-01-02T00:00:00Z" },
];

const ONE_ACTIVE_FAMILY = [
  {
    id: 9,
    rubric_family_id: "fam-1",
    version: 2,
    title: "TIP Format",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    criteria_count: 24,
    report_count: 0,
  },
];

describe("NewCheckModal", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("only lists ingested manuscripts", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/manuscripts": MANUSCRIPTS, "/rubric-families": ONE_ACTIVE_FAMILY }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} />);
    await screen.findByRole("option", { name: "G-11" });
    expect(screen.queryByRole("option", { name: "G-12 (still processing)" })).not.toBeInTheDocument();
  });

  it("shows a validation message when Start is clicked with nothing selected", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/manuscripts": MANUSCRIPTS, "/rubric-families": ONE_ACTIVE_FAMILY }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} />);
    await screen.findByRole("option", { name: "G-11" });
    fireEvent.click(screen.getByRole("button", { name: "Start check" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a manuscript first.");
  });

  it("auto-selects the single active rubric and shows its criteria count", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/manuscripts": MANUSCRIPTS, "/rubric-families": ONE_ACTIVE_FAMILY }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} />);
    expect(await screen.findByText(/24 criteria/)).toBeInTheDocument();
  });

  it("disables Start check when no active rubric exists", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/manuscripts": MANUSCRIPTS, "/rubric-families": [] }));
    renderWithProviders(<NewCheckModal onClose={() => {}} />);
    await screen.findByText("No active rubric yet — confirm one on the rubric review screen first.");
    expect(screen.getByRole("button", { name: "Start check" })).toBeDisabled();
  });

  it("submits the selected manuscript and rubric on Start check", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(typeof input === "string" ? input : input.toString(), "http://localhost")
        .pathname;
      if (path === "/manuscripts") return new Response(JSON.stringify(MANUSCRIPTS), { status: 200 });
      if (path === "/rubric-families") {
        return new Response(JSON.stringify(ONE_ACTIVE_FAMILY), { status: 200 });
      }
      if (path === "/check-runs" && init?.method === "POST") {
        return new Response(JSON.stringify({ id: 42, status: "queued" }), { status: 201 });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<NewCheckModal onClose={() => {}} />);

    await screen.findByText(/24 criteria/);
    fireEvent.change(screen.getByRole("combobox", { name: /manuscript/i }), {
      target: { value: "1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start check" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/check-runs"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse((call?.[1]?.body as string) ?? "{}")).toEqual({
      manuscript_id: 1,
      rubric_id: 9,
    });
  });
});
