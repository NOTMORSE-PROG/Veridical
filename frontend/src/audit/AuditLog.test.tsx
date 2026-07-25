import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { AuditLogPage } from "./AuditLog";

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
    expect(await screen.findByText("AI call")).toBeInTheDocument();
    expect(screen.getByText("Escalation resolved")).toBeInTheDocument();
  });

  it("shows the agreement score in the event summary", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    expect((await screen.findAllByText(/agreement 0\.67/)).length).toBeGreaterThan(0);
  });

  it("shows an empty state honestly, not a blank table", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/audit": { items: [], total: 0, page: 1, page_size: 25 } }),
    );
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    expect(await screen.findByText("No audit events match this filter.")).toBeInTheDocument();
  });

  it("opens the detail drawer with the full stored payload", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/audit": PAGE, "/audit/42": DETAIL }),
    );
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findByText("AI call");
    fireEvent.click(screen.getAllByRole("button", { name: "Detail" })[0]);
    expect(await screen.findByText(`Audit entry #42`)).toBeInTheDocument();
    expect(await screen.findByText(/input_hash:\s*abc123/)).toBeInTheDocument();
  });

  it("pre-filters by check_run_id when arriving from the report's audit-trail link", async () => {
    const fetchMock = stubFetchByPath({ "/audit": PAGE });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<AuditLogPage />, { route: "/audit?check_run_id=7", path: "/audit" });
    await screen.findByText("AI call");
    expect(screen.getByText("check run 7")).toBeInTheDocument();
    const calledUrl = fetchMock.mock.calls[0][0] as string;
    expect(calledUrl).toContain("check_run_id=7");
  });
});
