import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { NewCheckModal } from "./NewCheck";

const MANUSCRIPTS = {
  items: [
    {
      id: 1,
      group_label: "G-11",
      ingest_status: "done",
      created_at: "2026-01-01T00:00:00Z",
      latest_check_run_id: null,
      latest_check_run_status: null,
    },
    {
      id: 2,
      group_label: "G-12 (still processing)",
      ingest_status: "processing",
      created_at: "2026-01-02T00:00:00Z",
      latest_check_run_id: null,
      latest_check_run_status: null,
    },
  ],
  total: 2,
  page: 1,
  page_size: 200,
};

const ONE_ACTIVE_FAMILY = [
  {
    id: 9,
    rubric_family_id: "fam-1",
    version: 2,
    title: "TIP Format",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    criteria_count: 24,
    report_count: 0,
  },
];

describe("NewCheckModal", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("only lists ingested manuscripts", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/manuscripts": MANUSCRIPTS, "/rubric-families": ONE_ACTIVE_FAMILY }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} />);
    await screen.findByRole("option", { name: /^G-11,/ });
    expect(screen.queryByRole("option", { name: /G-12/ })).not.toBeInTheDocument();
  });

  it("BUG-022: two manuscripts sharing the default group_label list distinguishably via original_filename", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": {
          items: [
            {
              id: 3,
              group_label: "Ungrouped",
              original_filename: "Chapter1-3_FinalDefense.pdf",
              ingest_status: "done",
              created_at: "2026-01-01T00:00:00Z",
              latest_check_run_id: null,
              latest_check_run_status: null,
            },
            {
              id: 4,
              group_label: "Ungrouped",
              original_filename: "Chapter1-3_Revised.pdf",
              ingest_status: "done",
              created_at: "2026-01-01T00:00:00Z",
              latest_check_run_id: null,
              latest_check_run_status: null,
            },
          ],
          total: 2,
          page: 1,
          page_size: 200,
        },
        "/rubric-families": ONE_ACTIVE_FAMILY,
      }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} />);
    await screen.findByRole("option", { name: /^Chapter1-3_FinalDefense\.pdf,/ });
    expect(
      screen.getByRole("option", { name: /^Chapter1-3_Revised\.pdf,/ }),
    ).toBeInTheDocument();
  });

  it("shows a focused, accessible error summary when Start is clicked with nothing selected", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/manuscripts": MANUSCRIPTS, "/rubric-families": ONE_ACTIVE_FAMILY }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} />);
    await screen.findByRole("option", { name: /^G-11,/ });
    // Start check is never disabled for validation reasons (GOV.UK
    // pattern, matching 4d) — clicking it is what surfaces the problem.
    expect(screen.getByRole("button", { name: "Start check" })).not.toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Start check" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Choose a manuscript.");
    expect(alert).toHaveFocus();
  });

  it("auto-selects the single active rubric and shows its criteria count", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/manuscripts": MANUSCRIPTS, "/rubric-families": ONE_ACTIVE_FAMILY }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} />);
    expect(await screen.findByText(/24 criteria/)).toBeInTheDocument();
  });

  it("BUG-039: explains a missing active rubric immediately (not just after a Start-check click), focused for a keyboard user", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/manuscripts": MANUSCRIPTS, "/rubric-families": [] }));
    renderWithProviders(<NewCheckModal onClose={() => {}} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("No active rubric is available yet.");
    // Same wording as a later Start-check click would show — one
    // canonical message, not two different claims for the same state.
    expect(alert).not.toHaveTextContent("Confirm one on the rubric review screen first.");
    await waitFor(() => expect(document.activeElement).toBe(alert));
    expect(screen.getByRole("button", { name: "Start check" })).not.toBeDisabled();
  });

  it("BUG-040: a structural blocker with nowhere to jump to renders as plain text, not a false-affordance button", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/manuscripts": MANUSCRIPTS, "/rubric-families": [] }));
    renderWithProviders(<NewCheckModal onClose={() => {}} />);
    const alert = await screen.findByRole("alert");
    const item = within(alert).getByText("No active rubric is available yet.");
    expect(item.tagName).not.toBe("BUTTON");
  });

  it("V-059: 'no manuscripts' still renders as plain text when no upload handoff is wired (no caller regression)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/manuscripts": { items: [], total: 0, page: 1, page_size: 200 }, "/rubric-families": ONE_ACTIVE_FAMILY }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} />);
    const alert = await screen.findByRole("alert");
    const item = within(alert).getByText("No manuscripts are available to check yet.");
    expect(item.tagName).not.toBe("BUTTON");
  });

  it("V-059: 'no manuscripts' becomes a real action that hands off to upload when the caller wires one", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/manuscripts": { items: [], total: 0, page: 1, page_size: 200 }, "/rubric-families": ONE_ACTIVE_FAMILY }),
    );
    const onUploadManuscript = vi.fn();
    renderWithProviders(<NewCheckModal onClose={() => {}} onUploadManuscript={onUploadManuscript} />);
    const alert = await screen.findByRole("alert");
    const button = within(alert).getByRole("button", { name: "No manuscripts are available to check yet." });
    fireEvent.click(button);
    expect(onUploadManuscript).toHaveBeenCalledTimes(1);
  });

  it("V-059: preselects a just-uploaded manuscript once it actually appears in the loaded picker", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/manuscripts": MANUSCRIPTS, "/rubric-families": ONE_ACTIVE_FAMILY }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} initialManuscriptId={1} />);
    const select = await screen.findByRole("combobox", { name: /manuscript/i });
    await waitFor(() => expect(select).toHaveValue("1"));
    expect(screen.getByText(/^Selected:/)).toBeInTheDocument();
  });

  it("submits the selected manuscript and rubric on Start check", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(typeof input === "string" ? input : input.toString(), "http://localhost")
        .pathname;
      if (path === "/manuscripts") return new Response(JSON.stringify(MANUSCRIPTS), { status: 200 });
      if (path === "/rubric-families") {
        return new Response(JSON.stringify(ONE_ACTIVE_FAMILY), { status: 200 });
      }
      if (path === "/check-runs" && init?.method === "POST") {
        return new Response(JSON.stringify({ id: 42, status: "queued" }), { status: 201 });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<NewCheckModal onClose={() => {}} />);

    await screen.findByText(/24 criteria/);
    fireEvent.change(screen.getByRole("combobox", { name: /manuscript/i }), {
      target: { value: "1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start check" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/check-runs"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse((call?.[1]?.body as string) ?? "{}")).toEqual({
      manuscript_id: 1,
      rubric_id: 9,
    });
  });

  it("shows a real loading state for manuscripts, not a false empty state (Nielsen visibility of system status)", async () => {
    let resolveManuscripts: (value: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = new URL(typeof input === "string" ? input : input.toString(), "http://localhost").pathname;
        if (path === "/manuscripts") {
          return new Promise<Response>((resolve) => {
            resolveManuscripts = resolve;
          });
        }
        return new Response(JSON.stringify(ONE_ACTIVE_FAMILY), { status: 200 });
      }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} />);

    expect(await screen.findByText("Loading manuscripts.")).toBeInTheDocument();
    expect(screen.queryByText(/No manuscripts have been ingested/)).not.toBeInTheDocument();

    resolveManuscripts(new Response(JSON.stringify(MANUSCRIPTS), { status: 200 }));
    await screen.findByRole("option", { name: /^G-11,/ });
  });

  it("shows a real fetch-error state with a working retry, for manuscripts", async () => {
    let calls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(typeof input === "string" ? input : input.toString(), "http://localhost").pathname;
      if (path === "/manuscripts") {
        calls += 1;
        if (calls === 1) return new Response("Internal error", { status: 500 });
        return new Response(JSON.stringify(MANUSCRIPTS), { status: 200 });
      }
      return new Response(JSON.stringify(ONE_ACTIVE_FAMILY), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<NewCheckModal onClose={() => {}} />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load your manuscripts.");
    expect(calls).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("option", { name: /^G-11,/ });
  });

  it("distinguishes still-processing manuscripts from a genuinely empty list, honestly", async () => {
    const allPending = {
      ...MANUSCRIPTS,
      items: [{ ...MANUSCRIPTS.items[1], id: 3 }],
    };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/manuscripts": allPending, "/rubric-families": ONE_ACTIVE_FAMILY }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} />);

    expect(
      await screen.findByText("Your manuscripts are still being processed. This can take a few minutes. Check back shortly."),
    ).toBeInTheDocument();
  });

  const CS_MANUSCRIPT = {
    id: 1,
    group_label: "G-CS",
    program: "CS",
    ingest_status: "done",
    created_at: "2026-01-01T00:00:00Z",
    latest_check_run_id: null,
    latest_check_run_status: null,
  };
  const UNSET_PROGRAM_MANUSCRIPT = { ...CS_MANUSCRIPT, id: 2, group_label: "G-Unset", program: null };
  const CS_RUBRIC = { ...ONE_ACTIVE_FAMILY[0], id: 9, title: "CS Format", program: "CS" };
  const IT_RUBRIC = { ...ONE_ACTIVE_FAMILY[0], id: 10, title: "IT Format", program: "IT" };

  it("V-064 AC2: auto-selects the single ELIGIBLE rubric once a program-matching manuscript is chosen, not just the single active one overall", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": { items: [CS_MANUSCRIPT], total: 1, page: 1, page_size: 200 },
        "/rubric-families": [CS_RUBRIC, IT_RUBRIC],
      }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} initialManuscriptId={1} />);
    const select = await screen.findByRole("combobox", { name: /manuscript/i });
    await waitFor(() => expect(select).toHaveValue("1"));
    expect(await screen.findByText(/CS Format/)).toBeInTheDocument();
    expect(screen.queryByText(/IT Format/)).not.toBeInTheDocument();
  });

  it("V-064 AC4 (ux-critic finding): a PARTIAL exclusion (1 of 2 active rubrics eligible) is disclosed too, not just the zero-eligible case", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": { items: [CS_MANUSCRIPT], total: 1, page: 1, page_size: 200 },
        "/rubric-families": [CS_RUBRIC, IT_RUBRIC],
      }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} initialManuscriptId={1} />);
    const select = await screen.findByRole("combobox", { name: /manuscript/i });
    await waitFor(() => expect(select).toHaveValue("1"));
    // The picker itself never renders (only 1 of 2 is eligible) -- the
    // excluded one's existence must still be disclosed somewhere.
    expect(screen.queryByRole("combobox", { name: /rubric/i })).not.toBeInTheDocument();
    expect(
      await screen.findByText("1 other active rubric not shown: its program doesn't match this manuscript's (CS)."),
    ).toBeInTheDocument();
  });

  it("V-064 AC4: explains, not silently hides, when nothing active fits the manuscript's program", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": { items: [CS_MANUSCRIPT], total: 1, page: 1, page_size: 200 },
        "/rubric-families": [IT_RUBRIC],
      }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} initialManuscriptId={1} />);
    const select = await screen.findByRole("combobox", { name: /manuscript/i });
    await waitFor(() => expect(select).toHaveValue("1"));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("No active rubric is set up for this manuscript's program (CS)");
  });

  it("V-064 AC3: a manuscript with no program set is offered every active rubric, with a non-blocking hint", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": { items: [UNSET_PROGRAM_MANUSCRIPT], total: 1, page: 1, page_size: 200 },
        "/rubric-families": [CS_RUBRIC, IT_RUBRIC],
      }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} initialManuscriptId={2} />);
    const select = await screen.findByRole("combobox", { name: /manuscript/i });
    await waitFor(() => expect(select).toHaveValue("2"));
    expect(
      await screen.findByText("This manuscript's program is not set, so every active rubric is offered."),
    ).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /CS Format/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /IT Format/ })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("V-064 AC2: a rubric with no program set stays eligible for a program-set manuscript too", async () => {
    const unsetRubric = { ...ONE_ACTIVE_FAMILY[0], id: 11, title: "Any Program", program: null };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/manuscripts": { items: [CS_MANUSCRIPT], total: 1, page: 1, page_size: 200 },
        "/rubric-families": [unsetRubric],
      }),
    );
    renderWithProviders(<NewCheckModal onClose={() => {}} initialManuscriptId={1} />);
    const select = await screen.findByRole("combobox", { name: /manuscript/i });
    await waitFor(() => expect(select).toHaveValue("1"));
    expect(await screen.findByText(/Any Program/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
