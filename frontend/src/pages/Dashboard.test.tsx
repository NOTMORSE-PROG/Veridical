import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { DashboardPage } from "./Dashboard";

const ACTIVE_FAMILY = [
  {
    id: 9,
    rubric_family_id: "fam-1",
    version: 2,
    title: "TIP Format",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    criteria_count: 24,
    report_count: 1,
  },
];

const STATS = {
  manuscripts_checked: 3,
  ready_count: 1,
  conditionally_ready_count: 1,
  not_ready_count: 1,
  needs_review_count: 0,
  escalations_awaiting_review: 2,
  escalation_rate: 0.5,
  escalation_budget: 0.2,
  system_underperforming: true,
  decided_count: 1,
};

const MANUSCRIPTS_PAGE = {
  items: [
    {
      id: 1,
      group_label: "G-11",
      ingest_status: "done",
      ingest_failure_reason: null,
      created_at: "2026-01-01T00:00:00Z",
      latest_check_run_id: 7,
      latest_check_run_status: "done",
      latest_done_check_run_id: 7,
    },
  ],
  total: 1,
  page: 1,
  page_size: 20,
};

describe("DashboardPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the first-run empty state (screen 4b) with the 3-step guide", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": {
          id: 1,
          email: "a@b.com",
          display_name: "Demo Instructor",
          onboarding_dismissed_at: "2026-01-01T00:00:00Z",
        },
        "/rubric-families": [],
      }),
    );
    renderWithProviders(<DashboardPage />);
    // Staged reveal: nothing paints until `families` resolves (avoids a
    // flash of the wrong screen), so this is asynchronous now.
    expect(await screen.findByText("No required format yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload required format" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New check" })).toBeEnabled();
    await waitFor(() => expect(screen.getByText("Demo Instructor")).toBeInTheDocument());
  });

  it("BUG-023: 'New check' with no active rubric explains what's missing instead of dead-clicking silently", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": {
          id: 1,
          email: "a@b.com",
          display_name: "Demo Instructor",
          onboarding_dismissed_at: "2026-01-01T00:00:00Z",
        },
        "/rubric-families": [],
        "/manuscripts": { items: [], total: 0, page: 1, page_size: 200 },
      }),
    );
    renderWithProviders(<DashboardPage />);
    const newCheck = await screen.findByRole("button", { name: "New check" });
    expect(newCheck).toBeEnabled();
    fireEvent.click(newCheck);
    expect(
      await screen.findByText("No active rubric is available yet."),
    ).toBeInTheDocument();
  });

  it("renders the populated dashboard (screen 4e) once a rubric is active", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/rubric-families": ACTIVE_FAMILY,
        "/stats": STATS,
        "/manuscripts": MANUSCRIPTS_PAGE,
      }),
    );
    renderWithProviders(<DashboardPage />);
    expect(await screen.findByText("3 manuscripts checked, by readiness status")).toBeInTheDocument();
    expect(screen.queryByText("No required format yet")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New check" })).toBeEnabled();
    expect((await screen.findAllByText("G-11")).length).toBeGreaterThan(0);
    expect(screen.getByText("System underperforming.")).toBeInTheDocument();
  });

  it("V-059: Upload manuscript is available even with no active rubric yet (BUG-023 precedent: destination explains, never hidden)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": {
          id: 1,
          email: "a@b.com",
          display_name: "Demo Instructor",
          onboarding_dismissed_at: "2026-01-01T00:00:00Z",
        },
        "/rubric-families": [],
      }),
    );
    renderWithProviders(<DashboardPage />);
    expect(await screen.findByRole("button", { name: "Upload manuscript" })).toBeEnabled();
  });

  it("V-059: end-to-end handoff — upload a manuscript, then it's preselected in New check", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(typeof input === "string" ? input : input.toString(), "http://localhost").pathname;
      if (path === "/auth/me") {
        return new Response(
          JSON.stringify({ id: 1, email: "a@b.com", display_name: "Demo Instructor" }),
          { status: 200 },
        );
      }
      if (path === "/rubric-families") return new Response(JSON.stringify(ACTIVE_FAMILY), { status: 200 });
      if (path === "/stats") return new Response(JSON.stringify(STATS), { status: 200 });
      if (path === "/manuscripts/ingest" && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            manuscript_id: 99,
            group_label: "Ungrouped",
            ingest_status: "done",
            page_count: 8,
            anchor_kind: "page",
            image_only: false,
            text_chars: 100,
            images: 0,
            tables: 0,
            equations: 0,
            citations: 3,
            vision_status: "none",
            notes: [],
          }),
          { status: 200 },
        );
      }
      if (path === "/manuscripts") {
        // Reflects the newly-ingested manuscript once the picker refetches
        // (real invalidation behavior, not a hand-wired test double).
        return new Response(
          JSON.stringify({
            items: [
              {
                id: 99,
                group_label: "Ungrouped",
                original_filename: "thesis.pdf",
                ingest_status: "done",
                ingest_failure_reason: null,
                created_at: "2026-01-01T00:00:00Z",
                latest_check_run_id: null,
                latest_check_run_status: null,
                latest_done_check_run_id: null,
              },
            ],
            total: 1,
            page: 1,
            page_size: 200,
          }),
          { status: 200 },
        );
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<DashboardPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Upload manuscript" }));
    const dialog = await screen.findByRole("dialog");
    const file = new File(["dummy"], "thesis.pdf", { type: "application/pdf" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    // Scoped to the dialog: the header's own "Upload manuscript" CTA is
    // still in the DOM behind the modal and shares the same accessible
    // name as the modal's submit button.
    fireEvent.click(within(dialog).getByRole("button", { name: "Upload manuscript" }));

    await screen.findByText("Uploaded. 8 page(s) parsed, 3 citation(s) found.");
    fireEvent.click(screen.getByRole("button", { name: "Start a check with this manuscript" }));

    // Lands in New Check with the just-uploaded manuscript preselected,
    // no manual re-pick required (Flow B as one continuous task).
    const select = await screen.findByRole("combobox", { name: /manuscript/i });
    await waitFor(() => expect(select).toHaveValue("99"));
  });
});
