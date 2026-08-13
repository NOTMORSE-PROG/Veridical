import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { AuditLogPage } from "./AuditLog";

// Mobile card + desktop table both render in the DOM simultaneously (CSS
// `sm:hidden`/`hidden sm:block` — jsdom applies no media queries), so every
// query here expects TWO matches, not one (same convention as
// dashboard/ManuscriptsTable.test.tsx and rubric/Manage.test.tsx).

const PAGE = {
  items: [
    {
      id: 42,
      event_type: "llm_call",
      check_run_id: 7,
      manuscript_group_label: "G-11",
      prompt_type: "semantic_grading",
      prompt_version: "v1",
      agreement_score: 0.667,
      created_at: "2026-07-25T14:05:00Z",
    },
    {
      id: 41,
      event_type: "escalation_resolved",
      check_run_id: 7,
      manuscript_group_label: "G-11",
      prompt_type: null,
      prompt_version: null,
      agreement_score: 0.667,
      created_at: "2026-07-25T14:18:00Z",
    },
  ],
  total: 2,
  page: 1,
  page_size: 25,
};

const DETAIL = {
  ...PAGE.items[0],
  input_hash: "abc123",
  payload: { prompt_type: "semantic_grading", prompt: "Grade this...", response: { verdicts: [] } },
};

describe("AuditLogPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists audit events with their labels", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    expect((await screen.findAllByText("AI call")).length).toBe(2);
    expect(screen.getAllByText("Escalation resolved").length).toBe(2);
  });

  it("BUG (found live): the event-type key for an overridden flag never matched the real backend value, so it always showed a raw string instead of a label", async () => {
    const overridden = {
      items: [{ ...PAGE.items[0], id: 43, event_type: "flag_overridden" }],
      total: 1,
      page: 1,
      page_size: 25,
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": overridden }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    expect((await screen.findAllByText("Flag overridden")).length).toBe(2);
    expect(screen.queryAllByText("flag_overridden")).toHaveLength(0);
  });

  it("marks an instructor-initiated event distinctly from an automated one (icon meaning carried in text too, never color/shape alone)", async () => {
    const mixed = {
      items: [
        { ...PAGE.items[0], id: 44, event_type: "llm_call" },
        { ...PAGE.items[0], id: 45, event_type: "escalation_resolved" },
      ],
      total: 2,
      page: 1,
      page_size: 25,
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": mixed }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    expect(screen.getAllByText("Automated:", { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Instructor action:", { exact: false }).length).toBeGreaterThan(0);
  });

  it("shows the agreement score in the event summary", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    expect((await screen.findAllByText(/agreement 0\.67/)).length).toBeGreaterThan(0);
  });

  it("shows an empty state honestly, not a blank table, and announces it", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/audit": { items: [], total: 0, page: 1, page_size: 25 } }),
    );
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    expect((await screen.findAllByText("No audit events match this filter.")).length).toBe(2);
    expect(await screen.findByText("No audit events match this filter.", { selector: '[role="status"]' })).toBeInTheDocument();
  });

  it("opens the detail drawer with the full stored payload", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/audit": PAGE, "/audit/42": DETAIL }),
    );
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    fireEvent.click(screen.getAllByRole("button", { name: "Detail" })[0]);
    expect(await screen.findByText(`Audit entry #42`)).toBeInTheDocument();
    expect(await screen.findByText(/input_hash:\s*abc123/)).toBeInTheDocument();
    expect(screen.getByText(/uv run python -m scripts.replay_call 42/)).toBeInTheDocument();
  });

  it("BUG-022: shows the manuscript's filename in the detail drawer, but not in the list rows (which stay light at volume)", async () => {
    const detailWithFilename = { ...DETAIL, manuscript_original_filename: "Chapter1-3_FinalDefense.pdf" };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/audit": PAGE, "/audit/42": detailWithFilename }),
    );
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    expect(screen.queryByText("Chapter1-3_FinalDefense.pdf")).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Detail" })[0]);
    expect(await screen.findByText("Chapter1-3_FinalDefense.pdf")).toBeInTheDocument();
  });

  it("pre-filters by check_run_id when arriving from the report's audit-trail link", async () => {
    const fetchMock = stubFetchByPath({ "/audit": PAGE });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<AuditLogPage />, { route: "/audit?check_run_id=7", path: "/audit" });
    await screen.findAllByText("AI call");
    expect(screen.getByText("check run 7")).toBeInTheDocument();
    const calledUrl = fetchMock.mock.calls[0][0] as string;
    expect(calledUrl).toContain("check_run_id=7");
  });

  it("marks the active filter with aria-pressed, not color alone (WCAG 4.1.2)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "AI calls" })).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(screen.getByRole("button", { name: "AI calls" }));
    expect(screen.getByRole("button", { name: "AI calls" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "false");
  });

  it("focuses and titles the page on arrival (route focus management, previously missing entirely)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    expect(document.title).toBe("Audit log - VERIDICAL");
  });

  it("the mobile and desktop list regions resolve a real aria-labelledby target (ux-critic finding: the id didn't exist)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    const heading = document.getElementById("audit-log-heading");
    expect(heading).not.toBeNull();
    expect(heading?.textContent).toBe("Audit log");
  });

  it("BUG (ux-critic finding): the Detail modal shows a real error and retry, never stays stuck on 'Loading' forever", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/audit": PAGE,
        "/audit/42": new Response(
          JSON.stringify({ error: { code: "not_found", message: "No audit entry 42." } }),
          { status: 404 },
        ),
      }),
    );
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    fireEvent.click(screen.getAllByRole("button", { name: "Detail" })[0]);
    expect(await screen.findByRole("alert")).toHaveTextContent("No audit entry 42.");
    expect(screen.queryByText("Loading audit entry.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("BUG (ux-critic finding): Previous/Next disabling itself no longer strands focus on <body> — it lands on the page indicator", async () => {
    const PAGE_MULTI = { ...PAGE, page_size: 1, total: 2 };
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE_MULTI }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Page 2 of 2");
    expect(document.activeElement?.textContent).toBe("Page 2 of 2");
  });

  it("labels rubric-parse and model-exhausted events instead of showing the raw event_type string (same bug class as the flag_overridden fix)", async () => {
    const rare = {
      items: [
        { ...PAGE.items[0], id: 50, event_type: "rubric_parse_attempt" },
        { ...PAGE.items[0], id: 51, event_type: "llm_model_exhausted" },
      ],
      total: 2,
      page: 1,
      page_size: 25,
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": rare }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    expect((await screen.findAllByText("Rubric parse attempt")).length).toBe(2);
    expect(screen.getAllByText("AI model exhausted, failed over").length).toBe(2);
    expect(screen.queryAllByText("rubric_parse_attempt")).toHaveLength(0);
    expect(screen.queryAllByText("llm_model_exhausted")).toHaveLength(0);
  });
});
