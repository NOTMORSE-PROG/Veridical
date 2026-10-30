import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../api/client";
import type { TitlePageProposal } from "../api/types";
import type { GroupSelectionInput, GroupSelectionOutput } from "../api/groupAssignmentOutcome";
import { renderWithProviders } from "../test/renderWithProviders";
import { GroupAssignmentFields } from "./GroupAssignmentFields";

const empty: TitlePageProposal = { title: null, short_name: null, members: [], program: null, adviser: null, extraction_failed: false };
const initial = { manuscript_id: 9, group: { id: 3, name: "Ungrouped", member_names: [], program_id: null, program: null, title: null }, confirmation: "not_recorded" };
const saved = { id: 7, name: "Practice team", member_names: ["Maria", "Ana"], program_id: 1, program: "IT", title: "Saved title", fingerprint: "a".repeat(64) };
const other = { ...saved, id: 8, program: "CS", member_names: [], fingerprint: "b".repeat(64) };
const outlets: HTMLElement[] = [];
function mount(proposal: TitlePageProposal = empty) {
  const outlet = document.createElement("div"); document.body.append(outlet); outlets.push(outlet);
  const onDone = vi.fn();
  const get = vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path === "/auth/session") return { instructor: { id: 4 } } as never;
    if (path === "/manuscripts/9/group") return structuredClone(initial) as never;
    if (path === "/groups") return [saved, other].map(({ id, name, program }) => ({ id, name, program })) as never;
    if (path === "/groups/7") return structuredClone(saved) as never;
    if (path === "/groups/8") return structuredClone(other) as never;
    if (path === "/programs") return [{ id: 1, name: "IT" }] as never;
    if (path.startsWith("/groups/collision-check")) return { collision: false, existing_group_name: null } as never;
    throw new Error("Unexpected GET: " + path);
  });
  const post = vi.spyOn(api, "post").mockImplementation(async (_path, body) => ({
    action: "select_saved_group", manuscript_id: 9, outcome: "selected", receipt: {
      version: 2, manuscript_id: 9, instructor_id: 4, assigned_group_id: 7, request: body as GroupSelectionInput, effective: saved,
    },
  }) satisfies GroupSelectionOutput as never);
  const patch = vi.spyOn(api, "patch").mockResolvedValue({ group_id: 10, group_label: "New team", program: null, title: null, matched: false, outcome: "created" });
  renderWithProviders(<GroupAssignmentFields manuscriptId={9} initialProposal={proposal} actionsTarget={outlet} onDone={onDone} />, { uploadInstructorId: 4 });
  return { outlet, onDone, get, post, patch };
}
async function chooseSaved() {
  const intent = await screen.findByRole("radio", { name: "Use a saved group" });
  await waitFor(() => expect(intent).toBeEnabled()); fireEvent.click(intent);
  const radio = await screen.findByRole("radio", { name: /Practice team IT · Group #7/ });
  fireEvent.click(radio);
  await screen.findByText("Saved title", { selector: "dd" });
}
afterEach(() => { vi.restoreAllMocks(); for (const element of outlets.splice(0)) element.remove(); });

describe("explicit group assignment", () => {
  it("offers both unselected intents even when extraction found no group details", async () => {
    mount();
    const savedChoice = await screen.findByRole("radio", { name: "Use a saved group" });
    await waitFor(() => expect(savedChoice).toBeEnabled());
    expect(savedChoice).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "Review or enter group details" })).not.toBeChecked();
    expect(screen.queryByRole("button", { name: "Confirm assignment" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Assign to selected group" })).not.toBeInTheDocument();
  });
  it("shows duplicate names with program and ID, requires explicit choice and retains selection across search", async () => {
    const control = mount();
    await chooseSaved();
    const summary = screen.getByRole("region", { name: "Selected saved group" });
    expect(within(summary).getByText("Maria")).toBeInTheDocument();
    expect(within(summary).getByText("Ana")).toBeInTheDocument();
    expect(within(summary).getByText("IT")).toBeInTheDocument();
    expect(within(summary).getByText("Saved title", { selector: "dd" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search saved groups" }), { target: { value: "No matching team" } });
    expect(screen.getByText("No saved groups match this search.")).toBeInTheDocument();
    expect(summary).toBeVisible();
    expect(screen.getByRole("button", { name: "Assign to selected group" })).toBeEnabled();
    expect(control.post).not.toHaveBeenCalled();
    expect(control.patch).not.toHaveBeenCalled();
  });
  it("sends saved identity and real current FK without proposal overrides", async () => {
    const control = mount(); await chooseSaved();
    fireEvent.click(screen.getByRole("button", { name: "Assign to selected group" }));
    await screen.findByRole("heading", { name: "Assigned to saved group: Practice team" });
    expect(control.post).toHaveBeenCalledExactlyOnceWith("/manuscripts/9/group-selection", { saved_group_id: 7, fingerprint: saved.fingerprint, expected_group_id: 3 });
    expect(control.patch).not.toHaveBeenCalled();
    expect(control.onDone).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(control.onDone).toHaveBeenCalledTimes(1);
  });
  it("preserves exact manual draft and only one portaled action set across intent changes and session verification", async () => {
    const control = mount({ ...empty, short_name: { value: "Source name", anchor: "paragraph 1" } });
    const intent = await screen.findByRole("radio", { name: "Review or enter group details" });
    await waitFor(() => expect(intent).toBeEnabled()); fireEvent.click(intent);
    const input = screen.getByRole("textbox", { name: "Group name" });
    fireEvent.change(input, { target: { value: "  My corrected team  " } });
    fireEvent.click(screen.getByRole("button", { name: "Add member" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Group member 1 name" }), { target: { value: "  Maria Santos  " } });
    fireEvent.click(screen.getByRole("radio", { name: "Use a saved group" }));
    expect(within(control.outlet).queryByRole("button", { name: "Confirm assignment" })).not.toBeInTheDocument();
    expect(control.outlet.querySelectorAll(".signal-group-proposal__actions")).toHaveLength(1);
    fireEvent.click(screen.getByRole("radio", { name: "Review or enter group details" }));
    expect(screen.getByRole("textbox", { name: "Group name" })).toBe(input);
    expect(input).toHaveValue("  My corrected team  ");
    expect(screen.getByRole("textbox", { name: "Group member 1 name" })).toHaveValue("  Maria Santos  ");
    await act(async () => { window.dispatchEvent(new Event("focus")); });
    await waitFor(() => expect(input).toBeVisible());
    expect(input).toBe(screen.getByRole("textbox", { name: "Group name" }));
    expect(input).toHaveValue("  My corrected team  ");
    expect(control.outlet.querySelectorAll(".signal-group-proposal__actions")).toHaveLength(1);
  });
  it("marks a refused member name at its field and says so once, without calling it unconfirmed", async () => {
    const control = mount();
    control.patch.mockRejectedValue(new ApiError(422, "validation_error", "Member name is too long.", ["member_names.0"], new Map([["member_names.0", "Member name is too long."]])));
    const intent = await screen.findByRole("radio", { name: "Review or enter group details" });
    await waitFor(() => expect(intent).toBeEnabled()); fireEvent.click(intent);
    fireEvent.change(screen.getByRole("textbox", { name: "Group name" }), { target: { value: "New team" } });
    fireEvent.click(screen.getByRole("button", { name: "Add member" }));
    fireEvent.click(screen.getByRole("button", { name: "Add member" }));
    // The first row stays blank and is not sent; the refusal is for the second.
    const member = screen.getByRole("textbox", { name: "Group member 2 name" });
    fireEvent.change(member, { target: { value: "A very long name" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm assignment" }));
    await screen.findByRole("heading", { name: "Assignment was not saved" });
    expect(screen.getByText("Some names were not accepted. Each one is marked below. Nothing was saved.")).toBeInTheDocument();
    expect(member).toHaveAttribute("aria-invalid", "true");
    expect(member).toHaveAccessibleDescription("Member name is too long. Nothing was changed.");
    expect(member).toHaveFocus();
    expect(screen.getByRole("textbox", { name: "Group member 1 name" })).not.toHaveAttribute("aria-invalid");
    expect(screen.queryByRole("heading", { name: "Assignment result unconfirmed" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });
  it("never tells the instructor to check their entry when the request itself was refused", async () => {
    const control = mount();
    control.patch.mockRejectedValue(new ApiError(422, "validation_error", "Some of the information sent was not accepted. Check what you entered and try again.", ["expected_group_id"], new Map([["expected_group_id", "Some of the information sent was not accepted. Check what you entered and try again."]])));
    const intent = await screen.findByRole("radio", { name: "Review or enter group details" });
    await waitFor(() => expect(intent).toBeEnabled()); fireEvent.click(intent);
    fireEvent.change(screen.getByRole("textbox", { name: "Group name" }), { target: { value: "New team" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm assignment" }));
    await screen.findByRole("heading", { name: "Assignment was not saved" });
    expect(screen.getByText("The group record may have changed. Close this form, open it again and try again. Nothing was saved.")).toBeInTheDocument();
    expect(screen.queryByText(/Check what you entered/)).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Group name" })).not.toHaveAttribute("aria-invalid");
  });
  it("freezes an unknown proposal result, reads current facts and returns to review without a retry or success callback", async () => {
    const control = mount();
    control.patch.mockRejectedValue(new Error("Connection lost after commit"));
    const intent = await screen.findByRole("radio", { name: "Review or enter group details" });
    await waitFor(() => expect(intent).toBeEnabled()); fireEvent.click(intent);
    fireEvent.change(screen.getByRole("textbox", { name: "Group name" }), { target: { value: "New team" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm assignment" }));
    await screen.findByRole("heading", { name: "Assignment result unconfirmed" });
    expect(screen.queryByRole("button", { name: "Confirm assignment" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Check saved assignment" }));
    await screen.findByRole("heading", { name: "Saved assignment now" });
    expect(control.patch).toHaveBeenCalledTimes(1);
    expect(control.onDone).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Review another assignment" }));
    fireEvent.click(screen.getByRole("radio", { name: "Review or enter group details" }));
    expect(screen.getByRole("textbox", { name: "Group name" })).toHaveValue("New team");
    expect(control.patch).toHaveBeenCalledTimes(1);
  });
  it("retains selected identity after a known stale-summary refusal and requires a reviewed refresh", async () => {
    const control = mount();
    control.post.mockRejectedValueOnce(new ApiError(409, "saved_group_changed", "This saved group's details changed."));
    await chooseSaved();
    fireEvent.click(screen.getByRole("button", { name: "Assign to selected group" }));
    await screen.findByRole("heading", { name: "Assignment was not saved" });
    expect(screen.getByRole("region", { name: "Selected saved group" })).toBeVisible();
    expect(control.post).toHaveBeenCalledTimes(1);
    expect(control.onDone).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("radio", { name: "Use a saved group" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "Assign to selected group" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Assign to selected group" }));
    expect(control.post).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Refresh selected group details" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Assign to selected group" })).toBeEnabled());
    expect(control.post).toHaveBeenCalledTimes(1);
  });
  it("a changed manuscript assignment requires current-assignment review, not a selected-team refresh", async () => {
    const control = mount();
    const originalRead = control.get.getMockImplementation()!;
    let changed = false;
    control.get.mockImplementation(async (path, options) => {
      if (path === "/manuscripts/9/group" && changed) return {
        ...initial, group: { ...initial.group, id: 99, name: "Current other team" },
      } as never;
      return originalRead(path, options);
    });
    control.post.mockRejectedValueOnce(new ApiError(409, "group_assignment_changed", "This manuscript's assignment changed."));
    await chooseSaved();
    fireEvent.click(screen.getByRole("button", { name: "Assign to selected group" }));
    await screen.findByRole("heading", { name: "Assignment was not saved" });
    await waitFor(() => expect(screen.getByRole("radio", { name: "Use a saved group" })).toBeEnabled());
    changed = true;
    fireEvent.click(screen.getByRole("button", { name: "Refresh selected group details" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Refresh selected group details" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "Assign to selected group" })).toBeDisabled();
    expect(control.post).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Check current assignment" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Assign to selected group" })).toBeEnabled());
    expect(screen.getByText("Current other team")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Assign to selected group" }));
    await screen.findByRole("heading", { name: "Assigned to saved group: Practice team" });
    expect(control.post).toHaveBeenCalledTimes(2);
    expect(control.post.mock.calls[1]?.[1]).toMatchObject({ expected_group_id: 99 });
  });
});
