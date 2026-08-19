import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { GroupProposalDialog } from "./GroupProposalDialog";

describe("GroupProposalDialog", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("V-063 (AC6): re-derives and shows the same proposal a manuscript was ingested with", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/programs": [],
        "/manuscripts/7/group-proposal": {
          title: { value: "AI-Powered Capstone Advisor", anchor: "p. 1" },
          short_name: { value: "VERIDICAL", anchor: "p. 1" },
          members: [],
          program: null,
          adviser: null,
          extraction_failed: false,
        },
      }),
    );
    renderWithProviders(
      <GroupProposalDialog manuscriptId={7} onClose={() => {}} onDone={() => {}} />,
    );
    expect(await screen.findByLabelText("Group name")).toHaveValue("VERIDICAL");
  });

  it("shows an honest error state when the proposal can't be loaded", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("not found", { status: 404 })));
    renderWithProviders(
      <GroupProposalDialog manuscriptId={7} onClose={() => {}} onDone={() => {}} />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load this manuscript's group proposal.",
    );
  });

  it("closes via the dialog's own X control", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/programs": [],
        "/manuscripts/7/group-proposal": {
          title: null,
          short_name: null,
          members: [],
          program: null,
          adviser: null,
          extraction_failed: true,
        },
      }),
    );
    const onClose = vi.fn();
    renderWithProviders(<GroupProposalDialog manuscriptId={7} onClose={onClose} onDone={() => {}} />);
    await screen.findByText(/Couldn't read the title page/);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });
});
