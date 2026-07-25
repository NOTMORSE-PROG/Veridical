import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { ManuscriptsTable } from "./ManuscriptsTable";

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
            created_at: "2026-01-01T00:00:00Z",
            latest_check_run_id: 7,
            latest_check_run_status: "done",
          },
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} />);
    expect(await screen.findByText("Checked")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Open report" });
    expect(link).toHaveAttribute("href", "/report/7");
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
            created_at: "2026-01-01T00:00:00Z",
            latest_check_run_id: 8,
            latest_check_run_status: "semantic",
          },
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} />);
    expect(await screen.findByText("Checking")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "View progress" });
    expect(link).toHaveAttribute("href", "/checks/8");
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
            created_at: "2026-01-01T00:00:00Z",
            latest_check_run_id: null,
            latest_check_run_status: null,
          },
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} />);
    expect(await screen.findByText("Not checked yet")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open report" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "View progress" })).not.toBeInTheDocument();
  });

  it("always shows Re-run disabled (arrives in V-041)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": page([
          {
            id: 1,
            group_label: "G-11",
            ingest_status: "done",
            created_at: "2026-01-01T00:00:00Z",
            latest_check_run_id: 7,
            latest_check_run_status: "done",
          },
        ]),
      }),
    );
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} />);
    expect(await screen.findByRole("button", { name: "Re-run" })).toBeDisabled();
  });

  it("shows pagination controls only when there is more than one page", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/manuscripts": page([], 45) }));
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={() => {}} />);
    expect(await screen.findByText("No manuscripts uploaded yet.")).toBeInTheDocument();
    expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
  });

  it("calls onPageChange when Next is clicked", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/manuscripts": page([], 45) }));
    const onPageChange = vi.fn();
    renderWithProviders(<ManuscriptsTable page={1} onPageChange={onPageChange} />);
    fireEvent.click(await screen.findByRole("button", { name: "Next" }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });
});
