import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useEffect, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../test/renderWithProviders";
import { CriterionEvidenceDialog } from "./CriterionEvidenceDialog";
import type { EvidenceReference } from "./evidenceTypes";

const observed = vi.hoisted(() => ({ mounted: vi.fn(), unmounted: vi.fn(), requests: vi.fn() }));
vi.mock("../document/RecordedEvidenceReader", () => ({ RecordedEvidenceReader: (props: { request: unknown; onManual: () => void }) => {
  const [page, setPage] = useState(7);
  observed.requests(props.request);
  useEffect(() => { observed.mounted(); return () => observed.unmounted(); }, []);
  return <section aria-label="Original manuscript"><p>Manual page {page}</p><button onClick={() => { props.onManual(); setPage(8); }}>Read next manuscript page</button></section>;
} }));

const first: EvidenceReference = { reference_id: "a".repeat(64), criterion_id: 24, flag_id: null,
  role: "criterion_evidence", index: 0, quote: "The first complete quotation remains connected to its source.",
  anchor: "page 7", source_scope: "own_manuscript", source_state: "not_checked" };
const second = { ...first, reference_id: "b".repeat(64), index: 1, anchor: "page 9", quote: "The complete second passage supplies additional recorded context." };
const request = { snapshotId: "c".repeat(64), reference: first, actionId: "source-opener" };

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); observed.mounted.mockClear(); observed.unmounted.mockClear(); observed.requests.mockClear(); });

function show() {
  return renderWithProviders(<CriterionEvidenceDialog run={9} request={request} context={{ title: "Purpose",
    explanation: "The full original assessment remains beside the manuscript.", explanationLabel: "Recorded assessment",
    recordedNote: "The original recorded limitation remains available.", references: [first, second] }} onClose={() => {}} />);
}

describe("source and explanation reading", () => {
  it("keeps manual reading and complete context when available desktop space changes", async () => {
    let available = 1200;
    const observers: ResizeObserverCallback[] = [];
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) { return this.classList.contains("signal-source-context") ? available : 0; });
    vi.spyOn(HTMLSpanElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 850 } as DOMRect);
    vi.stubGlobal("ResizeObserver", class {
      constructor(callback: ResizeObserverCallback) { observers.push(callback); }
      observe() {} disconnect() {} unobserve() {}
    });
    const view = show();
    const reader = await screen.findByRole("region", { name: "Original manuscript" });
    const layout = reader.closest(".signal-source-context");
    expect(layout).toHaveAttribute("data-layout", "split");
    fireEvent.click(within(reader).getByRole("button", { name: "Read next manuscript page" }));
    expect(screen.getByText("Manual page 8")).toBeVisible();
    act(() => { available = 600; for (const callback of observers) callback([], {} as ResizeObserver); });
    expect(layout).toHaveAttribute("data-layout", "stacked");
    expect(screen.getByText("Manual page 8")).toBeVisible();
    expect(screen.getByText(`“${first.quote}”`)).toBeVisible();
    expect(screen.getByText(`“${second.quote}”`)).toBeVisible();
    expect(screen.getByText("The full original assessment remains beside the manuscript.")).toBeVisible();
    expect(observed.mounted).toHaveBeenCalledTimes(1);
    expect(observed.unmounted).not.toHaveBeenCalled();
    expect(observed.requests).toHaveBeenLastCalledWith(request);
    view.unmount();
  });

  it("reloads the source reader without losing its saved reasoning or quotations", async () => {
    show();
    await screen.findByRole("region", { name: "Original manuscript" });
    fireEvent.click(screen.getByRole("button", { name: "Read next manuscript page" }));
    fireEvent.click(screen.getByRole("button", { name: "Reload manuscript" }));
    await waitFor(() => expect(observed.mounted).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Manual page 7")).toBeVisible();
    expect(screen.getByText(`“${second.quote}”`)).toBeVisible();
    expect(screen.getByText(/The original recorded limitation/)).toBeVisible();
    expect(observed.requests).toHaveBeenLastCalledWith(request);
  });
});
