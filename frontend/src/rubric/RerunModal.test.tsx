import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ManuscriptListItem, RubricListItem } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { RerunModal } from "./RerunModal";

const ACTIVE_FAMILY: RubricListItem[] = [
  {
    id: 9,
    rubric_family_id: "fam-1",
    version: 2,
    title: "TIP Format",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    criteria_count: 12,
    report_count: 3,
  },
];

function manuscript(overrides: Partial<ManuscriptListItem> = {}): ManuscriptListItem {
  return {
    id: 1,
    group_label: "G-11",
    original_filename: null,
    ingest_status: "done",
    ingest_failure_reason: null,
    created_at: "2026-01-01T00:00:00Z",
    latest_check_run_id: 7,
    latest_check_run_status: "done",
    latest_done_check_run_id: 7,
    latest_decision: null,
    // Matches ACTIVE_FAMILY's own rubric_family_id by default -- tests
    // that need to exercise the cross-family exclusion override this
    // explicitly (see the dedicated test below).
    latest_done_rubric_family_id: "fam-1",
    escalations_awaiting_review: 0,
    program: null,
    ...overrides,
  };
}

function stubBase(overrides: Record<string, unknown> = {}) {
  return stubFetchByPath({
    "/manuscripts": { items: [manuscript()], total: 1, page: 1, page_size: 200 },
    "/rubric-families": ACTIVE_FAMILY,
    "/check-runs/rerun-estimate": { items: [], total_estimated_calls: 0, manuscripts_with_no_estimate: 0 },
    ...overrides,
  });
}

