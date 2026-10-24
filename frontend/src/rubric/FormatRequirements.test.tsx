import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RubricListItem } from "../api/types";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { FormatRequirements } from "./FormatRequirements";

const SELECTED: RubricListItem = { id: 9, rubric_family_id: "saved-family", version: 2, title: "Saved format",
  is_active: true, criteria_count: 1, created_at: "2026-10-24T01:02:03Z", program: null, report_count: 1 };
const receipt = (changes: Record<string, unknown> = {}) => ({ ...SELECTED,
  criteria: [{ id: 4, text: "Read the actual design evidence <not markup>.", type: "semantic", position: 0 }], ...changes });

describe("selected format requirements", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("loads only on request and keeps the full actual text safely rendered", async () => {
    const fetcher = stubFetchByPath({ "/rubrics/9": receipt() });
    vi.stubGlobal("fetch", fetcher);
    renderWithProviders(<FormatRequirements selected={SELECTED} onUnconfirmed={vi.fn()} />, { authInstructorId: 1 });
    expect(fetcher).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "View criteria" }));
    expect(await screen.findByText("Read the actual design evidence <not markup>.")).toBeVisible();
    expect(document.querySelector("not")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Hide criteria" }));
    expect(screen.queryByText("Read the actual design evidence <not markup>.")).not.toBeInTheDocument();
  });

  it.each([
    { id: 10 }, { rubric_family_id: "other-family" }, { version: 3 }, { is_active: false },
    { title: "Other format" }, { criteria: [] }, { criteria: [{ id: 4, text: "" }] },
    { criteria: [{ id: true, text: "PRIVATE-WRONG-REQUIREMENT" }] },
  ])("withholds unconfirmed or malformed requirements %j and asks the parent to refresh choices", async change => {
    const onUnconfirmed = vi.fn();
    vi.stubGlobal("fetch", stubFetchByPath({ "/rubrics/9": receipt(change) }));
    renderWithProviders(<FormatRequirements selected={SELECTED} onUnconfirmed={onUnconfirmed} />, { authInstructorId: 1 });
    fireEvent.click(screen.getByRole("button", { name: "View criteria" }));
    expect(await screen.findByText("This format could not be confirmed. Choose a current active format.")).toBeVisible();
    expect(screen.queryByText("PRIVATE-WRONG-REQUIREMENT")).not.toBeInTheDocument();
    await waitFor(() => expect(onUnconfirmed).toHaveBeenCalledTimes(1));
  });

  it("keeps a failed preview optional and recovers without focus falling to the page body", async () => {
    let attempts = 0;
    vi.stubGlobal("fetch", vi.fn(async () => ++attempts === 1
      ? new Response(JSON.stringify({ error: { code: "internal", message: "Read unavailable." } }), { status: 503 })
      : new Response(JSON.stringify(receipt()), { status: 200 })));
    renderWithProviders(<FormatRequirements selected={SELECTED} onUnconfirmed={vi.fn()} />, { authInstructorId: 1 });
    fireEvent.click(screen.getByRole("button", { name: "View criteria" }));
    const retry = await screen.findByRole("button", { name: "Retry requirements" });
    retry.focus();
    fireEvent.click(retry);
    expect(screen.getByRole("heading", { name: "Requirements in this format" })).toHaveFocus();
    expect(await screen.findByText("Read the actual design evidence <not markup>.")).toBeVisible();
  });

  it("rejects a completed private read after the authenticated reader changes", async () => {
    let client!: QueryClient;
    let finish!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { finish = resolve; })));
    function Probe() { client = useQueryClient(); return <FormatRequirements selected={SELECTED} onUnconfirmed={vi.fn()} />; }
    renderWithProviders(<Probe />, { authInstructorId: 1 });
    fireEvent.click(screen.getByRole("button", { name: "View criteria" }));
    await waitFor(() => expect(finish).toBeDefined());
    await act(async () => { client.setQueryData(ME_QUERY_KEY, { id: 2 }); });
    await act(async () => { finish(new Response(JSON.stringify(receipt()), { status: 200 })); });
    expect(await screen.findByText("Instructor access changed. Close this check and sign in again.")).toBeVisible();
    expect(screen.queryByText("Read the actual design evidence <not markup>.")).not.toBeInTheDocument();
  });

  it("aborts the private preview when its modal/version component retires", async () => {
    let signal: AbortSignal | null | undefined;
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => { signal = init?.signal; return new Promise<Response>(() => {}); }));
    const view = renderWithProviders(<FormatRequirements selected={SELECTED} onUnconfirmed={vi.fn()} />, { authInstructorId: 1 });
    fireEvent.click(screen.getByRole("button", { name: "View criteria" }));
    await waitFor(() => expect(signal).toBeDefined());
    view.unmount();
    expect(signal?.aborted).toBe(true);
  });

  it("reports an actually empty saved version without inventing requirements", async () => {
    const selected = { ...SELECTED, criteria_count: 0 };
    vi.stubGlobal("fetch", stubFetchByPath({ "/rubrics/9": receipt({ criteria: [] }) }));
    renderWithProviders(<FormatRequirements selected={selected} onUnconfirmed={vi.fn()} />, { authInstructorId: 1 });
    fireEvent.click(screen.getByRole("button", { name: "View criteria" }));
    expect(await screen.findByText("No criteria are recorded in this version.")).toBeVisible();
  });

  it("retires a loaded preview when the same active version's saved metadata changes", async () => {
    let calls = 0;
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(++calls === 1 ? receipt() : receipt({
      criteria: [{ id: 4, text: "Updated design requirement." }, { id: 5, text: "New limitations requirement." }],
    })), { status: 200 })));
    function RefreshControl() {
      const [selected, setSelected] = useState(SELECTED);
      return <><button onClick={() => setSelected({ ...SELECTED, criteria_count: 2 })}>Refresh saved metadata</button>
        <FormatRequirements selected={selected} onUnconfirmed={vi.fn()} /></>;
    }
    renderWithProviders(<RefreshControl />, { authInstructorId: 1 });
    fireEvent.click(screen.getByRole("button", { name: "View criteria" }));
    expect(await screen.findByText("Read the actual design evidence <not markup>.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Refresh saved metadata" }));
    expect(screen.queryByText("Read the actual design evidence <not markup>.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View criteria" })).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(screen.getByRole("button", { name: "View criteria" }));
    expect(await screen.findByText("Updated design requirement.")).toBeVisible();
    expect(screen.getByText("New limitations requirement.")).toBeVisible();
  });
});
