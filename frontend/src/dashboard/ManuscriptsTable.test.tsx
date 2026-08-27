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
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
    expect((await screen.findAllByText("Checked")).length).toBe(2);
    const links = screen.getAllByRole("link", { name: "Open report" });
    expect(links).toHaveLength(2);
    for (const link of links) expect(link).toHaveAttribute("href", "/report/7");
  });

  it("V-038 / ux-critic finding: a decided manuscript shows its decision instead of the generic 'Checked' pill", async () => {
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
            latest_decision: "approved",
          },
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
    expect((await screen.findAllByText("Approved for defense")).length).toBe(2);
    expect(screen.queryByText("Checked")).not.toBeInTheDocument();
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
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
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
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
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
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
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
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
    expect((await screen.findAllByText("Not checked yet")).length).toBe(2);
    expect(screen.queryByRole("link", { name: "Open report" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "View progress" })).not.toBeInTheDocument();
  });

  it("V-071 (BUG-055, second half): a still-ingesting row's desktop grid row exposes 4 cells, matching the header's 4 columnheaders (ARIA 1.2), even with nothing to act on yet", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": page([
          {
            id: 9,
            group_label: "G-19",
            ingest_status: "processing",
            ingest_failure_reason: null,
            created_at: "2026-01-01T00:00:00Z",
            latest_check_run_id: null,
            latest_check_run_status: null,
            latest_done_check_run_id: null,
          },
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
    await screen.findAllByText("Ingesting");
    const rows = screen.getAllByRole("row").filter((r) => r.textContent?.includes("G-19"));
    expect(rows).toHaveLength(1); // desktop grid only -- the mobile card isn't a role="row"
    expect(rows[0].children).toHaveLength(4);
  });

  it("V-071 (AC1): a manuscript with escalated criteria shows a badge naming the count, so the row itself says it needs review", async () => {
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
            escalations_awaiting_review: 2,
          },
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
    expect((await screen.findAllByText("2 escalations")).length).toBe(2); // mobile + desktop
  });

  it("V-071 (AC1): a manuscript with zero escalations shows no badge", async () => {
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
            escalations_awaiting_review: 0,
          },
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
    await screen.findAllByText("Checked");
    expect(screen.queryByText(/escalation/i)).not.toBeInTheDocument();
  });

  it("V-041: a done manuscript offers Re-run alongside Open report, and it fires onRerun with the manuscript id", async () => {
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
    const onRerun = vi.fn();
    renderWithProviders(
      <ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={onRerun} onStartCheck={() => {}} onSetGroup={() => {}} />,
    );
    await screen.findAllByText("Checked");
    const rerunButtons = screen.getAllByRole("button", { name: "Re-run" });
    expect(rerunButtons).toHaveLength(2); // mobile + desktop
    fireEvent.click(rerunButtons[0]);
    expect(onRerun).toHaveBeenCalledWith(1);
  });

  it("V-071 (BUG-055): a never-checked manuscript offers a 'Start check' action, firing onStartCheck with its id", async () => {
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
    const onStartCheck = vi.fn();
    renderWithProviders(
      <ManuscriptsTable
        page={1}
        onPageChange={() => {}}
        program={undefined}
        onProgramChange={() => {}}
        onUploadManuscript={() => {}}
        onRerun={() => {}}
        onStartCheck={onStartCheck}
        onSetGroup={() => {}}
      />,
    );
    await screen.findAllByText("Not checked yet");
    const startButtons = screen.getAllByRole("button", { name: "Start check" });
    expect(startButtons).toHaveLength(2); // mobile + desktop
    fireEvent.click(startButtons[0]);
    expect(onStartCheck).toHaveBeenCalledWith(3);
  });

  it("V-063 (AC6): a successfully-ingested row always offers 'Set group', firing onSetGroup with its id", async () => {
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
    const onSetGroup = vi.fn();
    renderWithProviders(
      <ManuscriptsTable
        page={1}
        onPageChange={() => {}}
        program={undefined}
        onProgramChange={() => {}}
        onUploadManuscript={() => {}}
        onRerun={() => {}}
        onStartCheck={() => {}}
        onSetGroup={onSetGroup}
      />,
    );
    const setGroupButtons = await screen.findAllByRole("button", { name: "Set group" });
    expect(setGroupButtons).toHaveLength(2); // mobile + desktop
    fireEvent.click(setGroupButtons[0]);
    expect(onSetGroup).toHaveBeenCalledWith(3);
  });

  it("V-041 / ui-designer finding: a never-checked manuscript offers no Re-run action (nothing to re-run, no false affordance)", async () => {
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
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
    await screen.findAllByText("Not checked yet");
    expect(screen.queryByRole("button", { name: "Re-run" })).not.toBeInTheDocument();
  });

  it("V-041: a manuscript whose latest run failed but has an earlier done run still offers Re-run", async () => {
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
            latest_check_run_id: 21,
            latest_check_run_status: "failed",
            latest_done_check_run_id: 10,
          },
        ]),
      }),
    );
    const onRerun = vi.fn();
    renderWithProviders(
      <ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={onRerun} onStartCheck={() => {}} onSetGroup={() => {}} />,
    );
    await screen.findAllByText("Check failed");
    const rerunButtons = screen.getAllByRole("button", { name: "Re-run" });
    expect(rerunButtons).toHaveLength(2);
    fireEvent.click(rerunButtons[0]);
    expect(onRerun).toHaveBeenCalledWith(7);
  });

  it("BUG-137: a manuscript whose very FIRST check run failed offers 'Start a fresh check', not a dead end", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": page([
          {
            id: 8,
            group_label: "G-18",
            ingest_status: "done",
            ingest_failure_reason: null,
            created_at: "2026-01-01T00:00:00Z",
            latest_check_run_id: 22,
            latest_check_run_status: "failed",
            latest_done_check_run_id: null,
          },
        ]),
      }),
    );
    const onStartCheck = vi.fn();
    renderWithProviders(
      <ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={onStartCheck} onSetGroup={() => {}} />,
    );
    await screen.findAllByText("Check failed");
    expect(screen.queryByRole("button", { name: "Re-run" })).not.toBeInTheDocument();
    const startButtons = screen.getAllByRole("button", { name: "Start a fresh check" });
    expect(startButtons).toHaveLength(2); // mobile + desktop
    fireEvent.click(startButtons[0]);
    expect(onStartCheck).toHaveBeenCalledWith(8);
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
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
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
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
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
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
    expect((await screen.findAllByText("Chapter1-3_FinalDefense.pdf")).length).toBe(2);
    expect(screen.getAllByText("Chapter1-3_Revised.pdf").length).toBe(2);
    // The default label is redundant once a filename wins the primary
    // slot -- it must not also render as dead-weight chrome on the row.
    expect(screen.queryByText("Ungrouped")).not.toBeInTheDocument();
  });

  it("BUG-069 item 8 (ux-critic finding, live-reproduced at 320px): the mobile card's identity spans truncate from the START, preserving a distinguishing trailing character", async () => {
    // Structural guard only -- jsdom has no real layout engine and can't
    // measure where an ellipsis actually lands (this file's own sibling
    // BUG-103 test makes the identical point). Two groups differing only
    // in their LAST character ("...Group A" / "...Group B") rendered as
    // the IDENTICAL truncated string live at 320px, because ordinary
    // end-truncation drops the tail first -- `direction: rtl` +
    // `text-align: left` is the fix (truncates from the start instead),
    // so this pins that the style is actually applied, not just that the
    // full (untruncated-in-jsdom) text happens to still differ.
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": page([
          {
            id: 9,
            group_label: "DOCX Viewer Test Group A",
            original_filename: "docx_test_a.docx",
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
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
    // Mobile card + desktop table both render in the DOM (this file's own
    // top comment) -- the fix is mobile-card-scoped (that's the layout
    // active at 320px, the reflow floor this bug was found at), so only
    // ONE of the two matches is expected to carry the style.
    const primaries = await screen.findAllByText("DOCX Viewer Test Group A");
    expect(primaries.some((el) => el.style.direction === "rtl" && el.style.textAlign === "left")).toBe(
      true,
    );
    const secondaries = screen.getAllByText("docx_test_a.docx");
    expect(
      secondaries.some((el) => el.style.direction === "rtl" && el.style.textAlign === "left"),
    ).toBe(true);
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
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
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
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load your manuscripts.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("shows pagination controls only when there is more than one page", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/manuscripts": page([], 45) }));
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />);
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
      <ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={onUploadManuscript} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />,
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
      <ManuscriptsTable page={1} onPageChange={onPageChange} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Next" }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it("V-062: renders a program filter sourced from GET /programs, sends the chosen value, and 'Not set' uses the reserved sentinel", async () => {
    const fetchMock = vi.fn(
      stubFetchByPath({
        "/manuscripts": page([]),
        "/programs": [
          { id: 1, name: "CS" },
          { id: 2, name: "IT" },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onProgramChange = vi.fn();
    renderWithProviders(
      <ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={onProgramChange} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />,
    );
    const select = await screen.findByLabelText("Filter by program");
    expect(screen.getByRole("option", { name: "CS" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "IT" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Not set" })).toHaveValue("__unset__");

    fireEvent.change(select, { target: { value: "CS" } });
    expect(onProgramChange).toHaveBeenCalledWith("CS");

    fireEvent.change(select, { target: { value: "" } });
    expect(onProgramChange).toHaveBeenCalledWith(undefined);
  });

  it("V-062: renders nothing when the programs list is empty -- no real values to filter between", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/manuscripts": page([]), "/programs": [] }),
    );
    renderWithProviders(
      <ManuscriptsTable page={1} onPageChange={() => {}} program={undefined} onProgramChange={() => {}} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />,
    );
    expect((await screen.findAllByText("No manuscripts yet")).length).toBe(2);
    expect(screen.queryByLabelText("Filter by program")).not.toBeInTheDocument();
  });

  it("V-062: an active filter with zero results shows a distinct 'no match' state, not the 'no manuscripts yet' CTA", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": page([]),
        "/programs": [{ id: 1, name: "CS" }],
      }),
    );
    const onProgramChange = vi.fn();
    renderWithProviders(
      <ManuscriptsTable page={1} onPageChange={() => {}} program="CS" onProgramChange={onProgramChange} onUploadManuscript={() => {}} onRerun={() => {}} onStartCheck={() => {}} onSetGroup={() => {}} />,
    );
    expect((await screen.findAllByText("No manuscripts match this filter.")).length).toBe(2);
    expect(screen.queryByText("No manuscripts yet")).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Clear filter" })[0]);
    expect(onProgramChange).toHaveBeenCalledWith(undefined);
    // ux-critic (V-062 review): "Clear filter" unmounts itself immediately
    // on click -- focus must land somewhere real (the filter select), not
    // fall back to <body>.
    expect(document.activeElement).toBe(screen.getByLabelText("Filter by program"));
  });
});
