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

  it("BUG-188: does not invent a vote count from an agreement field with unknown provenance", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    expect(screen.queryByText(/grading passes agreed/)).not.toBeInTheDocument();
    expect(screen.queryByText(/0\.67/)).not.toBeInTheDocument();
    expect(screen.queryByText("semantic_grading")).not.toBeInTheDocument();
    expect(screen.getAllByText(/Semantic criterion review/).length).toBeGreaterThan(0);
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

  it("BUG-188: opens with an instructor explanation and keeps exact data in a closed technical disclosure", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/audit": PAGE, "/audit/42": DETAIL }),
    );
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    fireEvent.click(screen.getAllByRole("button", { name: "Detail" })[0]);
    expect(await screen.findByText(`Audit entry #42`)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "What this records" })).toBeInTheDocument();
    expect(screen.getByText(/completed an AI-assisted review step/)).toBeInTheDocument();
    const technical = screen.getByText("Technical record for reproducibility").closest("details");
    expect(technical).not.toHaveAttribute("open");
    expect(await screen.findByText(/agreement_score:\s*0\.667/)).toBeInTheDocument();
    expect(await screen.findByText(/input_hash:\s*abc123/)).toBeInTheDocument();
    expect(screen.getByText(/"prompt_type": "semantic_grading"/)).toBeInTheDocument();
    expect(screen.queryByText(/uv run python -m scripts\.replay_call/)).not.toBeInTheDocument();
    expect(screen.queryByText(/can reproduce the record/i)).not.toBeInTheDocument();
    expect(screen.getByText(/use it in a reproduction workflow/i)).toBeInTheDocument();
  });

  it("BUG-188: keeps override confidence exact in technical data without calling it grader agreement", async () => {
    const override = {
      ...PAGE.items[0],
      id: 43,
      event_type: "flag_overridden",
      prompt_type: null,
      prompt_version: null,
      agreement_score: 0.91,
    };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/audit": { items: [override], total: 1, page: 1, page_size: 25 },
        "/audit/43": {
          ...override,
          input_hash: null,
          payload: { flag_id: 19, confidence: 0.91, instructor_id: 1 },
        },
      }),
    );
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("Flag overridden");
    expect(screen.queryByText(/grading passes|0\.91/)).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "Detail" })[0]);
    expect(await screen.findByText(/agreement_score:\s*0\.91/)).toBeInTheDocument();
    expect(screen.queryByText(/grading passes/)).not.toBeInTheDocument();
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
    expect(heading?.textContent).toBe("Audit");
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

  it("BUG-188: labels current workflow events and humanizes future identifiers instead of exposing raw enums", async () => {
    const workflowEvents = {
      items: [
        { ...PAGE.items[0], id: 60, event_type: "citation_source_confirmed" },
        { ...PAGE.items[0], id: 61, event_type: "check_run_cancel_requested" },
        { ...PAGE.items[0], id: 62, event_type: "check_run_cancelled" },
        { ...PAGE.items[0], id: 63, event_type: "manuscript_ingestion_failure_dismissed" },
        { ...PAGE.items[0], id: 64, event_type: "future_review_event" },
      ],
      total: 5,
      page: 1,
      page_size: 25,
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": workflowEvents }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });

    expect((await screen.findAllByText("Citation source confirmed")).length).toBe(2);
    expect(screen.getAllByText("Check cancellation requested").length).toBe(2);
    expect(screen.getAllByText("Check cancelled").length).toBe(2);
    expect(screen.getAllByText("Failed upload moved to Archive").length).toBe(2);
    expect(screen.getAllByText("Future review event").length).toBe(2);
    expect(screen.queryByText(/citation_source_confirmed|future_review_event/)).not.toBeInTheDocument();
  });
});
