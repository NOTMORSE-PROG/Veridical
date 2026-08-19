import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { GroupProposalFields } from "./GroupProposalFields";
import type { TitlePageProposal } from "../api/types";

const FULL_PROPOSAL: TitlePageProposal = {
  title: { value: "AI-Powered Capstone Advisor", anchor: "p. 1" },
  short_name: { value: "VERIDICAL", anchor: "p. 1" },
  members: [
    { value: "Juan Dela Cruz", anchor: "p. 1" },
    { value: "Maria Santos", anchor: "p. 1" },
  ],
  program: null,
  adviser: { value: "Prof. Reyes", anchor: "p. 1" },
  extraction_failed: false,
};

const EMPTY_PROPOSAL: TitlePageProposal = {
  title: null,
  short_name: null,
  members: [],
  program: null,
  adviser: null,
  extraction_failed: true,
};

describe("GroupProposalFields", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("seeds the form from the proposal and shows each extracted field's anchor", () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/programs": [] }));
    renderWithProviders(
      <GroupProposalFields manuscriptId={1} proposal={FULL_PROPOSAL} onDone={() => {}} />,
    );
    expect(screen.getByLabelText("Group name")).toHaveValue("VERIDICAL");
    expect(screen.getByDisplayValue("Juan Dela Cruz")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Maria Santos")).toBeInTheDocument();
    // p. 1 anchors: title, group name, both members, adviser.
    expect(screen.getAllByText("p. 1").length).toBeGreaterThanOrEqual(4);
  });

  it("V-063 edited-value rule: changing a field away from its extracted value replaces the anchor with an 'Edited' tag", () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/programs": [] }));
    renderWithProviders(
      <GroupProposalFields manuscriptId={1} proposal={FULL_PROPOSAL} onDone={() => {}} />,
    );
    fireEvent.change(screen.getByLabelText("Group name"), { target: { value: "VERIDICAL Team" } });
    expect(screen.getByText("Edited.")).toBeInTheDocument();
  });

  it("lets an instructor remove a proposed member and add a new one", () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/programs": [] }));
    renderWithProviders(
      <GroupProposalFields manuscriptId={1} proposal={FULL_PROPOSAL} onDone={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Remove Juan Dela Cruz/ }));
    expect(screen.queryByDisplayValue("Juan Dela Cruz")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add member" }));
    const memberInputs = screen.getAllByLabelText(/^Group member \d+ name$/);
    expect(memberInputs).toHaveLength(2); // Maria Santos + the new blank row
  });

  it("moves focus to the next remove button after removing a member, never stranding it at <body>", () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/programs": [] }));
    renderWithProviders(
      <GroupProposalFields manuscriptId={1} proposal={FULL_PROPOSAL} onDone={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Remove Juan Dela Cruz/ }));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /Remove Maria Santos/ }));
  });

  it("moves focus to 'Add member' after removing the last remaining member", () => {
    const oneMemberProposal: TitlePageProposal = { ...FULL_PROPOSAL, members: [FULL_PROPOSAL.members[0]] };
    vi.stubGlobal("fetch", stubFetchByPath({ "/programs": [] }));
    renderWithProviders(
      <GroupProposalFields manuscriptId={1} proposal={oneMemberProposal} onDone={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Remove Juan Dela Cruz/ }));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Add member" }));
  });

  it("extraction_failed shows a caution banner with fields pre-cleared for manual entry", () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/programs": [] }));
    renderWithProviders(
      <GroupProposalFields manuscriptId={1} proposal={EMPTY_PROPOSAL} onDone={() => {}} />,
    );
    expect(screen.getByText(/Couldn't read the title page/)).toBeInTheDocument();
    expect(screen.getByLabelText("Group name")).toHaveValue("");
    expect(screen.getByText("Not found on the title page. Enter it yourself.")).toBeInTheDocument();
  });

  it("blocks Confirm with an inline, associated error when the group name is empty", () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/programs": [] }));
    renderWithProviders(
      <GroupProposalFields manuscriptId={1} proposal={EMPTY_PROPOSAL} onDone={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm group" }));
    const input = screen.getByLabelText("Group name");
    expect(input).toHaveAttribute("aria-invalid", "true");
    const errorId = input.getAttribute("aria-describedby");
    expect(errorId).toBeTruthy();
    expect(document.getElementById(errorId!)).toHaveTextContent(
      "Enter a group name before confirming.",
    );
  });

  it("'Skip for now' calls onDone(null) without confirming anything", () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/programs": [] }));
    const onDone = vi.fn();
    renderWithProviders(
      <GroupProposalFields manuscriptId={1} proposal={FULL_PROPOSAL} onDone={onDone} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
    expect(onDone).toHaveBeenCalledWith(null);
  });

  it("Confirm PATCHes the edited proposal and shows a matched-group banner distinct from a created one", async () => {
    const patchBody: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(typeof input === "string" ? input : input.toString(), "http://localhost").pathname;
        if (path === "/programs") return new Response(JSON.stringify([]), { status: 200 });
        if (path === "/manuscripts/1/group" && init?.method === "PATCH") {
          patchBody.push(JSON.parse(init.body as string));
          return new Response(
            JSON.stringify({ group_id: 5, group_label: "VERIDICAL", program: null, matched: true }),
            { status: 200 },
          );
        }
        return new Response("not found", { status: 404 });
      }),
    );
    const onDone = vi.fn();
    renderWithProviders(
      <GroupProposalFields manuscriptId={1} proposal={FULL_PROPOSAL} onDone={onDone} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm group" }));

    expect(await screen.findByText(/Matched to an existing group/)).toBeInTheDocument();
    expect(patchBody[0]).toEqual({
      group_name: "VERIDICAL",
      member_names: ["Juan Dela Cruz", "Maria Santos"],
      program_id: null,
    });
    expect(onDone).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(onDone).toHaveBeenCalledWith(
      expect.objectContaining({ group_id: 5, group_label: "VERIDICAL", matched: true }),
    );
  });

  it("owner's call (2026-08-19): warns before confirming when the group name collides with an existing group and no member overlaps", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/programs": [],
        "/groups/collision-check": { collision: true, existing_group_name: "VERIDICAL" },
      }),
    );
    renderWithProviders(
      <GroupProposalFields manuscriptId={1} proposal={FULL_PROPOSAL} onDone={() => {}} />,
    );
    expect(
      await screen.findByText(/A group named "VERIDICAL" already exists/),
    ).toBeInTheDocument();
    // Proposes, never blocks -- Confirm group stays available.
    expect(screen.getByRole("button", { name: "Confirm group" })).not.toBeDisabled();
  });

  it("shows no collision warning when there's no collision", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/programs": [],
        "/groups/collision-check": { collision: false, existing_group_name: null },
      }),
    );
    renderWithProviders(
      <GroupProposalFields manuscriptId={1} proposal={FULL_PROPOSAL} onDone={() => {}} />,
    );
    await waitFor(() => expect(screen.queryByText(/already exists/)).not.toBeInTheDocument(), {
      timeout: 1000,
    });
  });

  it("keeps every edit intact and shows the server message when Confirm fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(typeof input === "string" ? input : input.toString(), "http://localhost").pathname;
        if (path === "/programs") return new Response(JSON.stringify([]), { status: 200 });
        if (path === "/manuscripts/1/group" && init?.method === "PATCH") {
          return new Response(
            JSON.stringify({ error: { code: "internal", message: "Could not save this group." } }),
            { status: 500 },
          );
        }
        return new Response("not found", { status: 404 });
      }),
    );
    renderWithProviders(
      <GroupProposalFields manuscriptId={1} proposal={FULL_PROPOSAL} onDone={() => {}} />,
    );
    fireEvent.change(screen.getByLabelText("Group name"), { target: { value: "VERIDICAL Team" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm group" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not save this group.");
    await waitFor(() => expect(screen.getByLabelText("Group name")).toHaveValue("VERIDICAL Team"));
  });
});
