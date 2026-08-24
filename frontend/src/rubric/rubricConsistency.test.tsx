// BUG-067: the rubric management screen (4m) and the New check modal (4f)
// must never disagree about how many active rubrics exist -- the original
// defect (found 2026-08-16, whole-product audit) was Manage.tsx implying
// exactly one active rubric while NewCheck.tsx offered a choice of two
// with no explanation. Both screens have long since been rebuilt (V-055
// gave Manage.tsx its own honest "found N formats... switching not
// available yet" disclosure; V-064 gave NewCheck.tsx a principled,
// disclosed program-eligibility explanation for why more than one is
// offered) and both derive their family list from the exact same query
// (`useRubricFamilies`, single `["rubric-families"]` key) -- this test
// pins that the two screens' own numbers actually reconcile against one
// shared fixture, not just that each screen's OWN test passes in
// isolation.
import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RubricListItem } from "../api/types";
import { NewCheckModal } from "../check/NewCheck";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { ManageRubricPage } from "./Manage";

const FAMILY_A_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const FAMILY_B_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";

const CS_RUBRIC: RubricListItem = {
  id: 1,
  rubric_family_id: FAMILY_A_ID,
  version: 1,
  title: "CS Format",
  is_active: true,
  created_at: "2026-06-01T00:00:00Z",
  criteria_count: 10,
  report_count: 0,
  program: "CS",
};

const IT_RUBRIC: RubricListItem = {
  id: 2,
  rubric_family_id: FAMILY_B_ID,
  version: 1,
  title: "IT Format",
  is_active: true,
  created_at: "2026-06-01T00:00:00Z",
  criteria_count: 8,
  report_count: 0,
  program: "IT",
};

const CS_MANUSCRIPT = {
  id: 5,
  group_label: "G-CS",
  ingest_status: "done",
  program: "CS",
  created_at: "2026-01-01T00:00:00Z",
  latest_check_run_id: null,
  latest_check_run_status: null,
};

describe("Manage.tsx / NewCheck.tsx rubric-count consistency (BUG-067)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("Manage's 'found 2' and NewCheck's 'shown + excluded' both account for the same 2 active families", async () => {
    // Same two-family fixture fed to both screens' identical
    // `/rubric-families` endpoint -- proves neither screen invents its
    // own count, both read the one real list.
    const families = [CS_RUBRIC, IT_RUBRIC];

    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/rubric-families": families,
        [`/rubric-families/${FAMILY_A_ID}/versions`]: [CS_RUBRIC],
      }),
    );
    const manage = renderWithProviders(<ManageRubricPage />);
    expect(
      await screen.findByText(/VERIDICAL found 2 required formats on your account/),
    ).toBeInTheDocument();
    manage.unmount();
    vi.unstubAllGlobals();

    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": { items: [CS_MANUSCRIPT], total: 1, page: 1, page_size: 200 },
        "/rubric-families": families,
      }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} initialManuscriptId={5} />);
    const select = await screen.findByRole("combobox", { name: /manuscript/i });
    await waitFor(() => expect(select).toHaveValue("5"));
    // Only CS Format is eligible for this CS manuscript -- auto-selected,
    // no dropdown (only 1 of 2 eligible), and the OTHER one is explicitly
    // disclosed as excluded, not silently dropped. 1 shown + 1 excluded
    // == the same 2 Manage.tsx reported finding.
    expect(await screen.findByText(/CS Format/)).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /rubric/i })).not.toBeInTheDocument();
    expect(
      await screen.findByText(
        "1 other active rubric not shown: its program doesn't match this manuscript's (CS).",
      ),
    ).toBeInTheDocument();
  });
});