describe("RerunModal", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("selects every eligible manuscript by default", async () => {
    vi.stubGlobal("fetch", stubBase());
    renderWithProviders(<RerunModal onClose={() => {}} />);
    const checkbox = await screen.findByRole("checkbox", { name: /Select all/ });
    expect(checkbox).toBeChecked();
    expect(screen.getByRole("button", { name: "Re-run 1 manuscript" })).toBeEnabled();
  });

  it("deselecting a manuscript updates the confirm button count", async () => {
    vi.stubGlobal(
      "fetch",
      stubBase({
        "/manuscripts": {
          items: [manuscript({ id: 1 }), manuscript({ id: 2, group_label: "G-12", latest_check_run_id: 8, latest_done_check_run_id: 8 })],
          total: 2,
          page: 1,
          page_size: 200,
        },
      }),
    );
    renderWithProviders(<RerunModal onClose={() => {}} />);
    await screen.findByRole("button", { name: "Re-run 2 manuscripts" });
    // Each row is a real <label>, so its checkbox has an accessible name
    // built from the row's own identity text.
    fireEvent.click(screen.getByRole("checkbox", { name: /G-12/ }));
    expect(await screen.findByRole("button", { name: "Re-run 1 manuscript" })).toBeInTheDocument();
  });

  it("shows the honest empty state when nothing has ever been checked", async () => {
    vi.stubGlobal(
      "fetch",
      stubBase({
        "/manuscripts": { items: [], total: 0, page: 1, page_size: 200 },
      }),
    );
    renderWithProviders(<RerunModal onClose={() => {}} />);
    expect(
      await screen.findByText("No manuscripts have been checked yet, so there's nothing to re-run."),
    ).toBeInTheDocument();
  });

  it("excludes a manuscript with no completed run from the eligible set", async () => {
    vi.stubGlobal(
      "fetch",
      stubBase({
        "/manuscripts": {
          items: [manuscript({ id: 1, latest_done_check_run_id: null, latest_check_run_id: null, latest_check_run_status: null })],
          total: 1,
          page: 1,
          page_size: 200,
        },
      }),
    );
    renderWithProviders(<RerunModal onClose={() => {}} />);
    expect(
      await screen.findByText("No manuscripts have been checked yet, so there's nothing to re-run."),
    ).toBeInTheDocument();
  });

  it("ux-critic finding (P1): excludes a manuscript checked only under an UNRELATED rubric family, never defaulting it to selected", async () => {
    vi.stubGlobal(
      "fetch",
      stubBase({
        "/manuscripts": {
          items: [
            manuscript({ id: 1, group_label: "Same family" }),
            manuscript({ id: 2, group_label: "Different family", latest_done_rubric_family_id: "fam-2" }),
          ],
          total: 2,
          page: 1,
          page_size: 200,
        },
      }),
    );
    renderWithProviders(<RerunModal onClose={() => {}} />);
    await screen.findByText("Same family");
    expect(screen.queryByText("Different family")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Re-run 1 manuscript" })).toBeInTheDocument();
  });

  it("ux-critic finding (P1): distinguishes 'nothing checked under this format' from 'nothing checked at all'", async () => {
    vi.stubGlobal(
      "fetch",
      stubBase({
        "/manuscripts": {
          items: [manuscript({ id: 1, latest_done_rubric_family_id: "fam-2" })],
          total: 1,
          page: 1,
          page_size: 200,
        },
      }),
    );
    renderWithProviders(<RerunModal onClose={() => {}} />);
    expect(
      await screen.findByText(
        "No manuscripts have been checked against TIP Format yet, so there's nothing to re-run.",
      ),
    ).toBeInTheDocument();
  });

  it("ux-critic finding: discloses when a manuscript opened via a single row's Re-run action isn't eligible for the current family", async () => {
    vi.stubGlobal(
      "fetch",
      stubBase({
        "/manuscripts": {
          items: [
            manuscript({ id: 1, group_label: "Eligible one" }),
            manuscript({ id: 2, group_label: "Wrong family", latest_done_rubric_family_id: "fam-2" }),
          ],
          total: 2,
          page: 1,
          page_size: 200,
        },
      }),
    );
    renderWithProviders(<RerunModal onClose={() => {}} initialManuscriptIds={[2]} />);
    expect(
      await screen.findByText("The manuscript you selected wasn't checked against TIP Format, so it isn't included below."),
    ).toBeInTheDocument();
    // Nothing preselected, since the requested one was excluded.
    expect(screen.getByRole("button", { name: "Re-run 0 manuscripts" })).toBeInTheDocument();
  });

  it("V-071 (newcomer finding): the bulk-entry paragraph claims 'all manuscripts' are selected, and that's true here (opened with no initialManuscriptIds)", async () => {
    vi.stubGlobal(
      "fetch",
      stubBase({
        "/manuscripts": {
          items: [manuscript({ id: 1 }), manuscript({ id: 2, group_label: "G-12", latest_check_run_id: 8, latest_done_check_run_id: 8 })],
          total: 2,
          page: 1,
          page_size: 200,
        },
      }),
    );
    renderWithProviders(<RerunModal onClose={() => {}} />);
    expect(
      await screen.findByText(
        "All manuscripts previously checked against TIP Format are selected by default. Deselect any you don't want to re-run.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Only the manuscript/)).not.toBeInTheDocument();
  });

  it("V-071 (newcomer finding, live-reproduced): opened via a single row's Re-run action, the paragraph says ONLY that manuscript is preselected, not 'all manuscripts'", async () => {
    vi.stubGlobal(
      "fetch",
      stubBase({
        "/manuscripts": {
          items: [manuscript({ id: 1 }), manuscript({ id: 2, group_label: "G-12", latest_check_run_id: 8, latest_done_check_run_id: 8 })],
          total: 2,
          page: 1,
          page_size: 200,
        },
      }),
    );
    renderWithProviders(<RerunModal onClose={() => {}} initialManuscriptIds={[1]} />);
    expect(
      await screen.findByText(
        "Only the manuscript you chose is selected by default. Select others below if you want to re-run them too.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/All manuscripts previously checked/)).not.toBeInTheDocument();
    // The checkbox state backs up the sentence: only the requested row is checked.
    expect(screen.getByRole("button", { name: "Re-run 1 manuscript" })).toBeInTheDocument();
  });

  it("ux-critic finding (P2): the Select all checkbox goes indeterminate on a partial selection, not indistinguishable from none selected", async () => {
    vi.stubGlobal(
      "fetch",
      stubBase({
        "/manuscripts": {
          items: [manuscript({ id: 1 }), manuscript({ id: 2, group_label: "G-12" })],
          total: 2,
          page: 1,
          page_size: 200,
        },
      }),
    );
    renderWithProviders(<RerunModal onClose={() => {}} />);
    const selectAll = (await screen.findByRole("checkbox", { name: "Select all" })) as HTMLInputElement;
    expect(selectAll.indeterminate).toBe(false); // both selected initially

    fireEvent.click(screen.getByRole("checkbox", { name: "G-12" }));
    await waitFor(() => expect(selectAll.indeterminate).toBe(true));
    expect(selectAll).not.toBeChecked();
  });

  it("shows the honest quota estimate once resolved", async () => {
    vi.stubGlobal(
      "fetch",
      stubBase({
        "/check-runs/rerun-estimate": {
          items: [{ manuscript_id: 1, estimated_calls: 4 }],
          total_estimated_calls: 4,
          manuscripts_with_no_estimate: 0,
        },
      }),
    );
    renderWithProviders(<RerunModal onClose={() => {}} />);
    await screen.findByRole("button", { name: "Re-run 1 manuscript" });
    expect(await screen.findByText(/Estimated AI usage:/)).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
  });

  it("discloses manuscripts with no estimate rather than silently dropping them from the total", async () => {
    vi.stubGlobal(
      "fetch",
      stubBase({
        "/check-runs/rerun-estimate": {
          items: [{ manuscript_id: 1, estimated_calls: null }],
          total_estimated_calls: 0,
          manuscripts_with_no_estimate: 1,
        },
      }),
    );
    renderWithProviders(<RerunModal onClose={() => {}} />);
    await screen.findByRole("button", { name: "Re-run 1 manuscript" });
    expect(
      await screen.findByText(/No AI usage estimate is available for the 1 selected manuscript/),
    ).toBeInTheDocument();
  });

  it("submits sequentially, shows real per-row status, and never fires a fake aggregate percentage", async () => {
    const postedRubricIds: number[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(typeof input === "string" ? input : input.toString(), "http://localhost").pathname;
        if (path === "/manuscripts") {
          return new Response(JSON.stringify({ items: [manuscript()], total: 1, page: 1, page_size: 200 }), { status: 200 });
        }
        if (path === "/rubric-families") return new Response(JSON.stringify(ACTIVE_FAMILY), { status: 200 });
        if (path === "/check-runs/rerun-estimate") {
          return new Response(
            JSON.stringify({ items: [{ manuscript_id: 1, estimated_calls: 2 }], total_estimated_calls: 2, manuscripts_with_no_estimate: 0 }),
            { status: 200 },
          );
        }
        if (path === "/check-runs" && init?.method === "POST") {
          const body = JSON.parse(init.body as string) as { manuscript_id: number; rubric_id: number };
          postedRubricIds.push(body.rubric_id);
          return new Response(
            JSON.stringify({
              id: 99,
              manuscript_id: body.manuscript_id,
              rubric_id: body.rubric_id,
              status: "queued",
              stage_status: null,
              queue_position: 2,
              started_at: null,
              finished_at: null,
              created_at: "2026-01-01T00:00:00Z",
            }),
            { status: 201 },
          );
        }
        return new Response("not found", { status: 404 });
      }),
    );
    renderWithProviders(<RerunModal onClose={() => {}} />);
    const confirmButton = await screen.findByRole("button", { name: "Re-run 1 manuscript" });
    fireEvent.click(confirmButton);

    expect(await screen.findByText("Submitted")).toBeInTheDocument();
    expect(screen.getByText("Queue position 2.")).toBeInTheDocument();
    expect(postedRubricIds).toEqual([9]); // the active family's rubric id
    expect(screen.getByRole("button", { name: "Done" })).toBeInTheDocument();
  });

  it("a single failure never aborts the rest of the batch, and Retry failed only resubmits the failed ones", async () => {
    let manuscript2Attempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(typeof input === "string" ? input : input.toString(), "http://localhost").pathname;
        if (path === "/manuscripts") {
          return new Response(
            JSON.stringify({
              items: [manuscript({ id: 1 }), manuscript({ id: 2, group_label: "G-12", latest_check_run_id: 8, latest_done_check_run_id: 8 })],
              total: 2,
              page: 1,
              page_size: 200,
            }),
            { status: 200 },
          );
        }
        if (path === "/rubric-families") return new Response(JSON.stringify(ACTIVE_FAMILY), { status: 200 });
        if (path === "/check-runs/rerun-estimate") {
          return new Response(JSON.stringify({ items: [], total_estimated_calls: 0, manuscripts_with_no_estimate: 0 }), { status: 200 });
        }
        if (path === "/check-runs" && init?.method === "POST") {
          const body = JSON.parse(init.body as string) as { manuscript_id: number };
          if (body.manuscript_id === 2) {
            manuscript2Attempts++;
            if (manuscript2Attempts === 1) {
              return new Response(
                JSON.stringify({ error: { code: "conflict", message: "This manuscript hasn't finished ingestion yet." } }),
                { status: 409 },
              );
            }
          }
          return new Response(
            JSON.stringify({
              id: 100 + body.manuscript_id,
              manuscript_id: body.manuscript_id,
              rubric_id: 9,
              status: "queued",
              stage_status: null,
              queue_position: null,
              started_at: null,
              finished_at: null,
              created_at: "2026-01-01T00:00:00Z",
            }),
            { status: 201 },
          );
        }
        return new Response("not found", { status: 404 });
      }),
    );
    renderWithProviders(<RerunModal onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Re-run 2 manuscripts" }));

    await screen.findByText("This manuscript hasn't finished ingestion yet.");
    expect(await screen.findByText("Submitted")).toBeInTheDocument(); // manuscript 1 still succeeded
    const retryButton = screen.getByRole("button", { name: "Retry failed (1)" });
    fireEvent.click(retryButton);

    await waitFor(() => expect(screen.queryByRole("button", { name: /Retry failed/ })).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Done" })).toBeInTheDocument();
    expect(manuscript2Attempts).toBe(2);
  });

  it("preselects only the given manuscript when opened from a single row's Re-run action", async () => {
    vi.stubGlobal(
      "fetch",
      stubBase({
        "/manuscripts": {
          items: [manuscript({ id: 1 }), manuscript({ id: 2, group_label: "G-12", latest_check_run_id: 8, latest_done_check_run_id: 8 })],
          total: 2,
          page: 1,
          page_size: 200,
        },
      }),
    );
    renderWithProviders(<RerunModal onClose={() => {}} initialManuscriptIds={[2]} />);
    expect(await screen.findByRole("button", { name: "Re-run 1 manuscript" })).toBeInTheDocument();
  });

  it("shows a structural blocker, no action, when no active rubric exists", async () => {
    vi.stubGlobal("fetch", stubBase({ "/rubric-families": [] }));
    renderWithProviders(<RerunModal onClose={() => {}} />);
    expect(
      await screen.findByText(
        "No active rubric is available right now. Activate a format version before re-running manuscripts.",
      ),
    ).toBeInTheDocument();
  });

  it("shows a real fetch-error state with retry", async () => {
    vi.stubGlobal(
      "fetch",
      stubBase({ "/manuscripts": new Response("err", { status: 500 }) }),
    );
    renderWithProviders(<RerunModal onClose={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load your manuscripts.");
  });

  it("Cancel closes without submitting", async () => {
    const onClose = vi.fn();
    vi.stubGlobal("fetch", stubBase());
    renderWithProviders(<RerunModal onClose={onClose} />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
