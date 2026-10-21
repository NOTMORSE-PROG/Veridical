import { focusManager } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { GroupProposalDialog } from "./GroupProposalDialog";

const saved = { manuscript_id: 7, confirmation: "not_recorded", group: {
  id: 42, name: "Saved memberless group", member_names: [], program_id: null, program: null, title: "Saved manuscript title",
} };
const source = { title: { value: "Source title", anchor: "p. 1" }, short_name: { value: "Source team", anchor: "p. 1" },
  members: [], program: null, adviser: null, extraction_failed: false };
function setup() {
  const fetch = stubFetchByPath({ "/programs": [], "/manuscripts/7/group": saved, "/manuscripts/7/group-proposal": source });
  vi.stubGlobal("fetch", fetch);
  const onClose = vi.fn();
  renderWithProviders(<GroupProposalDialog manuscriptId={7} onClose={onClose} onDone={() => {}} />);
  return { fetch, onClose };
}

describe("GroupProposalDialog", () => {
  afterEach(() => { vi.unstubAllGlobals(); focusManager.setFocused(undefined); });
  it("reads the actual saved memberless group without requesting source extraction", async () => {
    const { fetch } = setup();
    expect(await screen.findByText("Saved memberless group")).toBeInTheDocument();
    expect(screen.getByText("No members recorded.")).toBeInTheDocument();
    expect(screen.getByText("Saved manuscript title")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm assignment" })).not.toBeInTheDocument();
    expect(fetch.mock.calls.some(([url]) => String(url).includes("group-proposal"))).toBe(false);
  });
  it("opens an explicit current-record change form with unchanged confirmation disabled", async () => {
    setup();
    fireEvent.click(await screen.findByRole("button", { name: "Change group assignment" }));
    expect(screen.getByLabelText("Group name")).toHaveValue("Saved memberless group");
    expect(screen.getByRole("button", { name: "Confirm assignment" })).toBeDisabled();
    expect(screen.queryByText(/Not found on the title page/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Group name"), { target: { value: "Changed assignment" } });
    expect(screen.getByRole("button", { name: "Confirm assignment" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("Saved memberless group")).toBeInTheDocument();
  });
  it("keeps source proposal available through an explicit action after Skip", async () => {
    setup();
    fireEvent.click(await screen.findByRole("button", { name: "Change group assignment" }));
    fireEvent.click(screen.getByRole("button", { name: "Review title-page proposal" }));
    expect(await screen.findByLabelText("Group name")).toHaveValue("Source team");
    expect(screen.getByRole("dialog")).toHaveAccessibleName("Review proposed group");
    fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
    expect(screen.getByText("Saved memberless group")).toBeInTheDocument();
  });
  it("reports saved-record failure without silently falling back to source extraction", async () => {
    const fetch = vi.fn(async (_url: RequestInfo | URL) => new Response("not found", { status: 404 }));
    vi.stubGlobal("fetch", fetch);
    renderWithProviders(<GroupProposalDialog manuscriptId={7} onClose={() => {}} onDone={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load saved group details.");
    expect(fetch.mock.calls.every(([url]) => !String(url).includes("group-proposal"))).toBe(true);
  });
  it("keeps the outcome visible after the saved assignment cache refreshes", async () => {
    let assigned = false;
    let reads = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path === "/programs") return Response.json([]);
      if (path === "/manuscripts/7/group" && init?.method === "PATCH") {
        assigned = true;
        return Response.json({ group_id: 99, group_label: "New assignment", program: null, title: "Saved manuscript title", matched: false, outcome: "created" });
      }
      if (path === "/manuscripts/7/group") {
        reads += 1;
        return Response.json(assigned ? { ...saved, group: { ...saved.group, id: 99, name: "New assignment" } } : saved);
      }
      return Response.json({});
    }));
    renderWithProviders(<GroupProposalDialog manuscriptId={7} onClose={() => {}} onDone={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Change group assignment" }));
    fireEvent.change(screen.getByLabelText("Group name"), { target: { value: "New assignment" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm assignment" }));
    await waitFor(() => expect(reads).toBeGreaterThanOrEqual(2));
    expect(screen.getByRole("status")).toHaveTextContent("Created a new group: New assignment.");
    expect(screen.getByRole("button", { name: "Done" })).toBeInTheDocument();
  });
  it("keeps the edit's expected ID when a background refresh observes a different assignment", async () => {
    let externalChange = false;
    let reads = 0;
    let submitted: { expected_group_id: number } | undefined;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path === "/programs") return Response.json([]);
      if (path === "/manuscripts/7/group" && init?.method === "PATCH") {
        submitted = JSON.parse(String(init.body));
        return Response.json({ error: { code: "conflict", message: "Assignment changed. Reopen Group details." } }, { status: 409 });
      }
      if (path === "/manuscripts/7/group") {
        reads += 1;
        return Response.json(externalChange ? { ...saved, group: { ...saved.group, id: 99, name: "Other assignment" } } : saved);
      }
      return Response.json({});
    }));
    renderWithProviders(<GroupProposalDialog manuscriptId={7} onClose={() => {}} onDone={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Change group assignment" }));
    fireEvent.change(screen.getByLabelText("Group name"), { target: { value: "My intended change" } });
    externalChange = true;
    await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true); });
    await waitFor(() => expect(reads).toBeGreaterThanOrEqual(2));
    expect(screen.getByLabelText("Group name")).toHaveValue("My intended change");
    fireEvent.click(screen.getByRole("button", { name: "Confirm assignment" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Assignment changed. Reopen Group details.");
    expect(submitted?.expected_group_id).toBe(42);
  });
  it("closes through the dialog control and retains the existing modal contract", async () => {
    const { onClose } = setup();
    await screen.findByText("Saved memberless group");
    fireEvent.click(screen.getAllByRole("button", { name: "Close" })[0]);
    expect(onClose).toHaveBeenCalledOnce();
  });
});
