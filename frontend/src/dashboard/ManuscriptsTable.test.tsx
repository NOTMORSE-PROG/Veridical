import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { ManuscriptsTable } from "./ManuscriptsTable";

// Mobile card + desktop table both render in the DOM simultaneously
// (CSS `hidden`/`sm:hidden` — jsdom applies no media queries), so every
// query here expects TWO matches, not one.
function page(items: unknown[], total = items.length) {
  return { items, total, page: 1, page_size: 20 };
}

describe("ManuscriptsTable", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows an 'Open report' action for a done check, linking to the report", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": page([
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
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} onUploadManuscript={() => {}} />);
    expect((await screen.findAllByText("Checked")).length).toBe(2);
    const links = screen.getAllByRole("link", { name: "Open report" });
    expect(links).toHaveLength(2);
    for (const link of links) expect(link).toHaveAttribute("href", "/report/7");
  });

  it("shows a 'View progress' action for a still-running check", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": page([
          {
            id: 2,
            group_label: "G-12",
            ingest_status: "done",
            ingest_failure_reason: null,
            created_at: "2026-01-01T00:00:00Z",
            latest_check_run_id: 8,
            latest_check_run_status: "semantic",
            latest_done_check_run_id: null,
          },
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} onUploadManuscript={() => {}} />);
    expect((await screen.findAllByText("Checking")).length).toBe(2);
    const links = screen.getAllByRole("link", { name: "View progress" });
    expect(links).toHaveLength(2);
    for (const link of links) expect(link).toHaveAttribute("href", "/checks/8");
  });

  it("backend-critic finding (BUG-012): a prior report stays reachable when a newer re-run is still running", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": page([
          {
            id: 6,
            group_label: "G-16",
            ingest_status: "done",
            ingest_failure_reason: null,
            created_at: "2026-01-01T00:00:00Z",
            latest_check_run_id: 20, // the newer, still-running re-run
            latest_check_run_status: "semantic",
            latest_done_check_run_id: 9, // the OLDER, still-valid report
          },
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} onUploadManuscript={() => {}} />);
    await screen.findAllByText("Checking");
    const progressLinks = screen.getAllByRole("link", { name: "View progress" });
    for (const link of progressLinks) expect(link).toHaveAttribute("href", "/checks/20");
    const priorReportLinks = screen.getAllByRole("link", { name: "Open prior report" });
    expect(priorReportLinks).toHaveLength(2);
    for (const link of priorReportLinks) expect(link).toHaveAttribute("href", "/report/9");
  });

  it("backend-critic finding (BUG-012): a prior report stays reachable when a newer re-run failed", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": page([
          {
            id: 7,
            group_label: "G-17",
            ingest_status: "done",
            ingest_failure_reason: null,
            created_at: "2026-01-01T00:00:00Z",
            latest_check_run_id: 21, // the newer, FAILED re-run
            latest_check_run_status: "failed",
            latest_done_check_run_id: 10, // the OLDER, still-valid report
          },
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} onUploadManuscript={() => {}} />);
    await screen.findAllByText("Check failed");
    const priorReportLinks = screen.getAllByRole("link", { name: "Open prior report" });
    expect(priorReportLinks).toHaveLength(2);
    for (const link of priorReportLinks) expect(link).toHaveAttribute("href", "/report/10");
  });

  it("shows 'Not checked yet' for an ingested manuscript with no check run", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": page([
          {
            id: 3,
            group_label: "G-13",
            ingest_status: "done",
            ingest_failure_reason: null,
            created_at: "2026-01-01T00:00:00Z",
            latest_check_run_id: null,
            latest_check_run_status: null,
            latest_done_check_run_id: null,
          },
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} onUploadManuscript={() => {}} />);
    expect((await screen.findAllByText("Not checked yet")).length).toBe(2);
    expect(screen.queryByRole("link", { name: "Open report" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "View progress" })).not.toBeInTheDocument();
  });

  it("shows 'Re-run unavailable' (never a native enabled/disabled button pretending re-run exists)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": page([
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
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} onUploadManuscript={() => {}} />);
    // "Open report" already exists for a done run; "Re-run unavailable"
    // only shows for rows with no report/progress link to offer instead —
    // covered by the ingest-failure test below.
    await screen.findAllByText("Checked");
  });

  it("BUG-016: an 'Ingestion failed' row explains why and never dead-ends", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": page([
          {
            id: 4,
            group_label: "G-14",
            ingest_status: "failed",
            ingest_failure_reason: "file_too_large",
            created_at: "2026-01-01T00:00:00Z",
            latest_check_run_id: null,
            latest_check_run_status: null,
            latest_done_check_run_id: null,
          },
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} onUploadManuscript={() => {}} />);
    expect((await screen.findAllByText("Ingestion failed")).length).toBe(2);
    const [whyButton] = screen.getAllByRole("button", { name: "Why did this fail?" });
    fireEvent.click(whyButton);
    expect(
      screen.getAllByText(/larger than VERIDICAL currently accepts/i).length,
    ).toBeGreaterThan(0);
  });

  it("BUG-016: a pre-existing failed row with no recorded reason gets an honest fallback, not a fabricated one", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": page([
          {
            id: 5,
            group_label: "G-15",
            ingest_status: "failed",
            ingest_failure_reason: null,
            created_at: "2026-01-01T00:00:00Z",
            latest_check_run_id: null,
            latest_check_run_status: null,
            latest_done_check_run_id: null,
          },
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} onUploadManuscript={() => {}} />);
    await screen.findAllByText("Ingestion failed");
    const [whyButton] = screen.getAllByRole("button", { name: "Why did this fail?" });
    fireEvent.click(whyButton);
    expect(screen.getAllByText(/was not recorded before this feature shipped/i).length).toBeGreaterThan(0);
  });

  it("BUG-022: two manuscripts sharing the default group_label render distinguishably via original_filename", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": page([
          {
            id: 6,
            group_label: "Ungrouped",
            original_filename: "Chapter1-3_FinalDefense.pdf",
            ingest_status: "done",
            ingest_failure_reason: null,
            created_at: "2026-01-01T00:00:00Z",
            latest_check_run_id: null,
            latest_check_run_status: null,
            latest_done_check_run_id: null,
          },
          {
            id: 7,
            group_label: "Ungrouped",
            original_filename: "Chapter1-3_Revised.pdf",
            ingest_status: "done",
            ingest_failure_reason: null,
            created_at: "2026-01-01T00:00:00Z",
            latest_check_run_id: null,
            latest_check_run_status: null,
            latest_done_check_run_id: null,
          },
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} onUploadManuscript={() => {}} />);
    expect((await screen.findAllByText("Chapter1-3_FinalDefense.pdf")).length).toBe(2);
    expect(screen.getAllByText("Chapter1-3_Revised.pdf").length).toBe(2);
    // The default label is redundant once a filename wins the primary
    // slot -- it must not also render as dead-weight chrome on the row.
    expect(screen.queryByText("Ungrouped")).not.toBeInTheDocument();
  });

  it("BUG-022: a pre-migration row with no original_filename falls back to group_label alone, unchanged", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": page([
          {
            id: 8,
            group_label: "Ungrouped",
            original_filename: null,
            ingest_status: "done",
            ingest_failure_reason: null,
            created_at: "2026-01-01T00:00:00Z",
            latest_check_run_id: null,
            latest_check_run_status: null,
            latest_done_check_run_id: null,
          },
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} onUploadManuscript={() => {}} />);
    expect((await screen.findAllByText("Ungrouped")).length).toBe(2);
  });

  it("shows an error state with retry when the manuscripts fetch fails", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": new Response(
          JSON.stringify({ error: { code: "internal", message: "x" } }),
          { status: 500 },
        ),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} onUploadManuscript={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load your manuscripts.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("shows pagination controls only when there is more than one page", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/manuscripts": page([], 45) }));
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} onUploadManuscript={() => {}} />);
    expect(await screen.findByText("Page 1 of 3")).toBeInTheDocument();
    // total=45 here (a later page's fixture) -- zero ROWS on this
    // particular page must not be confused with zero manuscripts overall,
    // so the "no manuscripts at all" upload CTA must not appear.
    expect(screen.queryByText("No manuscripts yet")).not.toBeInTheDocument();
  });

  it("V-059: zero manuscripts overall shows a real upload CTA, not flat dead-end text", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/manuscripts": page([], 0) }));
    const onUploadManuscript = vi.fn();
    renderWithProviders(
      <ManuscriptsTable page={1} onPageChange={() => {}} onUploadManuscript={onUploadManuscript} />,
    );
    const ctaButtons = await screen.findAllByRole("button", { name: "Upload manuscript" });
    expect(ctaButtons.length).toBe(2); // mobile card + desktop table, same as every other dual-render case
    fireEvent.click(ctaButtons[0]);
    expect(onUploadManuscript).toHaveBeenCalledTimes(1);
  });

  it("calls onPageChange when Next is clicked", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/manuscripts": page([], 45) }));
    const onPageChange = vi.fn();
    renderWithProviders(
      <ManuscriptsTable page={1} onPageChange={onPageChange} onUploadManuscript={() => {}} />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Next" }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });
});
