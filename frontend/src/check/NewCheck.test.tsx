import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { NewCheckModal } from "./NewCheck";

const MANUSCRIPTS = {
  items: [
    {
      id: 1,
      group_label: "G-11",
      ingest_status: "done",
      created_at: "2026-01-01T00:00:00Z",
      latest_check_run_id: null,
      latest_check_run_status: null,
    },
    {
      id: 2,
      group_label: "G-12 (still processing)",
      ingest_status: "processing",
      created_at: "2026-01-02T00:00:00Z",
      latest_check_run_id: null,
      latest_check_run_status: null,
    },
  ],
  total: 2,
  page: 1,
  page_size: 200,
};

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
    await screen.findByRole("option", { name: /^G-11,/ });
    expect(screen.queryByRole("option", { name: /G-12/ })).not.toBeInTheDocument();
  });

  it("shows a focused, accessible error summary when Start is clicked with nothing selected", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/manuscripts": MANUSCRIPTS, "/rubric-families": ONE_ACTIVE_FAMILY }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} />);
    await screen.findByRole("option", { name: /^G-11,/ });
    // Start check is never disabled for validation reasons (GOV.UK
    // pattern, matching 4d) — clicking it is what surfaces the problem.
    expect(screen.getByRole("button", { name: "Start check" })).not.toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Start check" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Choose a manuscript.");
    expect(alert).toHaveFocus();
  });

  it("auto-selects the single active rubric and shows its criteria count", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/manuscripts": MANUSCRIPTS, "/rubric-families": ONE_ACTIVE_FAMILY }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} />);
    expect(await screen.findByText(/24 criteria/)).toBeInTheDocument();
  });

  it("explains, via the error summary, when no active rubric exists — Start check stays enabled", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/manuscripts": MANUSCRIPTS, "/rubric-families": [] }));
    renderWithProviders(<NewCheckModal onClose={() => {}} />);
    await screen.findByText("No active rubric yet. Confirm one on the rubric review screen first.");
    expect(screen.getByRole("button", { name: "Start check" })).not.toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Start check" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No active rubric is available yet.");
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

  it("shows a real loading state for manuscripts, not a false empty state (Nielsen visibility of system status)", async () => {
    let resolveManuscripts: (value: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = new URL(typeof input === "string" ? input : input.toString(), "http://localhost").pathname;
        if (path === "/manuscripts") {
          return new Promise<Response>((resolve) => {
            resolveManuscripts = resolve;
          });
        }
        return new Response(JSON.stringify(ONE_ACTIVE_FAMILY), { status: 200 });
      }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} />);

    expect(await screen.findByText("Loading manuscripts.")).toBeInTheDocument();
    expect(screen.queryByText(/No manuscripts have been ingested/)).not.toBeInTheDocument();

    resolveManuscripts(new Response(JSON.stringify(MANUSCRIPTS), { status: 200 }));
    await screen.findByRole("option", { name: /^G-11,/ });
  });

  it("shows a real fetch-error state with a working retry, for manuscripts", async () => {
    let calls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(typeof input === "string" ? input : input.toString(), "http://localhost").pathname;
      if (path === "/manuscripts") {
        calls += 1;
        if (calls === 1) return new Response("Internal error", { status: 500 });
        return new Response(JSON.stringify(MANUSCRIPTS), { status: 200 });
      }
      return new Response(JSON.stringify(ONE_ACTIVE_FAMILY), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<NewCheckModal onClose={() => {}} />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load your manuscripts.");
    expect(calls).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("option", { name: /^G-11,/ });
  });

  it("distinguishes still-processing manuscripts from a genuinely empty list, honestly", async () => {
    const allPending = {
      ...MANUSCRIPTS,
      items: [{ ...MANUSCRIPTS.items[1], id: 3 }],
    };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/manuscripts": allPending, "/rubric-families": ONE_ACTIVE_FAMILY }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} />);

    expect(
      await screen.findByText("Your manuscripts are still being processed. This can take a few minutes. Check back shortly."),
    ).toBeInTheDocument();
  });
});
