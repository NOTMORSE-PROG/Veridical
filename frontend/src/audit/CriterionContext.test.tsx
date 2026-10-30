import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuditLogDetail } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { AuditLogPage } from "./AuditLog";

const entry: AuditLogDetail = {
  id: 41, event_type: "escalation_resolved", check_run_id: 7,
  manuscript_group_label: "Example group", manuscript_original_filename: "example.docx",
  prompt_type: null, prompt_version: null, agreement_score: null,
  llm_execution_mode: null, created_at: "2026-10-20T08:00:00Z", input_hash: null,
  payload: { criterion_id: 11, check_result_id: 29, resolution: "mark_fail", reason: "The design explanation was missing." },
  criterion_context: { check_run_id: 7, criterion_id: 11, check_result_id: 29,
    criterion_text: "Explain the design.\nAddress the recorded constraints.", criterion_position: 2,
    rubric_id: 3, rubric_version: 1, rubric_title: "Producing format" },
};

function openDetail(detail: AuditLogDetail = entry) {
  vi.stubGlobal("fetch", stubFetchByPath({
    "/audit": { items: [detail], page: 2, page_size: 25, total: 26 },
    "/audit/41": detail,
  }));
  return renderWithProviders(<AuditLogPage />, { authInstructorId: 1,
    route: "/audit?filter=Escalations&page=2&detail=41" });
}

describe("Audit criterion context", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("identifies the producing requirement separately from the saved judgment", async () => {
    openDetail();
    const context = await screen.findByRole("region", { name: "Criterion context" });
    expect(within(context).getByText("From the required-format version used for this review.")).toBeVisible();
    expect(within(context).getByText(/Explain the design/).textContent).toBe(entry.criterion_context!.criterion_text);
    expect(within(context).getByText("Producing format")).toBeVisible();
    const link = within(context).getByRole("link", { name: "View this criterion in current report" });
    expect(link).toHaveAttribute("href", "/report/7?criterion_record=11#criterion-record-7-11");
    const judgment = screen.getByRole("region", { name: "Criterion judgment recorded" });
    expect(within(judgment).getByText("Does not meet criterion")).toBeVisible();
    expect(within(judgment).getByText("The design explanation was missing.")).toBeVisible();
    expect(within(judgment).getByText("Recorded criterion identifier").parentElement)
      .toHaveClass("signal-audit-entry-fact--secondary");
    expect(within(context).queryByText("The design explanation was missing.")).not.toBeInTheDocument();
  });

  it.each([undefined, null])("keeps unavailable context neutral for an older API or an unbound entry", async criterion_context => {
    openDetail({ ...entry, criterion_context });
    const context = await screen.findByRole("region", { name: "Criterion context" });
    expect(within(context).getByText("Requirement text is unavailable.")).toBeVisible();
    expect(within(context).getByText("The stored judgment below is unchanged.")).toBeVisible();
    expect(within(context).queryByRole("link")).not.toBeInTheDocument();
    expect(within(context).queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("The design explanation was missing.")).toBeVisible();
  });

  it.each(["check_run_id", "criterion_id", "check_result_id"] as const)("withholds a context carrying a different %s", async field => {
    openDetail({ ...entry, criterion_context: { ...entry.criterion_context!, [field]: 99 } });
    const context = await screen.findByRole("region", { name: "Criterion context" });
    expect(within(context).getByText("Requirement text is unavailable.")).toBeVisible();
    expect(within(context).queryByText(/Explain the design/)).not.toBeInTheDocument();
    expect(within(context).queryByRole("link")).not.toBeInTheDocument();
  });

  it("does not reinterpret an automated event as a criterion judgment", async () => {
    openDetail({ ...entry, event_type: "llm_call" });
    await screen.findByText("What this records");
    expect(screen.queryByRole("region", { name: "Criterion context" })).not.toBeInTheDocument();
  });
});
